"""Automated UPS self-test scheduler, safety pre-checks, monitoring, and reporting."""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import Database, utc_now_iso
from app.models.schemas import (
    SelfTestReportRecord,
    SelfTestReportsResponse,
    SelfTestReportsSummary,
    SelfTestScheduleCreate,
    SelfTestScheduleRecord,
)
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.services.event_log import EventLogService
from app.services.ws_hub import WebSocketHub

logger = logging.getLogger(__name__)


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


def compute_next_run(
    frequency: str,
    day_of_week: int,
    day_of_month: int,
    time_hhmm: str,
    after_dt: datetime | None = None,
) -> str:
    """Compute the next UTC ISO-8601 run timestamp strictly after `after_dt`."""
    parts = time_hhmm.strip().split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid time_hhmm format: '{time_hhmm}'")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time_hhmm values: '{time_hhmm}'")

    if after_dt is None:
        base = datetime.now(timezone.utc)
    elif after_dt.tzinfo is None:
        base = after_dt.replace(tzinfo=timezone.utc)
    else:
        base = after_dt.astimezone(timezone.utc)

    freq = frequency.lower().strip()
    if freq == "daily":
        candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    if freq in ("weekly", "biweekly"):
        target_dow = int(day_of_week) % 7
        days_ahead = (target_dow - base.weekday()) % 7
        candidate = (base + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= base:
            step_days = 14 if freq == "biweekly" else 7
            candidate += timedelta(days=step_days)
        return candidate.isoformat()

    if freq == "monthly":
        target_dom = max(1, min(31, int(day_of_month)))
        max_day = calendar.monthrange(base.year, base.month)[1]
        candidate = base.replace(
            day=min(target_dom, max_day),
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate <= base:
            year = base.year + (1 if base.month == 12 else 0)
            month = 1 if base.month == 12 else base.month + 1
            next_max_day = calendar.monthrange(year, month)[1]
            candidate = candidate.replace(
                year=year,
                month=month,
                day=min(target_dom, next_max_day),
            )
        return candidate.isoformat()

    raise ValueError(f"Unsupported schedule frequency: '{frequency}'")


async def resolve_control_credentials_with_source(
    db: Database,
    nut_manager: Any,
) -> tuple[str, str, str]:
    """Resolve NUT control credentials from external config or local upsd.users."""
    conn_row = await db.fetch_one(
        "SELECT mode, username, password FROM nut_connection_config WHERE id = 1"
    )
    if conn_row and conn_row.get("mode") == "external":
        username = str(conn_row.get("username") or "").strip()
        password = str(conn_row.get("password") or "")
        if username:
            return username, password, "external_config"
        return "", "", "none"

    if nut_manager is None:
        return "", "", "none"

    parsed_users, _ = nut_manager.read_config("upsd.users", mask_secrets=False)
    users = list(parsed_users.get("users", []) or [])

    best_match: tuple[int, str, str] | None = None
    for entry in users:
        uname = str(entry.get("username") or "").strip()
        pwd = str(entry.get("password") or "")
        if not uname:
            continue
        instcmds = [str(c).strip().upper() for c in (entry.get("instcmds") or [])]
        actions = [str(a).strip().upper() for a in (entry.get("actions") or [])]
        has_all_cmds = "ALL" in instcmds
        has_any_cmd = len(instcmds) > 0
        has_set = "SET" in actions
        if not (has_all_cmds or has_any_cmd or has_set):
            continue

        score = 0
        if has_all_cmds:
            score += 4
        if has_set:
            score += 2
        if pwd:
            score += 1
        if best_match is None or score > best_match[0]:
            best_match = (score, uname, pwd)

    if best_match is not None:
        return best_match[1], best_match[2], "upsd.users"
    return "", "", "none"


async def resolve_control_credentials(db: Database, nut_manager: Any) -> tuple[str, str]:
    """Return `(username, password)` authorized for INSTCMD / SET VAR."""
    username, password, _ = await resolve_control_credentials_with_source(db, nut_manager)
    return username, password


def _row_to_schedule(row: dict[str, Any]) -> SelfTestScheduleRecord:
    return SelfTestScheduleRecord(
        id=int(row["id"]),
        ups_name=str(row["ups_name"]),
        test_command=str(row["test_command"]),
        frequency=row["frequency"],
        day_of_week=int(row["day_of_week"]),
        day_of_month=int(row["day_of_month"]),
        time_hhmm=str(row["time_hhmm"]),
        min_battery_pct=float(row["min_battery_pct"]),
        require_online=bool(row["require_online"]),
        notify_on_complete=bool(row["notify_on_complete"]),
        notify_on_fail=bool(row["notify_on_fail"]),
        enabled=bool(row["enabled"]),
        last_run_at=row.get("last_run_at"),
        next_run_at=row.get("next_run_at"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_report(row: dict[str, Any]) -> SelfTestReportRecord:
    return SelfTestReportRecord(
        id=int(row["id"]),
        schedule_id=int(row["schedule_id"]) if row.get("schedule_id") is not None else None,
        ups_name=str(row["ups_name"]),
        test_command=str(row["test_command"]),
        trigger_type=str(row["trigger_type"]),
        status=str(row["status"]),
        started_at=str(row["started_at"]),
        completed_at=row.get("completed_at"),
        duration_sec=int(row["duration_sec"]) if row.get("duration_sec") is not None else None,
        battery_before=float(row["battery_before"]) if row.get("battery_before") is not None else None,
        battery_after=float(row["battery_after"]) if row.get("battery_after") is not None else None,
        runtime_before=float(row["runtime_before"]) if row.get("runtime_before") is not None else None,
        runtime_after=float(row["runtime_after"]) if row.get("runtime_after") is not None else None,
        load_pct=float(row["load_pct"]) if row.get("load_pct") is not None else None,
        ups_test_result=str(row.get("ups_test_result") or ""),
        message=str(row.get("message") or ""),
    )


def _classify_selftest_result(ups_test_result: str, status_tokens: list[str]) -> str:
    lower = ups_test_result.lower()
    if (
        "fail" in lower
        or "error" in lower
        or "abort" in lower
        or "bad" in lower
        or "replace" in lower
        or "LB" in status_tokens
    ):
        return "failed"
    if "warn" in lower:
        return "warning"
    return "passed"


class SelfTestScheduler:
    """Manages UPS self-test schedules, safety pre-checks, async monitoring, and reports."""

    def __init__(
        self,
        db: Database,
        nut_client: NutTcpClient,
        nut_manager: Any,
        orchestrator: Any,
        dispatcher: NotificationDispatcher,
        event_log: EventLogService,
        ws_hub: WebSocketHub | None = None,
        poll_interval_sec: float = 30.0,
        monitor_step_sec: float = 3.0,
        monitor_max_wait_sec: float = 900.0,
        repeater_hub: Any = None,
    ) -> None:
        self.db = db
        self.nut_client = nut_client
        self.nut_manager = nut_manager
        self.orchestrator = orchestrator
        self.dispatcher = dispatcher
        self.event_log = event_log
        self.ws_hub = ws_hub
        self.poll_interval_sec = poll_interval_sec
        self.monitor_step_sec = monitor_step_sec
        self.monitor_max_wait_sec = monitor_max_wait_sec
        self.repeater_hub = repeater_hub

        self._task: asyncio.Task[None] | None = None
        self._monitor_tasks: set[asyncio.Task[None]] = set()
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._scheduler_loop(), name="selftest-scheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        tasks: list[asyncio.Task[None]] = []
        if self._task is not None:
            self._task.cancel()
            tasks.append(self._task)
            self._task = None
        for mtask in list(self._monitor_tasks):
            mtask.cancel()
            tasks.append(mtask)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._monitor_tasks.clear()

    async def _broadcast_report_update(self, report: SelfTestReportRecord) -> None:
        if self.ws_hub is not None:
            try:
                await self.ws_hub.broadcast("selftest_report", report.model_dump())
            except Exception:
                pass

    # --- Schedule CRUD ---

    async def list_schedules(self) -> list[SelfTestScheduleRecord]:
        rows = await self.db.fetch_all(
            "SELECT * FROM ups_selftest_schedules ORDER BY ups_name ASC, id ASC"
        )
        return [_row_to_schedule(r) for r in rows]

    async def get_schedule(self, schedule_id: int) -> SelfTestScheduleRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM ups_selftest_schedules WHERE id = ?",
            (schedule_id,),
        )
        return _row_to_schedule(row) if row is not None else None

    async def create_schedule(self, payload: SelfTestScheduleCreate) -> SelfTestScheduleRecord:
        now = utc_now_iso()
        next_run = (
            compute_next_run(
                payload.frequency,
                payload.day_of_week,
                payload.day_of_month,
                payload.time_hhmm,
            )
            if payload.enabled
            else None
        )
        new_id = await self.db.execute(
            """
            INSERT INTO ups_selftest_schedules (
                ups_name, test_command, frequency, day_of_week, day_of_month,
                time_hhmm, min_battery_pct, require_online, notify_on_complete,
                notify_on_fail, enabled, last_run_at, next_run_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                payload.ups_name.strip(),
                payload.test_command.strip(),
                payload.frequency,
                payload.day_of_week,
                payload.day_of_month,
                payload.time_hhmm,
                payload.min_battery_pct,
                1 if payload.require_online else 0,
                1 if payload.notify_on_complete else 0,
                1 if payload.notify_on_fail else 0,
                1 if payload.enabled else 0,
                next_run,
                now,
                now,
            ),
        )
        await self.event_log.record(
            "selftest",
            "info",
            "selftest_schedule_created",
            f"Created {payload.frequency} self-test schedule ({payload.test_command}) for '{payload.ups_name}'",
            {"schedule_id": new_id, "ups_name": payload.ups_name, "test_command": payload.test_command},
        )
        created = await self.get_schedule(new_id)
        assert created is not None
        return created

    async def update_schedule(
        self,
        schedule_id: int,
        payload: SelfTestScheduleCreate,
    ) -> SelfTestScheduleRecord | None:
        existing = await self.get_schedule(schedule_id)
        if existing is None:
            return None
        now = utc_now_iso()
        next_run = (
            compute_next_run(
                payload.frequency,
                payload.day_of_week,
                payload.day_of_month,
                payload.time_hhmm,
            )
            if payload.enabled
            else None
        )
        await self.db.execute(
            """
            UPDATE ups_selftest_schedules
            SET ups_name = ?, test_command = ?, frequency = ?, day_of_week = ?,
                day_of_month = ?, time_hhmm = ?, min_battery_pct = ?, require_online = ?,
                notify_on_complete = ?, notify_on_fail = ?, enabled = ?, next_run_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                payload.ups_name.strip(),
                payload.test_command.strip(),
                payload.frequency,
                payload.day_of_week,
                payload.day_of_month,
                payload.time_hhmm,
                payload.min_battery_pct,
                1 if payload.require_online else 0,
                1 if payload.notify_on_complete else 0,
                1 if payload.notify_on_fail else 0,
                1 if payload.enabled else 0,
                next_run,
                now,
                schedule_id,
            ),
        )
        await self.event_log.record(
            "selftest",
            "info",
            "selftest_schedule_updated",
            f"Updated self-test schedule #{schedule_id} for '{payload.ups_name}'",
            {"schedule_id": schedule_id, "enabled": payload.enabled},
        )
        return await self.get_schedule(schedule_id)

    async def delete_schedule(self, schedule_id: int) -> bool:
        existing = await self.get_schedule(schedule_id)
        if existing is None:
            return False
        await self.db.execute("DELETE FROM ups_selftest_schedules WHERE id = ?", (schedule_id,))
        await self.event_log.record(
            "selftest",
            "info",
            "selftest_schedule_deleted",
            f"Deleted self-test schedule #{schedule_id} for '{existing.ups_name}'",
            {"schedule_id": schedule_id, "ups_name": existing.ups_name},
        )
        return True

    # --- Reports ---

    async def get_report(self, report_id: int) -> SelfTestReportRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM ups_selftest_reports WHERE id = ?",
            (report_id,),
        )
        return _row_to_report(row) if row is not None else None

    async def list_reports(
        self,
        ups_name: str | None = None,
        limit: int = 100,
    ) -> SelfTestReportsResponse:
        if ups_name:
            rows = await self.db.fetch_all(
                "SELECT * FROM ups_selftest_reports WHERE ups_name = ? ORDER BY started_at DESC, id DESC",
                (ups_name,),
            )
        else:
            rows = await self.db.fetch_all(
                "SELECT * FROM ups_selftest_reports ORDER BY started_at DESC, id DESC"
            )

        total = len(rows)
        passed = sum(1 for r in rows if r["status"] == "passed")
        failed = sum(1 for r in rows if r["status"] == "failed")
        warning = sum(1 for r in rows if r["status"] == "warning")
        skipped = sum(1 for r in rows if r["status"] == "skipped")
        running = sum(1 for r in rows if r["status"] == "running")

        evaluated = passed + failed + warning
        pass_rate_pct = round((passed / evaluated) * 100.0, 1) if evaluated > 0 else None

        last_passed_at: str | None = None
        drops: list[float] = []
        for r in rows:
            if r["status"] == "passed" and last_passed_at is None:
                last_passed_at = r.get("completed_at") or r.get("started_at")
            b_before = r.get("battery_before")
            b_after = r.get("battery_after")
            if b_before is not None and b_after is not None:
                drops.append(float(b_before) - float(b_after))

        avg_battery_drop = round(sum(drops) / len(drops), 1) if drops else None

        summary = SelfTestReportsSummary(
            total=total,
            passed=passed,
            failed=failed,
            warning=warning,
            skipped=skipped,
            running=running,
            pass_rate_pct=pass_rate_pct,
            last_passed_at=last_passed_at,
            avg_battery_drop=avg_battery_drop,
        )
        limited = [_row_to_report(r) for r in rows[: max(1, limit)]]
        return SelfTestReportsResponse(summary=summary, reports=limited)

    async def delete_report(self, report_id: int) -> bool:
        existing = await self.get_report(report_id)
        if existing is None:
            return False
        await self.db.execute("DELETE FROM ups_selftest_reports WHERE id = ?", (report_id,))
        return True

    async def clear_reports(self, ups_name: str | None = None) -> int:
        if ups_name:
            row = await self.db.fetch_one(
                "SELECT COUNT(*) AS cnt FROM ups_selftest_reports WHERE ups_name = ?",
                (ups_name,),
            )
            count = int(row["cnt"]) if row else 0
            await self.db.execute("DELETE FROM ups_selftest_reports WHERE ups_name = ?", (ups_name,))
            return count
        row = await self.db.fetch_one("SELECT COUNT(*) AS cnt FROM ups_selftest_reports")
        count = int(row["cnt"]) if row else 0
        await self.db.execute("DELETE FROM ups_selftest_reports")
        return count

    # --- Telemetry lookup & Execution ---

    async def _find_ups_telemetry(self, ups_name: str, prefer_live: bool = False) -> dict[str, Any] | None:
        if not prefer_live and self.orchestrator is not None:
            latest = getattr(self.orchestrator, "latest_telemetry", None)
            if isinstance(latest, dict) and latest.get("connected"):
                for ups in latest.get("ups_list", []) or []:
                    if ups.get("name") == ups_name:
                        return ups

        remote = (
            self.repeater_hub.resolve_remote_ups(ups_name)
            if self.repeater_hub is not None
            else None
        )
        if remote is not None:
            node_id, _, raw_ups = remote
            if prefer_live:
                try:
                    res = await self.repeater_hub.call_rpc(node_id, "fetch_telemetry", {})
                    snap = res.get("snapshot") if isinstance(res, dict) else None
                    if isinstance(snap, dict):
                        for u in snap.get("ups_list") or []:
                            if u.get("name") == raw_ups:
                                copy = dict(u)
                                copy["name"] = ups_name
                                copy["remote_ups_name"] = raw_ups
                                copy["node_online"] = True
                                return copy
                except Exception:
                    pass
            if self.orchestrator is not None:
                latest = getattr(self.orchestrator, "latest_telemetry", None)
                if isinstance(latest, dict) and latest.get("connected"):
                    for ups in latest.get("ups_list", []) or []:
                        if ups.get("name") == ups_name:
                            return ups
            return None

        try:
            snapshot = await self.nut_client.fetch_telemetry()
            if snapshot.connected:
                for ups in snapshot.ups_list:
                    if ups.name == ups_name:
                        return ups.model_dump()
        except Exception:
            pass

        if prefer_live and self.orchestrator is not None:
            latest = getattr(self.orchestrator, "latest_telemetry", None)
            if isinstance(latest, dict) and latest.get("connected"):
                for ups in latest.get("ups_list", []) or []:
                    if ups.get("name") == ups_name:
                        return ups
        return None

    async def _insert_report(
        self,
        schedule_id: int | None,
        ups_name: str,
        test_command: str,
        trigger_type: str,
        status: str,
        started_at: str,
        completed_at: str | None = None,
        duration_sec: int | None = None,
        battery_before: float | None = None,
        battery_after: float | None = None,
        runtime_before: float | None = None,
        runtime_after: float | None = None,
        load_pct: float | None = None,
        ups_test_result: str = "",
        message: str = "",
    ) -> SelfTestReportRecord:
        report_id = await self.db.execute(
            """
            INSERT INTO ups_selftest_reports (
                schedule_id, ups_name, test_command, trigger_type, status,
                started_at, completed_at, duration_sec, battery_before, battery_after,
                runtime_before, runtime_after, load_pct, ups_test_result, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule_id,
                ups_name,
                test_command,
                trigger_type,
                status,
                started_at,
                completed_at,
                duration_sec,
                battery_before,
                battery_after,
                runtime_before,
                runtime_after,
                load_pct,
                ups_test_result,
                message,
            ),
        )
        record = await self.get_report(report_id)
        assert record is not None
        return record

    async def execute_selftest(
        self,
        ups_name: str,
        test_command: str = "test.battery.start.quick",
        trigger_type: str = "manual",
        schedule_id: int | None = None,
        min_battery_pct: float = 0.0,
        require_online: bool = True,
        notify_on_complete: bool = True,
        notify_on_fail: bool = True,
        wait_for_completion: bool = False,
    ) -> SelfTestReportRecord:
        """Run safety pre-checks, send INSTCMD, record report, and monitor completion."""
        started_at = utc_now_iso()
        ups_data = await self._find_ups_telemetry(ups_name, prefer_live=False)

        battery_before = (
            float(ups_data["battery_charge"])
            if ups_data and ups_data.get("battery_charge") is not None
            else None
        )
        runtime_before = (
            float(ups_data["battery_runtime"])
            if ups_data and ups_data.get("battery_runtime") is not None
            else None
        )
        load_pct = (
            float(ups_data["ups_load"])
            if ups_data and ups_data.get("ups_load") is not None
            else None
        )
        status_tokens: list[str] = list(ups_data.get("status_tokens") or []) if ups_data else []
        raw_vars: dict[str, str] = dict(ups_data.get("raw_vars") or {}) if ups_data else {}
        initial_test_result = str(raw_vars.get("ups.test.result") or "")

        # Safety pre-checks
        if require_online:
            if ups_data is None:
                msg = f"Skipped self-test: UPS '{ups_name}' is unreachable in live telemetry"
                report = await self._insert_report(
                    schedule_id=schedule_id,
                    ups_name=ups_name,
                    test_command=test_command,
                    trigger_type=trigger_type,
                    status="skipped",
                    started_at=started_at,
                    completed_at=started_at,
                    duration_sec=0,
                    message=msg,
                )
                await self.event_log.record(
                    "selftest", "warning", "selftest_skipped", msg,
                    {"ups_name": ups_name, "test_command": test_command, "report_id": report.id},
                )
                return report

            if "OL" not in status_tokens or "OB" in status_tokens:
                status_str = str(ups_data.get("status") or "UNKNOWN")
                msg = f"Skipped self-test: UPS '{ups_name}' is not online (status={status_str})"
                report = await self._insert_report(
                    schedule_id=schedule_id,
                    ups_name=ups_name,
                    test_command=test_command,
                    trigger_type=trigger_type,
                    status="skipped",
                    started_at=started_at,
                    completed_at=started_at,
                    duration_sec=0,
                    battery_before=battery_before,
                    runtime_before=runtime_before,
                    load_pct=load_pct,
                    message=msg,
                )
                await self.event_log.record(
                    "selftest", "warning", "selftest_skipped", msg,
                    {"ups_name": ups_name, "status": status_str, "report_id": report.id},
                )
                return report

        if min_battery_pct > 0:
            if battery_before is None or battery_before < min_battery_pct:
                msg = (
                    f"Skipped self-test: UPS '{ups_name}' battery {battery_before}% "
                    f"is below minimum {min_battery_pct}%"
                )
                report = await self._insert_report(
                    schedule_id=schedule_id,
                    ups_name=ups_name,
                    test_command=test_command,
                    trigger_type=trigger_type,
                    status="skipped",
                    started_at=started_at,
                    completed_at=started_at,
                    duration_sec=0,
                    battery_before=battery_before,
                    runtime_before=runtime_before,
                    load_pct=load_pct,
                    message=msg,
                )
                await self.event_log.record(
                    "selftest", "warning", "selftest_skipped", msg,
                    {
                        "ups_name": ups_name,
                        "battery_before": battery_before,
                        "min_battery_pct": min_battery_pct,
                        "report_id": report.id,
                    },
                )
                return report

        remote = (
            self.repeater_hub.resolve_remote_ups(ups_name)
            if self.repeater_hub is not None
            else None
        )
        if remote is not None:
            node_id, node_name, raw_ups = remote
            try:
                res = await self.repeater_hub.call_rpc(
                    node_id,
                    "run_command",
                    {"ups_name": raw_ups, "command": test_command},
                )
                ok = bool(res.get("ok", False))
                resp_msg = str(res.get("message") or "")
            except Exception as exc:
                ok = False
                resp_msg = f"Repeater '{node_name}' error: {exc}"
        else:
            # Resolve NUT control credentials
            username, password = await resolve_control_credentials(self.db, self.nut_manager)
            if not username or not password:
                msg = (
                    "No NUT control credentials configured "
                    "(add a user with instcmds=ALL in upsd.users or configure External NUT credentials)"
                )
                report = await self._insert_report(
                    schedule_id=schedule_id,
                    ups_name=ups_name,
                    test_command=test_command,
                    trigger_type=trigger_type,
                    status="failed",
                    started_at=started_at,
                    completed_at=started_at,
                    duration_sec=0,
                    battery_before=battery_before,
                    runtime_before=runtime_before,
                    load_pct=load_pct,
                    message=msg,
                )
                await self.event_log.record(
                    "selftest", "error", "selftest_failed", msg,
                    {"ups_name": ups_name, "test_command": test_command, "report_id": report.id},
                )
                if notify_on_fail:
                    await self.dispatcher.dispatch_event(
                        "ups_selftest_failed",
                        f"UPS Self-Test Failed ({ups_name})",
                        msg,
                        {"ups_name": ups_name, "test_command": test_command, "report_id": report.id},
                    )
                return report

            ok, resp_msg = await self.nut_client.run_instant_command(
                ups_name, test_command, username, password
            )
        if not ok:
            msg = f"INSTCMD '{test_command}' failed on '{ups_name}': {resp_msg}"
            report = await self._insert_report(
                schedule_id=schedule_id,
                ups_name=ups_name,
                test_command=test_command,
                trigger_type=trigger_type,
                status="failed",
                started_at=started_at,
                completed_at=started_at,
                duration_sec=0,
                battery_before=battery_before,
                runtime_before=runtime_before,
                load_pct=load_pct,
                message=msg,
            )
            await self.event_log.record(
                "selftest", "error", "selftest_failed", msg,
                {"ups_name": ups_name, "test_command": test_command, "report_id": report.id},
            )
            if notify_on_fail:
                await self.dispatcher.dispatch_event(
                    "ups_selftest_failed",
                    f"UPS Self-Test Failed ({ups_name})",
                    msg,
                    {"ups_name": ups_name, "test_command": test_command, "report_id": report.id},
                )
            return report

        report = await self._insert_report(
            schedule_id=schedule_id,
            ups_name=ups_name,
            test_command=test_command,
            trigger_type=trigger_type,
            status="running",
            started_at=started_at,
            battery_before=battery_before,
            runtime_before=runtime_before,
            load_pct=load_pct,
            ups_test_result=initial_test_result,
            message=f"Self-test '{test_command}' started",
        )
        await self.event_log.record(
            "selftest",
            "info",
            "selftest_started",
            f"Started {trigger_type} self-test '{test_command}' on '{ups_name}'",
            {"ups_name": ups_name, "test_command": test_command, "report_id": report.id},
        )
        await self._broadcast_report_update(report)

        if wait_for_completion:
            return await self._monitor_selftest(
                report_id=report.id,
                ups_name=ups_name,
                test_command=test_command,
                started_at_iso=started_at,
                initial_test_result=initial_test_result,
                notify_on_complete=notify_on_complete,
                notify_on_fail=notify_on_fail,
            )

        task = asyncio.create_task(
            self._monitor_selftest(
                report_id=report.id,
                ups_name=ups_name,
                test_command=test_command,
                started_at_iso=started_at,
                initial_test_result=initial_test_result,
                notify_on_complete=notify_on_complete,
                notify_on_fail=notify_on_fail,
            ),
            name=f"selftest-monitor-{report.id}",
        )
        self._monitor_tasks.add(task)
        task.add_done_callback(self._monitor_tasks.discard)
        return report

    async def _monitor_selftest(
        self,
        report_id: int,
        ups_name: str,
        test_command: str,
        started_at_iso: str,
        initial_test_result: str,
        notify_on_complete: bool,
        notify_on_fail: bool,
    ) -> SelfTestReportRecord:
        started_dt = _parse_iso(started_at_iso) or datetime.now(timezone.utc)
        saw_active = False
        timed_out = False
        latest_ups: dict[str, Any] | None = None
        last_test_result = initial_test_result
        last_tokens: list[str] = []

        settle_window = min(self.monitor_step_sec * 3.0, 10.0)

        while not self._stop_event.is_set():
            await asyncio.sleep(self.monitor_step_sec)
            elapsed = (datetime.now(timezone.utc) - started_dt).total_seconds()

            latest_ups = await self._find_ups_telemetry(ups_name, prefer_live=True)
            if latest_ups is not None:
                last_tokens = list(latest_ups.get("status_tokens") or [])
                raw_vars = dict(latest_ups.get("raw_vars") or {})
                last_test_result = str(raw_vars.get("ups.test.result") or "")
                res_lower = last_test_result.lower()
                is_active = (
                    "CAL" in last_tokens
                    or "OB" in last_tokens
                    or "in progress" in res_lower
                    or "testing" in res_lower
                )
                if is_active:
                    saw_active = True
                else:
                    if saw_active:
                        break
                    if last_test_result and last_test_result != initial_test_result:
                        break
                    if elapsed >= settle_window:
                        break

            if elapsed >= self.monitor_max_wait_sec:
                timed_out = True
                break

        completed_dt = datetime.now(timezone.utc)
        completed_at = completed_dt.isoformat()
        duration_sec = max(0, int((completed_dt - started_dt).total_seconds()))

        battery_after = (
            float(latest_ups["battery_charge"])
            if latest_ups and latest_ups.get("battery_charge") is not None
            else None
        )
        runtime_after = (
            float(latest_ups["battery_runtime"])
            if latest_ups and latest_ups.get("battery_runtime") is not None
            else None
        )

        if timed_out:
            final_status = "failed" if "LB" in last_tokens else "warning"
            message = f"Self-test '{test_command}' timed out waiting for completion after {duration_sec}s"
        else:
            final_status = _classify_selftest_result(last_test_result, last_tokens)
            result_note = f" ({last_test_result})" if last_test_result else ""
            message = f"Self-test '{test_command}' finished with status '{final_status}'{result_note}"

        await self.db.execute(
            """
            UPDATE ups_selftest_reports
            SET status = ?, completed_at = ?, duration_sec = ?,
                battery_after = ?, runtime_after = ?, ups_test_result = ?, message = ?
            WHERE id = ?
            """,
            (
                final_status,
                completed_at,
                duration_sec,
                battery_after,
                runtime_after,
                last_test_result,
                message,
                report_id,
            ),
        )

        severity = "success" if final_status == "passed" else ("warning" if final_status == "warning" else "error")
        event_type = "selftest_failed" if final_status == "failed" else "selftest_completed"
        await self.event_log.record(
            "selftest",
            severity,
            event_type,
            message,
            {
                "report_id": report_id,
                "ups_name": ups_name,
                "test_command": test_command,
                "status": final_status,
                "duration_sec": duration_sec,
                "battery_after": battery_after,
                "ups_test_result": last_test_result,
            },
        )

        if final_status == "failed" and notify_on_fail:
            await self.dispatcher.dispatch_event(
                "ups_selftest_failed",
                f"UPS Self-Test Failed ({ups_name})",
                message,
                {"ups_name": ups_name, "test_command": test_command, "report_id": report_id},
            )
        elif final_status in ("passed", "warning") and notify_on_complete:
            await self.dispatcher.dispatch_event(
                "ups_selftest_completed",
                f"UPS Self-Test {final_status.title()} ({ups_name})",
                message,
                {"ups_name": ups_name, "test_command": test_command, "report_id": report_id},
            )

        updated = await self.get_report(report_id)
        assert updated is not None
        await self._broadcast_report_update(updated)
        return updated

    async def run_schedule_now(
        self,
        schedule_id: int,
        wait_for_completion: bool = False,
    ) -> SelfTestReportRecord | None:
        schedule = await self.get_schedule(schedule_id)
        if schedule is None:
            return None
        now = utc_now_iso()
        await self.db.execute(
            "UPDATE ups_selftest_schedules SET last_run_at = ?, updated_at = ? WHERE id = ?",
            (now, now, schedule_id),
        )
        return await self.execute_selftest(
            ups_name=schedule.ups_name,
            test_command=schedule.test_command,
            trigger_type="manual",
            schedule_id=schedule.id,
            min_battery_pct=schedule.min_battery_pct,
            require_online=schedule.require_online,
            notify_on_complete=schedule.notify_on_complete,
            notify_on_fail=schedule.notify_on_fail,
            wait_for_completion=wait_for_completion,
        )

    async def process_due_schedules(self, now_dt: datetime | None = None) -> list[SelfTestReportRecord]:
        """Evaluate all enabled schedules and execute any that are due."""
        current = now_dt or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        rows = await self.db.fetch_all(
            "SELECT * FROM ups_selftest_schedules WHERE enabled = 1 ORDER BY id ASC"
        )
        reports: list[SelfTestReportRecord] = []
        for row in rows:
            next_dt = _parse_iso(row.get("next_run_at"))
            if next_dt is not None and next_dt > current:
                continue

            schedule_id = int(row["id"])
            now_iso = current.isoformat()
            next_run = compute_next_run(
                row["frequency"],
                int(row["day_of_week"]),
                int(row["day_of_month"]),
                str(row["time_hhmm"]),
                after_dt=current,
            )
            await self.db.execute(
                """
                UPDATE ups_selftest_schedules
                SET last_run_at = ?, next_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now_iso, next_run, now_iso, schedule_id),
            )
            report = await self.execute_selftest(
                ups_name=str(row["ups_name"]),
                test_command=str(row["test_command"]),
                trigger_type="scheduled",
                schedule_id=schedule_id,
                min_battery_pct=float(row["min_battery_pct"]),
                require_online=bool(row["require_online"]),
                notify_on_complete=bool(row["notify_on_complete"]),
                notify_on_fail=bool(row["notify_on_fail"]),
            )
            reports.append(report)
        return reports

    async def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.process_due_schedules()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Self-test scheduler tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_sec)
            except asyncio.TimeoutError:
                pass
