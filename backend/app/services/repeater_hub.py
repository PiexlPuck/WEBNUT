"""Main WebNUT Repeater Hub: pairing tokens, authenticated WS tunnel, telemetry merge & RPC."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from app.config import settings
from app.database import Database, utc_now_iso
from app.models.schemas import (
    RepeaterJoinRequest,
    RepeaterJoinResponse,
    RepeaterJoinTokenCreateResponse,
    RepeaterJoinTokenView,
    RepeaterNodeUpdateRequest,
    RepeaterNodeView,
)
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client_config import detect_local_lan_ip
from app.services.event_log import EventLogService
from app.services.ws_hub import WebSocketHub

logger = logging.getLogger(__name__)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def slugify_node_base(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return (cleaned or "node")[:24]


def namespace_ups_name(node_slug: str, raw_ups_name: str) -> str:
    slug = slugify_node_base(node_slug)
    raw = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_ups_name.strip()).strip("_") or "ups"
    combined = f"{slug}_{raw}"
    return combined[:64]


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _build_offline_ups_dict(raw_ups: dict[str, Any]) -> dict[str, Any]:
    copy = dict(raw_ups)
    copy["node_online"] = False
    copy["status"] = "OFFLINE NOCOMM"
    copy["status_tokens"] = ["OFFLINE", "NOCOMM"]
    raw_vars = dict(copy.get("raw_vars") or {})
    raw_vars["ups.status"] = "NOCOMM"
    copy["raw_vars"] = raw_vars
    return copy


@dataclass(slots=True)
class NodeSession:
    node_id: int
    name: str
    slug: str
    websocket: WebSocket
    remote_ip: str
    connected_at: str
    last_heartbeat_mono: float
    latency_ms: float | None = None
    live_ups: list[dict[str, Any]] = field(default_factory=list)
    pending_rpcs: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RepeaterHub:
    """Manages paired Repeater nodes on Main WebNUT, WS telemetry ingestion, and remote RPC."""

    def __init__(
        self,
        db: Database,
        event_log: EventLogService,
        dispatcher: NotificationDispatcher,
        ws_hub: WebSocketHub | None = None,
        heartbeat_timeout_sec: int = 30,
        on_telemetry_change: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.db = db
        self.event_log = event_log
        self.dispatcher = dispatcher
        self.ws_hub = ws_hub
        self.heartbeat_timeout_sec = max(10, int(heartbeat_timeout_sec))
        self._on_telemetry_change = on_telemetry_change

        self._sessions: dict[int, NodeSession] = {}
        self._nodes_cache: list[dict[str, Any]] = []
        self._ups_index: dict[str, tuple[int, str, str]] = {}
        self._rpc_seq = 0
        self._watchdog_task: asyncio.Task[None] | None = None
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        self._stop_event = asyncio.Event()

    def set_on_telemetry_change(
        self, callback: Callable[[], Awaitable[None]] | None
    ) -> None:
        self._on_telemetry_change = callback

    async def refresh_cache(self) -> None:
        """Reload node metadata from SQLite into memory for fast synchronous lookups."""
        rows = await self.db.fetch_all("SELECT * FROM repeater_nodes ORDER BY id ASC")
        self._nodes_cache = rows
        self._rebuild_ups_index()

    def _rebuild_ups_index(self) -> None:
        index: dict[str, tuple[int, str, str]] = {}
        for row in self._nodes_cache:
            if not bool(row.get("enabled", 1)):
                continue
            nid = int(row["id"])
            nname = str(row["name"])
            slug = str(row["slug"])
            session = self._sessions.get(nid)
            if session is not None:
                ups_items = session.live_ups
            else:
                try:
                    ups_items = json.loads(row.get("last_ups_json") or "[]")
                except json.JSONDecodeError:
                    ups_items = []
            for u in ups_items:
                namespaced = str(u.get("name") or "").strip()
                raw_name = str(u.get("remote_ups_name") or "").strip()
                if not raw_name and namespaced.startswith(f"{slug}_"):
                    raw_name = namespaced[len(slug) + 1 :]
                if namespaced and raw_name:
                    index[namespaced] = (nid, nname, raw_name)
        self._ups_index = index

    def resolve_remote_ups(self, ups_name: str) -> tuple[int, str, str] | None:
        """Return `(node_id, node_name, raw_ups_name)` if `ups_name` belongs to a Repeater node."""
        return self._ups_index.get(ups_name)

    def get_merged_ups_list(self) -> list[dict[str, Any]]:
        """Return namespaced UPS telemetry dicts for all enabled Repeater nodes."""
        merged: list[dict[str, Any]] = []
        for row in self._nodes_cache:
            if not bool(row.get("enabled", 1)):
                continue
            nid = int(row["id"])
            session = self._sessions.get(nid)
            if session is not None:
                for u in session.live_ups:
                    merged.append(dict(u))
                continue

            try:
                cached = json.loads(row.get("last_ups_json") or "[]")
            except json.JSONDecodeError:
                cached = []
            for u in cached:
                if isinstance(u, dict) and u.get("name"):
                    merged.append(_build_offline_ups_dict(u))
        return merged

    async def _notify_telemetry_change(self) -> None:
        if self._on_telemetry_change is not None:
            try:
                await self._on_telemetry_change()
            except Exception as exc:
                logger.exception("RepeaterHub telemetry change callback failed: %s", exc)

    async def _broadcast_nodes(self) -> None:
        if self.ws_hub is not None:
            nodes = await self.list_nodes()
            await self.ws_hub.broadcast("repeaters", [n.model_dump() for n in nodes])

    # --- Join Token Management ---

    def _build_compose_snippet(self, main_url: str, token: str, label: str = "") -> str:
        node_name = label.strip() or "remote-ups"
        return (
            "services:\n"
            "  webnut-repeater:\n"
            "    image: ghcr.io/piexlpuck/webnut:latest\n"
            "    # Or build locally from the repo:\n"
            "    # build: .\n"
            "    container_name: webnut-repeater\n"
            "    restart: unless-stopped\n"
            "    network_mode: host\n"
            "    cap_add:\n"
            "      - NET_ADMIN\n"
            "      - NET_RAW\n"
            "    devices:\n"
            "      - /dev/bus/usb:/dev/bus/usb\n"
            "    environment:\n"
            "      - TZ=UTC\n"
            "      - WEBNUT_ROLE=repeater\n"
            "      - WEB_PORT=8765\n"
            "      - WEBNUT_INTERNAL_NUT_PORT=3494\n"
            f"      - WEBNUT_MAIN_URL={main_url}\n"
            f"      - WEBNUT_JOIN_TOKEN={token}\n"
            f"      - WEBNUT_NODE_NAME={node_name}\n"
            "    volumes:\n"
            "      - ./data/repeater-nut:/etc/nut\n"
            "      - ./data/repeater-app:/app/data\n"
        )

    def _row_to_token_view(self, row: dict[str, Any], now_dt: datetime) -> RepeaterJoinTokenView:
        exp_dt = _parse_iso(row.get("expires_at"))
        expired = bool(exp_dt is not None and exp_dt <= now_dt)
        return RepeaterJoinTokenView(
            id=int(row["id"]),
            label=str(row.get("label") or ""),
            token_prefix=str(row.get("token_prefix") or ""),
            expires_at=str(row["expires_at"]),
            max_uses=int(row["max_uses"]),
            use_count=int(row["use_count"]),
            revoked=bool(row["revoked"]),
            expired=expired,
            created_at=str(row["created_at"]),
        )

    async def create_join_token(
        self,
        label: str = "",
        expires_hours: int = 24,
        max_uses: int = 1,
        main_url_override: str = "",
    ) -> RepeaterJoinTokenCreateResponse:
        token = f"wnjt_{secrets.token_urlsafe(24)}"
        token_hash = hash_token(token)
        token_prefix = token[:12]
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        expires_iso = (now_dt + timedelta(hours=int(expires_hours))).isoformat()

        row_id = await self.db.execute(
            """
            INSERT INTO repeater_join_tokens (
                label, token_hash, token_prefix, expires_at, max_uses, use_count, revoked, created_at
            ) VALUES (?, ?, ?, ?, ?, 0, 0, ?)
            """,
            (label.strip(), token_hash, token_prefix, expires_iso, int(max_uses), now_iso),
        )
        row = await self.db.fetch_one(
            "SELECT * FROM repeater_join_tokens WHERE id = ?", (row_id,)
        )
        assert row is not None
        main_url = (
            main_url_override.strip().rstrip("/")
            or f"http://{detect_local_lan_ip()}:{settings.web_port}"
        )
        await self.event_log.record(
            "repeater",
            "info",
            "repeater_join_token_created",
            f"Generated Repeater join token '{label.strip() or token_prefix}'",
            {"token_id": row_id, "expires_hours": expires_hours, "max_uses": max_uses},
        )
        return RepeaterJoinTokenCreateResponse(
            token=token,
            token_record=self._row_to_token_view(row, now_dt),
            main_url=main_url,
            docker_compose_yaml=self._build_compose_snippet(main_url, token, label),
        )

    async def list_join_tokens(self) -> list[RepeaterJoinTokenView]:
        rows = await self.db.fetch_all(
            "SELECT * FROM repeater_join_tokens ORDER BY id DESC LIMIT 50"
        )
        now_dt = datetime.now(timezone.utc)
        return [self._row_to_token_view(r, now_dt) for r in rows]

    async def revoke_join_token(self, token_id: int) -> bool:
        row = await self.db.fetch_one(
            "SELECT id, label, token_prefix FROM repeater_join_tokens WHERE id = ?",
            (token_id,),
        )
        if row is None:
            return False
        await self.db.execute(
            "UPDATE repeater_join_tokens SET revoked = 1 WHERE id = ?",
            (token_id,),
        )
        await self.event_log.record(
            "repeater",
            "info",
            "repeater_join_token_revoked",
            f"Revoked join token '{row.get('label') or row.get('token_prefix')}'",
            {"token_id": token_id},
        )
        return True

    async def _unique_slug(self, node_name: str) -> str:
        base = slugify_node_base(node_name)
        rows = await self.db.fetch_all("SELECT slug FROM repeater_nodes")
        existing = {str(r["slug"]) for r in rows}
        if base not in existing:
            return base
        idx = 2
        while True:
            suffix = f"_{idx}"
            candidate = f"{base[: max(1, 24 - len(suffix))]}{suffix}"
            if candidate not in existing:
                return candidate
            idx += 1

    async def redeem_join_token(
        self,
        req: RepeaterJoinRequest,
        remote_ip: str = "",
    ) -> RepeaterJoinResponse:
        token_hash = hash_token(req.join_token.strip())
        tok = await self.db.fetch_one(
            "SELECT * FROM repeater_join_tokens WHERE token_hash = ?",
            (token_hash,),
        )
        if tok is None:
            raise PermissionError("Invalid join token")
        if bool(tok["revoked"]):
            raise PermissionError("Join token has been revoked")
        exp_dt = _parse_iso(tok.get("expires_at"))
        now_dt = datetime.now(timezone.utc)
        if exp_dt is not None and exp_dt <= now_dt:
            raise PermissionError("Join token has expired")
        if int(tok["use_count"]) >= int(tok["max_uses"]):
            raise PermissionError("Join token has reached its maximum uses")

        now_iso = now_dt.isoformat()
        await self.db.execute(
            "UPDATE repeater_join_tokens SET use_count = use_count + 1 WHERE id = ?",
            (int(tok["id"]),),
        )

        node_name = req.node_name.strip()
        slug = await self._unique_slug(node_name)
        auth_token = f"wnrt_{secrets.token_urlsafe(32)}"
        auth_hash = hash_token(auth_token)

        node_id = await self.db.execute(
            """
            INSERT INTO repeater_nodes (
                name, slug, auth_token_hash, ui_url, remote_ip, app_version,
                enabled, last_seen_at, last_ups_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, '[]', ?, ?)
            """,
            (
                node_name,
                slug,
                auth_hash,
                req.ui_url.strip(),
                remote_ip.strip(),
                req.app_version.strip(),
                now_iso,
                now_iso,
                now_iso,
            ),
        )
        await self.refresh_cache()
        await self.event_log.record(
            "repeater",
            "success",
            "repeater_node_paired",
            f"Repeater node '{node_name}' paired (namespace: {slug}_*)",
            {"node_id": node_id, "slug": slug, "remote_ip": remote_ip},
        )
        await self._broadcast_nodes()
        return RepeaterJoinResponse(
            node_id=node_id,
            node_name=node_name,
            node_slug=slug,
            auth_token=auth_token,
            ws_path="/ws/repeater",
        )

    # --- Node Management ---

    def _row_to_node_view(self, row: dict[str, Any]) -> RepeaterNodeView:
        nid = int(row["id"])
        session = self._sessions.get(nid)
        if session is not None:
            ups_items = session.live_ups
        else:
            try:
                ups_items = json.loads(row.get("last_ups_json") or "[]")
            except json.JSONDecodeError:
                ups_items = []
        ups_names = [str(u.get("name")) for u in ups_items if isinstance(u, dict) and u.get("name")]
        return RepeaterNodeView(
            id=nid,
            name=str(row["name"]),
            slug=str(row["slug"]),
            ui_url=str(row.get("ui_url") or ""),
            remote_ip=str((session.remote_ip if session else row.get("remote_ip")) or ""),
            app_version=str(row.get("app_version") or ""),
            enabled=bool(row.get("enabled", 1)),
            online=session is not None,
            latency_ms=session.latency_ms if session is not None else None,
            last_seen_at=row.get("last_seen_at"),
            ups_count=len(ups_names),
            ups_names=ups_names,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    async def list_nodes(self) -> list[RepeaterNodeView]:
        rows = await self.db.fetch_all("SELECT * FROM repeater_nodes ORDER BY id ASC")
        return [self._row_to_node_view(r) for r in rows]

    async def update_node(
        self, node_id: int, req: RepeaterNodeUpdateRequest
    ) -> RepeaterNodeView | None:
        existing = await self.db.fetch_one(
            "SELECT * FROM repeater_nodes WHERE id = ?", (node_id,)
        )
        if existing is None:
            return None
        new_name = req.name.strip() if req.name is not None else str(existing["name"])
        new_ui_url = req.ui_url.strip() if req.ui_url is not None else str(existing["ui_url"])
        new_enabled = (
            bool(req.enabled) if req.enabled is not None else bool(existing.get("enabled", 1))
        )
        now_iso = utc_now_iso()
        await self.db.execute(
            """
            UPDATE repeater_nodes
            SET name = ?, ui_url = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_name, new_ui_url, 1 if new_enabled else 0, now_iso, node_id),
        )
        if not new_enabled:
            session = self._sessions.pop(node_id, None)
            if session is not None:
                self._fail_pending_rpcs(session, "Repeater node was disabled")
                try:
                    await session.websocket.close(code=4003)
                except Exception:
                    pass
        await self.refresh_cache()
        await self._notify_telemetry_change()
        await self._broadcast_nodes()
        updated = await self.db.fetch_one(
            "SELECT * FROM repeater_nodes WHERE id = ?", (node_id,)
        )
        assert updated is not None
        return self._row_to_node_view(updated)

    async def delete_node(self, node_id: int) -> bool:
        existing = await self.db.fetch_one(
            "SELECT * FROM repeater_nodes WHERE id = ?", (node_id,)
        )
        if existing is None:
            return False
        session = self._sessions.pop(node_id, None)
        if session is not None:
            self._fail_pending_rpcs(session, "Repeater node was deleted")
            try:
                await session.websocket.close(code=4004)
            except Exception:
                pass
        await self.db.execute("DELETE FROM repeater_nodes WHERE id = ?", (node_id,))
        await self.refresh_cache()
        await self.event_log.record(
            "repeater",
            "info",
            "repeater_node_deleted",
            f"Removed Repeater node '{existing['name']}'",
            {"node_id": node_id, "slug": existing["slug"]},
        )
        await self._notify_telemetry_change()
        await self._broadcast_nodes()
        return True

    # --- WebSocket Session Handling & RPC ---

    def _fail_pending_rpcs(self, session: NodeSession, message: str) -> None:
        for req_id, fut in list(session.pending_rpcs.items()):
            if not fut.done():
                fut.set_exception(RuntimeError(message))
            session.pending_rpcs.pop(req_id, None)

    async def _send_ws_json(self, session: NodeSession, payload: dict[str, Any]) -> None:
        async with session.send_lock:
            await session.websocket.send_json(payload)

    async def call_rpc(
        self,
        node_id: int,
        action: str,
        params: dict[str, Any],
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        session = self._sessions.get(node_id)
        if session is None:
            raise RuntimeError(f"Repeater node {node_id} is offline")

        self._rpc_seq += 1
        req_id = f"rpc_{self._rpc_seq}_{secrets.token_hex(4)}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        session.pending_rpcs[req_id] = fut

        try:
            await self._send_ws_json(
                session,
                {
                    "type": "rpc_request",
                    "id": req_id,
                    "action": action,
                    "params": params,
                },
            )
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Timed out waiting for RPC '{action}' from Repeater node '{session.name}'"
            ) from exc
        finally:
            session.pending_rpcs.pop(req_id, None)

    def _extract_ws_token(self, websocket: WebSocket) -> str:
        auth = str(websocket.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return str(websocket.query_params.get("token") or "").strip()

    async def handle_websocket(self, websocket: WebSocket) -> None:
        token = self._extract_ws_token(websocket)
        if not token:
            await websocket.close(code=4001)
            return

        token_hash = hash_token(token)
        node_row = await self.db.fetch_one(
            "SELECT * FROM repeater_nodes WHERE auth_token_hash = ? AND enabled = 1",
            (token_hash,),
        )
        if node_row is None:
            await websocket.close(code=4001)
            return

        node_id = int(node_row["id"])
        node_name = str(node_row["name"])
        node_slug = str(node_row["slug"])
        peer = websocket.client
        remote_ip = str(peer.host) if peer and getattr(peer, "host", None) else str(node_row.get("remote_ip") or "")

        await websocket.accept()

        # Replace stale connection cleanly if reconnecting before timeout
        prev_session = self._sessions.pop(node_id, None)
        was_offline = prev_session is None
        if prev_session is not None:
            self._fail_pending_rpcs(prev_session, "Replaced by newer Repeater session")
            try:
                await prev_session.websocket.close(code=4000)
            except Exception:
                pass

        now_iso = utc_now_iso()
        session = NodeSession(
            node_id=node_id,
            name=node_name,
            slug=node_slug,
            websocket=websocket,
            remote_ip=remote_ip,
            connected_at=now_iso,
            last_heartbeat_mono=time.monotonic(),
        )
        try:
            prev_ups = json.loads(node_row.get("last_ups_json") or "[]")
            if isinstance(prev_ups, list):
                session.live_ups = prev_ups
        except json.JSONDecodeError:
            session.live_ups = []

        self._sessions[node_id] = session
        await self.db.execute(
            "UPDATE repeater_nodes SET remote_ip = ?, last_seen_at = ?, updated_at = ? WHERE id = ?",
            (remote_ip, now_iso, now_iso, node_id),
        )
        await self.refresh_cache()

        if was_offline:
            await self.event_log.record(
                "repeater",
                "success",
                "repeater_node_online",
                f"Repeater node '{node_name}' connected from {remote_ip or 'unknown'}",
                {"node_id": node_id, "slug": node_slug, "remote_ip": remote_ip},
            )
            await self.dispatcher.dispatch_event(
                "repeater_node_online",
                f"Repeater Node Online: {node_name}",
                f"Repeater node '{node_name}' ({node_slug}) connected and streaming telemetry.",
                {"node_id": node_id, "slug": node_slug},
            )
        await self._broadcast_nodes()

        try:
            while True:
                msg = await websocket.receive_json()
                if not isinstance(msg, dict):
                    continue
                session.last_heartbeat_mono = time.monotonic()
                mtype = str(msg.get("type") or "")

                if mtype == "hello":
                    app_ver = str(msg.get("app_version") or "").strip()
                    ui_url = str(msg.get("ui_url") or "").strip()
                    updates: list[str] = ["last_seen_at = ?", "updated_at = ?"]
                    params: list[Any] = [utc_now_iso(), utc_now_iso()]
                    if app_ver:
                        updates.append("app_version = ?")
                        params.append(app_ver)
                    if ui_url:
                        updates.append("ui_url = ?")
                        params.append(ui_url)
                    params.append(node_id)
                    await self.db.execute(
                        f"UPDATE repeater_nodes SET {', '.join(updates)} WHERE id = ?",
                        tuple(params),
                    )
                    await self.refresh_cache()
                    await self._broadcast_nodes()

                elif mtype == "heartbeat":
                    now_str = utc_now_iso()
                    if isinstance(msg.get("latency_ms"), (int, float)):
                        session.latency_ms = float(msg["latency_ms"])
                    await self.db.execute(
                        "UPDATE repeater_nodes SET last_seen_at = ? WHERE id = ?",
                        (now_str, node_id),
                    )
                    await self._send_ws_json(
                        session,
                        {"type": "heartbeat_ack", "ts": msg.get("ts")},
                    )

                elif mtype == "telemetry":
                    raw_snapshot = msg.get("data") if isinstance(msg.get("data"), dict) else {}
                    raw_ups_list = raw_snapshot.get("ups_list") or []
                    latest_row = await self.db.fetch_one(
                        "SELECT name, slug FROM repeater_nodes WHERE id = ?", (node_id,)
                    )
                    cur_name = str(latest_row["name"]) if latest_row else node_name
                    cur_slug = str(latest_row["slug"]) if latest_row else node_slug
                    session.name = cur_name
                    session.slug = cur_slug

                    normalized: list[dict[str, Any]] = []
                    for item in raw_ups_list:
                        if not isinstance(item, dict) or not item.get("name"):
                            continue
                        raw_name = str(item["name"])
                        ns_name = namespace_ups_name(cur_slug, raw_name)
                        entry = dict(item)
                        entry["name"] = ns_name
                        entry["remote_ups_name"] = raw_name
                        entry["source_node_id"] = node_id
                        entry["source_node_name"] = cur_name
                        entry["source_node_slug"] = cur_slug
                        entry["node_online"] = True
                        normalized.append(entry)

                    session.live_ups = normalized
                    now_str = utc_now_iso()
                    await self.db.execute(
                        "UPDATE repeater_nodes SET last_seen_at = ?, last_ups_json = ?, updated_at = ? WHERE id = ?",
                        (now_str, json.dumps(normalized), now_str, node_id),
                    )
                    await self.refresh_cache()
                    await self._notify_telemetry_change()
                    await self._broadcast_nodes()

                elif mtype == "rpc_response":
                    req_id = str(msg.get("id") or "")
                    fut = session.pending_rpcs.get(req_id)
                    if fut is not None and not fut.done():
                        if msg.get("ok", False):
                            fut.set_result(
                                msg.get("result") if isinstance(msg.get("result"), dict) else {}
                            )
                        else:
                            fut.set_exception(
                                RuntimeError(str(msg.get("error") or "Remote RPC failed"))
                            )

        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug("Repeater session %s closed with exception: %s", node_slug, exc)
        finally:
            self._fail_pending_rpcs(session, f"Repeater node '{node_name}' disconnected")
            # Only clean up & alert if this session is still the active one for node_id
            active = self._sessions.get(node_id)
            if active is session:
                self._sessions.pop(node_id, None)
                self._rebuild_ups_index()
                if not self._stop_event.is_set():
                    cleanup_task = asyncio.create_task(
                        self._handle_node_disconnected(node_id),
                        name=f"repeater-disconnect-{node_id}",
                    )
                    self._cleanup_tasks.add(cleanup_task)
                    cleanup_task.add_done_callback(self._cleanup_tasks.discard)
                    try:
                        await asyncio.shield(cleanup_task)
                    except asyncio.CancelledError:
                        pass

    async def _handle_node_disconnected(self, node_id: int) -> None:
        try:
            row_check = await self.db.fetch_one(
                "SELECT id, name, slug, enabled FROM repeater_nodes WHERE id = ?",
                (node_id,),
            )
            if row_check is not None and bool(row_check.get("enabled", 1)):
                await self.refresh_cache()
                await self.event_log.record(
                    "repeater",
                    "warning",
                    "repeater_node_offline",
                    f"Repeater node '{row_check['name']}' went offline",
                    {"node_id": node_id, "slug": row_check["slug"]},
                )
                await self.dispatcher.dispatch_event(
                    "repeater_node_offline",
                    f"Repeater Node Offline: {row_check['name']}",
                    (
                        f"Repeater node '{row_check['name']}' disconnected. "
                        "Cached UPSes marked OFFLINE NOCOMM and host disconnect policies are active."
                    ),
                    {"node_id": node_id, "slug": row_check["slug"]},
                )
                await self._notify_telemetry_change()
                await self._broadcast_nodes()
        except Exception as exc:
            logger.exception("Repeater disconnect cleanup failed for node %s: %s", node_id, exc)

    # --- Watchdog Loop ---

    async def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            now_mono = time.monotonic()
            for nid, session in list(self._sessions.items()):
                if (now_mono - session.last_heartbeat_mono) > self.heartbeat_timeout_sec:
                    logger.warning(
                        "Repeater node '%s' timed out (> %ss without heartbeat); closing socket",
                        session.name,
                        self.heartbeat_timeout_sec,
                    )
                    try:
                        await session.websocket.close(code=4002)
                    except Exception:
                        pass
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        await self.refresh_cache()
        if self._watchdog_task is None or self._watchdog_task.done():
            self._stop_event.clear()
            self._watchdog_task = asyncio.create_task(
                self._watchdog_loop(), name="repeater-hub-watchdog"
            )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None
        for task in list(self._cleanup_tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._cleanup_tasks.clear()
        for session in list(self._sessions.values()):
            self._fail_pending_rpcs(session, "RepeaterHub shutting down")
            try:
                await session.websocket.close(code=1001)
            except Exception:
                pass
        self._sessions.clear()
