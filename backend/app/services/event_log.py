"""Persistent event and audit log service."""

from __future__ import annotations

import json
from typing import Any

from app.database import Database, utc_now_iso
from app.models.schemas import EventLogEntry, EventLogPage
from app.services.ws_hub import WebSocketHub


class EventLogService:
    """Writes structured audit events to SQLite and pushes live updates."""

    def __init__(self, db: Database, ws_hub: WebSocketHub | None = None) -> None:
        self.db = db
        self.ws_hub = ws_hub

    async def record(
        self,
        category: str,
        severity: str,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> EventLogEntry:
        now = utc_now_iso()
        details_json = json.dumps(details) if details is not None else None
        row_id = await self.db.execute(
            """
            INSERT INTO event_logs (timestamp, category, severity, event_type, message, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now, category, severity, event_type, message, details_json),
        )
        entry = EventLogEntry(
            id=row_id,
            timestamp=now,
            category=category,
            severity=severity,  # type: ignore[arg-type]
            event_type=event_type,
            message=message,
            details=details,
        )
        if self.ws_hub is not None:
            await self.ws_hub.broadcast("event", entry.model_dump())
        return entry

    async def query(
        self,
        category: str | None = None,
        severity: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EventLogPage:
        clauses: list[str] = []
        params: list[Any] = []

        if category and category != "all":
            clauses.append("category = ?")
            params.append(category)
        if severity and severity != "all":
            clauses.append("severity = ?")
            params.append(severity)
        if search:
            clauses.append("(message LIKE ? OR event_type LIKE ? OR COALESCE(details_json, '') LIKE ?)")
            term = f"%{search}%"
            params.extend([term, term, term])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        count_row = await self.db.fetch_one(
            f"SELECT COUNT(*) AS cnt FROM event_logs {where}",
            tuple(params),
        )
        total = int(count_row["cnt"]) if count_row else 0

        rows = await self.db.fetch_all(
            f"""
            SELECT id, timestamp, category, severity, event_type, message, details_json
            FROM event_logs
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            tuple([*params, limit, offset]),
        )

        items = [
            EventLogEntry(
                id=r["id"],
                timestamp=r["timestamp"],
                category=r["category"],
                severity=r["severity"],
                event_type=r["event_type"],
                message=r["message"],
                details=json.loads(r["details_json"]) if r["details_json"] else None,
            )
            for r in rows
        ]
        return EventLogPage(items=items, total=total, limit=limit, offset=offset)

    async def clear(self) -> int:
        cur = await self.db.conn.execute("DELETE FROM event_logs")
        await self.db.conn.commit()
        return cur.rowcount or 0
