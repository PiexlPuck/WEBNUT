"""Async SQLite database wrapper and versioned schema migrations."""

from __future__ import annotations

import json
import secrets
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
    ("ups_selftest_completed", 1, 0),
    ("ups_selftest_failed", 1, 1),
    ("repeater_node_offline", 1, 1),
    ("repeater_node_online", 1, 0),
    ("flow_drill_completed", 1, 0),
    ("flow_drill_failed", 1, 1),
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

        if 7 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS nut_client_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id INTEGER,
                    device_name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    server_host TEXT NOT NULL,
                    nut_port INTEGER NOT NULL DEFAULT 3493,
                    ups_name TEXT NOT NULL,
                    username TEXT NOT NULL,
                    final_delay INTEGER NOT NULL DEFAULT 5,
                    poll_freq INTEGER NOT NULL DEFAULT 5,
                    deadtime INTEGER NOT NULL DEFAULT 15,
                    shutdown_cmd TEXT NOT NULL DEFAULT '/sbin/shutdown -h +0',
                    delivery_method TEXT NOT NULL DEFAULT 'manual',
                    deploy_status TEXT NOT NULL DEFAULT 'generated',
                    config_payload_enc TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (host_id) REFERENCES hosts(id) ON DELETE SET NULL
                );
                """
            )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (7, ?)",
                (now,),
            )
            await self.conn.commit()

        if 8 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS integration_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    api_key_enabled INTEGER NOT NULL DEFAULT 0,
                    api_key_enc TEXT NOT NULL DEFAULT '',
                    homepage_enabled INTEGER NOT NULL DEFAULT 1,
                    homepage_default_ups TEXT NOT NULL DEFAULT '',
                    homepage_refresh_ms INTEGER NOT NULL DEFAULT 10000,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO integration_settings (
                    id, api_key_enabled, api_key_enc, homepage_enabled,
                    homepage_default_ups, homepage_refresh_ms, updated_at
                ) VALUES (1, 0, '', 1, '', 10000, ?)
                """,
                (now,),
            )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (8, ?)",
                (now,),
            )
            await self.conn.commit()

        if 9 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ups_selftest_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ups_name TEXT NOT NULL,
                    test_command TEXT NOT NULL DEFAULT 'test.battery.start.quick',
                    frequency TEXT NOT NULL DEFAULT 'weekly',
                    day_of_week INTEGER NOT NULL DEFAULT 0,
                    day_of_month INTEGER NOT NULL DEFAULT 1,
                    time_hhmm TEXT NOT NULL DEFAULT '03:00',
                    min_battery_pct REAL NOT NULL DEFAULT 80.0,
                    require_online INTEGER NOT NULL DEFAULT 1,
                    notify_on_complete INTEGER NOT NULL DEFAULT 1,
                    notify_on_fail INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_at TEXT,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ups_selftest_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER,
                    ups_name TEXT NOT NULL,
                    test_command TEXT NOT NULL,
                    trigger_type TEXT NOT NULL DEFAULT 'manual',
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_sec INTEGER,
                    battery_before REAL,
                    battery_after REAL,
                    runtime_before REAL,
                    runtime_after REAL,
                    load_pct REAL,
                    ups_test_result TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (schedule_id) REFERENCES ups_selftest_schedules(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_selftest_reports_started_at
                    ON ups_selftest_reports(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_selftest_reports_ups
                    ON ups_selftest_reports(ups_name);
                """
            )
            for event_type, enabled, priority in (
                ("ups_selftest_completed", 1, 0),
                ("ups_selftest_failed", 1, 1),
            ):
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_rules (
                        event_type, enabled, priority, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (event_type, enabled, priority, now),
                )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (9, ?)",
                (now,),
            )
            await self.conn.commit()

        if 10 not in applied:
            now = utc_now_iso()
            cols = {
                r["name"]
                for r in await self.fetch_all("PRAGMA table_info(integration_settings)")
            }
            if "homeassistant_enabled" not in cols:
                await self.conn.execute(
                    "ALTER TABLE integration_settings ADD COLUMN homeassistant_enabled INTEGER NOT NULL DEFAULT 1"
                )
            if "instance_id" not in cols:
                await self.conn.execute(
                    "ALTER TABLE integration_settings ADD COLUMN instance_id TEXT NOT NULL DEFAULT ''"
                )
            await self.conn.execute(
                "UPDATE integration_settings SET instance_id = ? WHERE id = 1 AND instance_id = ''",
                (secrets.token_hex(8),),
            )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (10, ?)",
                (now,),
            )
            await self.conn.commit()

        if 11 not in applied:
            now = utc_now_iso()
            cols = {
                r["name"]
                for r in await self.fetch_all("PRAGMA table_info(integration_settings)")
            }
            if "prometheus_enabled" not in cols:
                await self.conn.execute(
                    "ALTER TABLE integration_settings ADD COLUMN prometheus_enabled INTEGER NOT NULL DEFAULT 1"
                )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (11, ?)",
                (now,),
            )
            await self.conn.commit()

        if 12 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repeater_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    auth_token_hash TEXT NOT NULL,
                    ui_url TEXT NOT NULL DEFAULT '',
                    remote_ip TEXT NOT NULL DEFAULT '',
                    app_version TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_seen_at TEXT,
                    last_ups_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS repeater_join_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL DEFAULT '',
                    token_hash TEXT NOT NULL UNIQUE,
                    token_prefix TEXT NOT NULL DEFAULT '',
                    expires_at TEXT NOT NULL,
                    max_uses INTEGER NOT NULL DEFAULT 1,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS repeater_link_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    paired INTEGER NOT NULL DEFAULT 0,
                    main_url TEXT NOT NULL DEFAULT '',
                    node_id INTEGER,
                    node_name TEXT NOT NULL DEFAULT '',
                    node_slug TEXT NOT NULL DEFAULT '',
                    auth_token_enc TEXT NOT NULL DEFAULT '',
                    public_url TEXT NOT NULL DEFAULT '',
                    push_interval_sec INTEGER NOT NULL DEFAULT 3,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO repeater_link_config (
                    id, paired, main_url, node_id, node_name, node_slug,
                    auth_token_enc, public_url, push_interval_sec, updated_at
                ) VALUES (1, 0, '', NULL, '', '', '', '', 3, ?)
                """,
                (now,),
            )
            for event_type, enabled, priority in (
                ("repeater_node_offline", 1, 1),
                ("repeater_node_online", 1, 0),
            ):
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_rules (
                        event_type, enabled, priority, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (event_type, enabled, priority, now),
                )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (12, ?)",
                (now,),
            )
            await self.conn.commit()

        if 13 not in applied:
            now = utc_now_iso()
            cols = {
                r["name"]
                for r in await self.fetch_all("PRAGMA table_info(orchestrator_config)")
            }
            if "cold_start_boot_grace_sec" not in cols:
                await self.conn.execute(
                    "ALTER TABLE orchestrator_config ADD COLUMN cold_start_boot_grace_sec INTEGER NOT NULL DEFAULT 30"
                )
            if "cold_start_resume_recovery" not in cols:
                await self.conn.execute(
                    "ALTER TABLE orchestrator_config ADD COLUMN cold_start_resume_recovery INTEGER NOT NULL DEFAULT 1"
                )
            if "cold_start_wake_always_wake" not in cols:
                await self.conn.execute(
                    "ALTER TABLE orchestrator_config ADD COLUMN cold_start_wake_always_wake INTEGER NOT NULL DEFAULT 1"
                )

            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS flow_drill_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL DEFAULT 'Scheduled Flow Drill',
                    mode TEXT NOT NULL DEFAULT 'dry_run',
                    target_scope TEXT NOT NULL DEFAULT 'all',
                    host_ids_json TEXT NOT NULL DEFAULT '[]',
                    frequency TEXT NOT NULL DEFAULT 'weekly',
                    day_of_week INTEGER NOT NULL DEFAULT 0,
                    day_of_month INTEGER NOT NULL DEFAULT 1,
                    time_hhmm TEXT NOT NULL DEFAULT '04:00',
                    min_battery_pct REAL NOT NULL DEFAULT 80.0,
                    require_online INTEGER NOT NULL DEFAULT 1,
                    shutdown_timeout_sec INTEGER NOT NULL DEFAULT 180,
                    restore_delay_sec INTEGER NOT NULL DEFAULT 15,
                    notify_on_complete INTEGER NOT NULL DEFAULT 1,
                    notify_on_fail INTEGER NOT NULL DEFAULT 1,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_at TEXT,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS flow_drill_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER,
                    schedule_name TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'dry_run',
                    trigger_type TEXT NOT NULL DEFAULT 'manual',
                    status TEXT NOT NULL DEFAULT 'running',
                    current_stage TEXT NOT NULL DEFAULT 'precheck',
                    ups_name TEXT NOT NULL DEFAULT '',
                    ups_status_start TEXT NOT NULL DEFAULT '',
                    battery_start REAL,
                    battery_end REAL,
                    hosts_total INTEGER NOT NULL DEFAULT 0,
                    hosts_passed INTEGER NOT NULL DEFAULT 0,
                    hosts_failed INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    duration_sec INTEGER,
                    summary_message TEXT NOT NULL DEFAULT '',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    host_results_json TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY (schedule_id) REFERENCES flow_drill_schedules(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_flow_drill_reports_started_at
                    ON flow_drill_reports(started_at DESC);
                """
            )
            for event_type, enabled, priority in (
                ("flow_drill_completed", 1, 0),
                ("flow_drill_failed", 1, 1),
            ):
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO notification_rules (
                        event_type, enabled, priority, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (event_type, enabled, priority, now),
                )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (13, ?)",
                (now,),
            )
            await self.conn.commit()

        if 14 not in applied:
            now = utc_now_iso()
            await self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS onboarding_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    instance_name TEXT NOT NULL DEFAULT 'WebNUT',
                    completed INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    last_step INTEGER NOT NULL DEFAULT 0,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO onboarding_state (
                    id, instance_name, completed, skipped, last_step, completed_at, updated_at
                ) VALUES (1, 'WebNUT', 0, 0, 0, NULL, ?)
                """,
                (now,),
            )
            await self.conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (14, ?)",
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
