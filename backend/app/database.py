"""Async SQLite database wrapper and versioned schema migrations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

DEFAULT_NOTIFICATION_RULES: list[tuple[str, int, int]] = [
    ("ups_on_battery", 1, 1),
    ("ups_power_restored", 1, 0),
    ("ups_low_battery", 1, 2),
    ("ups_comms_lost", 1, 1),
    ("wake_sequence_started", 1, 0),
    ("host_wake_succeeded", 1, 0),
    ("host_wake_retrying", 1, 1),
    ("host_wake_failed", 1, 2),
    ("app_update_available", 1, 0),
]


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


MIGRATION_V1_SQL = """
CREATE TABLE IF NOT EXISTS hosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ip_or_host TEXT NOT NULL,
    mac_address TEXT NOT NULL,
    subnet_broadcast TEXT,
    boot_priority INTEGER NOT NULL DEFAULT 10,
    stagger_delay_sec INTEGER NOT NULL DEFAULT 15,
    check_method TEXT NOT NULL DEFAULT 'icmp',
    check_port INTEGER,
    boot_timeout_sec INTEGER NOT NULL DEFAULT 90,
    retry_interval_sec INTEGER NOT NULL DEFAULT 15,
    max_retries INTEGER NOT NULL DEFAULT 3,
    always_wake INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_online INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    was_online_before_outage INTEGER NOT NULL DEFAULT 0,
    wake_state TEXT NOT NULL DEFAULT 'IDLE',
    wake_attempt INTEGER NOT NULL DEFAULT 0,
    last_wake_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orchestrator_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    monitored_ups TEXT NOT NULL DEFAULT '',
    min_battery_charge REAL NOT NULL DEFAULT 25.0,
    stability_hold_sec INTEGER NOT NULL DEFAULT 60,
    host_poll_interval_sec INTEGER NOT NULL DEFAULT 30,
    ups_poll_interval_sec INTEGER NOT NULL DEFAULT 5,
    auto_recovery_enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orchestrator_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL DEFAULT 'NORMAL',
    last_ups_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    outage_started_at TEXT,
    stabilizing_started_at TEXT,
    recovery_started_at TEXT,
    snapshot_frozen INTEGER NOT NULL DEFAULT 0,
    is_dry_run INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_type TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_rules (
    event_type TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_logs_timestamp ON event_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_event_logs_category ON event_logs(category);
CREATE INDEX IF NOT EXISTS idx_event_logs_severity ON event_logs(severity);
"""


class Database:
    """Async SQLite connection manager with automatic schema migrations."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._run_migrations()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def _run_migrations(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        cur = await self.conn.execute("SELECT version FROM schema_migrations")
        applied = {row["version"] for row in await cur.fetchall()}

        if 1 not in applied:
            await self.conn.executescript(MIGRATION_V1_SQL)
            now = utc_now_iso()
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO orchestrator_config (
                    id, monitored_ups, min_battery_charge, stability_hold_sec,
                    host_poll_interval_sec, ups_poll_interval_sec, auto_recovery_enabled, updated_at
                ) VALUES (1, '', 25.0, 60, 30, 5, 1, ?)
                """,
                (now,),
            )
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO orchestrator_state (
                    id, state, last_ups_status, snapshot_frozen, is_dry_run, updated_at
                ) VALUES (1, 'NORMAL', 'UNKNOWN', 0, 0, ?)
                """,
                (now,),
            )
            default_pushover = json.dumps(
                {
                    "user_key": "",
                    "api_token": "",
                    "device": "",
                    "sound": "",
                    "emergency_retry_sec": 60,
                    "emergency_expire_sec": 3600,
                }
            )
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO notification_providers (
                    provider_type, enabled, config_json, updated_at
                ) VALUES ('pushover', 0, ?, ?)
                """,
                (default_pushover, now),
            )
            for event_type, enabled, priority in DEFAULT_NOTIFICATION_RULES:
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_rules (
                        event_type, enabled, priority, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (event_type, enabled, priority, now),
                )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                (now,),
            )
            await self.conn.commit()

        if 2 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                ALTER TABLE hosts ADD COLUMN nut_client_enabled INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE hosts ADD COLUMN nut_client_platform TEXT NOT NULL DEFAULT 'linux_debian';
                ALTER TABLE hosts ADD COLUMN nut_ups_name TEXT NOT NULL DEFAULT '';
                ALTER TABLE hosts ADD COLUMN nut_client_user TEXT NOT NULL DEFAULT '';
                """
            )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (2, ?)",
                (now,),
            )
            await self.conn.commit()

        if 3 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_update_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_version TEXT NOT NULL DEFAULT '1.0.0',
                    latest_version TEXT,
                    release_url TEXT,
                    release_notes TEXT,
                    update_available INTEGER NOT NULL DEFAULT 0,
                    last_checked_at TEXT,
                    last_error TEXT,
                    notified_version TEXT,
                    notified_at TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO notification_rules (
                    event_type, enabled, priority, updated_at
                ) VALUES ('app_update_available', 1, 0, ?)
                """,
                (now,),
            )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (3, ?)",
                (now,),
            )
            await self.conn.commit()

        if 4 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nut_connection_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    mode TEXT NOT NULL DEFAULT 'local',
                    host TEXT NOT NULL DEFAULT '127.0.0.1',
                    port INTEGER NOT NULL DEFAULT 3493,
                    username TEXT NOT NULL DEFAULT '',
                    password TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO nut_connection_config (
                    id, mode, host, port, username, password, updated_at
                ) VALUES (1, 'local', '127.0.0.1', 3493, '', '', ?)
                """,
                (now,),
            )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (4, ?)",
                (now,),
            )
            await self.conn.commit()

        if 5 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                ALTER TABLE hosts ADD COLUMN virtual_ups_name TEXT NOT NULL DEFAULT '';
                ALTER TABLE hosts ADD COLUMN wake_min_battery_charge REAL;
                ALTER TABLE hosts ADD COLUMN shutdown_mode TEXT NOT NULL DEFAULT 'nut_stream';
                ALTER TABLE hosts ADD COLUMN shutdown_battery_pct REAL;
                ALTER TABLE hosts ADD COLUMN shutdown_ob_delay_sec INTEGER;
                ALTER TABLE hosts ADD COLUMN disconnect_policy TEXT NOT NULL DEFAULT 'inherit';
                ALTER TABLE hosts ADD COLUMN disconnect_grace_sec INTEGER;
                ALTER TABLE hosts ADD COLUMN ssh_port INTEGER NOT NULL DEFAULT 22;
                ALTER TABLE hosts ADD COLUMN ssh_username TEXT NOT NULL DEFAULT '';
                ALTER TABLE hosts ADD COLUMN ssh_password_enc TEXT NOT NULL DEFAULT '';
                ALTER TABLE hosts ADD COLUMN ssh_sudo_password_enc TEXT NOT NULL DEFAULT '';
                ALTER TABLE hosts ADD COLUMN ssh_shutdown_cmd TEXT NOT NULL DEFAULT 'sudo -S shutdown -h now';

                ALTER TABLE orchestrator_config ADD COLUMN disconnect_policy TEXT NOT NULL DEFAULT 'keep_running';
                ALTER TABLE orchestrator_config ADD COLUMN disconnect_grace_sec INTEGER NOT NULL DEFAULT 600;
                ALTER TABLE orchestrator_config ADD COLUMN repeater_enabled INTEGER NOT NULL DEFAULT 1;
                ALTER TABLE orchestrator_config ADD COLUMN repeater_port INTEGER NOT NULL DEFAULT 3493;
                """
            )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (5, ?)",
                (now,),
            )
            await self.conn.commit()

        if 6 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nut_client_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_enc TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'secondary',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (6, ?)",
                (now,),
            )
            await self.conn.commit()

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur.lastrowid or 0


def sanitize_host_row(row: dict[str, Any]) -> dict[str, Any]:
    """Strip encrypted SSH secret columns before broadcasting host rows over WebSocket."""
    cleaned = dict(row)
    pw_enc = str(cleaned.pop("ssh_password_enc", "") or "")
    sudo_enc = str(cleaned.pop("ssh_sudo_password_enc", "") or "")
    cleaned["ssh_password_configured"] = bool(pw_enc)
    cleaned["ssh_password"] = "********" if pw_enc else ""
    cleaned["ssh_sudo_password"] = "********" if sudo_enc else ""
    return cleaned


async def fetch_public_hosts(db: Database) -> list[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM hosts ORDER BY boot_priority ASC, id ASC")
    return [sanitize_host_row(r) for r in rows]
