"""End-to-end outage & recovery flow drill runner, scheduler, and reporting."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.database import Database, utc_now_iso
from app.models.schemas import (
    FlowDrillHostResult,
    FlowDrillMode,
    FlowDrillReportRecord,
    FlowDrillReportsResponse,
    FlowDrillReportsSummary,
    FlowDrillScheduleCreate,
    FlowDrillScheduleRecord,
    FlowDrillStepEntry,
    FlowDrillTargetScope,
)
from app.notifications.dispatcher import NotificationDispatcher
from app.services.event_log import EventLogService
from app.services.selftest_scheduler import _parse_iso, compute_next_run
from app.services.ws_hub import WebSocketHub
from app.wol.magic_packet import build_magic_packet, normalize_mac

logger = logging.getLogger(__name__)


def _row_to_schedule(row: dict[str, Any]) -> FlowDrillScheduleRecord:
    try:
        host_ids = [int(x) for x in (json.loads(row.get("host_ids_json") or "[]") or [])]
    except (json.JSONDecodeError, TypeError, ValueError):
        host_ids = []
    return FlowDrillScheduleRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        mode=row["mode"],
        target_scope=row["target_scope"],
        host_ids=host_ids,
        frequency=row["frequency"],
        day_of_week=int(row["day_of_week"]),
        day_of_month=int(row["day_of_month"]),
        time_hhmm=str(row["time_hhmm"]),
        min_battery_pct=float(row["min_battery_pct"]),
        require_online=bool(row["require_online"]),
        shutdown_timeout_sec=int(row["shutdown_timeout_sec"]),
        restore_delay_sec=int(row["restore_delay_sec"]),
        notify_on_complete=bool(row["notify_on_complete"]),
        notify_on_fail=bool(row["notify_on_fail"]),
        enabled=bool(row["enabled"]),
        last_run_at=row.get("last_run_at"),
        next_run_at=row.get("next_run_at"),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_report(row: dict[str, Any]) -> FlowDrillReportRecord:
    try:
        raw_steps = json.loads(row.get("steps_json") or "[]") or []
    except (json.JSONDecodeError, TypeError):
        raw_steps = []
    try:
        raw_hosts = json.loads(row.get("host_results_json") or "[]") or []
    except (json.JSONDecodeError, TypeError):
        raw_hosts = []

    steps = [FlowDrillStepEntry(**s) for s in raw_steps if isinstance(s, dict)]
    host_results = [FlowDrillHostResult(**h) for h in raw_hosts if isinstance(h, dict)]

    return FlowDrillReportRecord(
        id=int(row["id"]),
        schedule_id=int(row["schedule_id"]) if row.get("schedule_id") is not None else None,
        schedule_name=str(row.get("schedule_name") or ""),
        mode=row["mode"],
        trigger_type=str(row["trigger_type"]),
        status=str(row["status"]),
        current_stage=str(row.get("current_stage") or "precheck"),
        ups_name=str(row.get("ups_name") or ""),
        ups_status_start=str(row.get("ups_status_start") or ""),
        battery_start=(
            float(row["battery_start"]) if row.get("battery_start") is not None else None
        ),
        battery_end=float(row["battery_end"]) if row.get("battery_end") is not None else None,
        hosts_total=int(row.get("hosts_total") or 0),
        hosts_passed=int(row.get("hosts_passed") or 0),
        hosts_failed=int(row.get("hosts_failed") or 0),
        started_at=str(row["started_at"]),
        completed_at=row.get("completed_at"),
        duration_sec=int(row["duration_sec"]) if row.get("duration_sec") is not None else None,
        summary_message=str(row.get("summary_message") or ""),
        steps=steps,
        host_results=host_results,
    )


class FlowDrillScheduler:
    """Executes and schedules one-off or recurring end-to-end outage & WoL recovery drills."""

    def __init__(
        self,
        db: Database,
        orchestrator: Any,
        dispatcher: NotificationDispatcher,
        event_log: EventLogService,
        ws_hub: WebSocketHub | None = None,
        poll_interval_sec: float = 30.0,
        check_step_sec: float = 2.0,
    ) -> None:
        self.db = db
        self.orchestrator = orchestrator
        self.dispatcher = dispatcher
        self.event_log = event_log
        self.ws_hub = ws_hub
        self.poll_interval_sec = poll_interval_sec
        self.check_step_sec = check_step_sec

        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._active_run_id: int | None = None
        self._active_task: asyncio.Task[FlowDrillReportRecord] | None = None
        self._abort_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._scheduler_loop(), name="flow-drill-scheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        self._abort_event.set()
        tasks: list[asyncio.Task[Any]] = []
        if self._task is not None:
            self._task.cancel()
            tasks.append(self._task)
            self._task = None
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()
            tasks.append(self._active_task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _broadcast_report(self, report: FlowDrillReportRecord) -> None:
        if self.ws_hub is not None:
            try:
                await self.ws_hub.broadcast("drill_update", report.model_dump())
            except Exception:
                pass

    # --- Schedule CRUD ---

    async def list_schedules(self) -> list[FlowDrillScheduleRecord]:
        rows = await self.db.fetch_all("SELECT * FROM flow_drill_schedules ORDER BY id ASC")
        return [_row_to_schedule(r) for r in rows]

    async def get_schedule(self, schedule_id: int) -> FlowDrillScheduleRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM flow_drill_schedules WHERE id = ?", (schedule_id,)
        )
        return _row_to_schedule(row) if row is not None else None

    async def create_schedule(self, payload: FlowDrillScheduleCreate) -> FlowDrillScheduleRecord:
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
        clean_ids = sorted({int(x) for x in payload.host_ids if int(x) > 0})
        new_id = await self.db.execute(
            """
            INSERT INTO flow_drill_schedules (
                name, mode, target_scope, host_ids_json, frequency, day_of_week,
                day_of_month, time_hhmm, min_battery_pct, require_online,
                shutdown_timeout_sec, restore_delay_sec, notify_on_complete,
                notify_on_fail, enabled, last_run_at, next_run_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                payload.name.strip(),
                payload.mode,
                payload.target_scope,
                json.dumps(clean_ids),
                payload.frequency,
                payload.day_of_week,
                payload.day_of_month,
                payload.time_hhmm,
                payload.min_battery_pct,
                1 if payload.require_online else 0,
                payload.shutdown_timeout_sec,
                payload.restore_delay_sec,
                1 if payload.notify_on_complete else 0,
                1 if payload.notify_on_fail else 0,
                1 if payload.enabled else 0,
                next_run,
                now,
                now,
            ),
        )
        await self.event_log.record(
            "drill",
            "info",
            "flow_drill_schedule_created",
            f"Created {payload.frequency} {payload.mode} flow drill schedule '{payload.name.strip()}'",
            {"schedule_id": new_id, "mode": payload.mode, "frequency": payload.frequency},
        )
        created = await self.get_schedule(new_id)
        assert created is not None
        return created

    async def update_schedule(
        self, schedule_id: int, payload: FlowDrillScheduleCreate
    ) -> FlowDrillScheduleRecord | None:
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
        clean_ids = sorted({int(x) for x in payload.host_ids if int(x) > 0})
        await self.db.execute(
            """
            UPDATE flow_drill_schedules
            SET name = ?,
                mode = ?,
                target_scope = ?,
                host_ids_json = ?,
                frequency = ?,
                day_of_week = ?,
                day_of_month = ?,
                time_hhmm = ?,
                min_battery_pct = ?,
                require_online = ?,
                shutdown_timeout_sec = ?,
                restore_delay_sec = ?,
                notify_on_complete = ?,
                notify_on_fail = ?,
                enabled = ?,
                next_run_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                payload.name.strip(),
                payload.mode,
                payload.target_scope,
                json.dumps(clean_ids),
                payload.frequency,
                payload.day_of_week,
                payload.day_of_month,
                payload.time_hhmm,
                payload.min_battery_pct,
                1 if payload.require_online else 0,
                payload.shutdown_timeout_sec,
                payload.restore_delay_sec,
                1 if payload.notify_on_complete else 0,
                1 if payload.notify_on_fail else 0,
                1 if payload.enabled else 0,
                next_run,
                now,
                schedule_id,
            ),
        )
        await self.event_log.record(
            "drill",
            "info",
            "flow_drill_schedule_updated",
            f"Updated flow drill schedule '{payload.name.strip()}'",
            {"schedule_id": schedule_id, "mode": payload.mode, "enabled": payload.enabled},
        )
        return await self.get_schedule(schedule_id)

    async def delete_schedule(self, schedule_id: int) -> bool:
        existing = await self.get_schedule(schedule_id)
        if existing is None:
            return False
        await self.db.execute("DELETE FROM flow_drill_schedules WHERE id = ?", (schedule_id,))
        await self.event_log.record(
            "drill",
            "info",
            "flow_drill_schedule_deleted",
            f"Deleted flow drill schedule '{existing.name}'",
            {"schedule_id": schedule_id},
        )
        return True

    async def run_schedule_now(
        self, schedule_id: int, wait_for_completion: bool = False
    ) -> FlowDrillReportRecord | None:
        sched = await self.get_schedule(schedule_id)
        if sched is None:
            return None
        now_dt = datetime.now(timezone.utc)
        next_run = (
            compute_next_run(
                sched.frequency,
                sched.day_of_week,
                sched.day_of_month,
                sched.time_hhmm,
                after_dt=now_dt,
            )
            if sched.enabled
            else None
        )
        await self.db.execute(
            "UPDATE flow_drill_schedules SET last_run_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
            (now_dt.isoformat(), next_run, now_dt.isoformat(), schedule_id),
        )
        return await self.start_drill(
            mode=sched.mode,
            target_scope=sched.target_scope,
            host_ids=sched.host_ids,
            min_battery_pct=sched.min_battery_pct,
            require_online=sched.require_online,
            shutdown_timeout_sec=sched.shutdown_timeout_sec,
            restore_delay_sec=sched.restore_delay_sec,
            notify_on_complete=sched.notify_on_complete,
            notify_on_fail=sched.notify_on_fail,
            confirm_live=True,
            trigger_type="scheduled",
            schedule_id=sched.id,
            schedule_name=sched.name,
            wait_for_completion=wait_for_completion,
        )

    # --- Report CRUD ---

    async def get_report(self, report_id: int) -> FlowDrillReportRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM flow_drill_reports WHERE id = ?", (report_id,)
        )
        return _row_to_report(row) if row is not None else None

    async def list_reports(self, limit: int = 100) -> FlowDrillReportsResponse:
        rows = await self.db.fetch_all(
            "SELECT * FROM flow_drill_reports ORDER BY started_at DESC, id DESC LIMIT ?",
            (max(1, min(500, int(limit))),),
        )
        reports = [_row_to_report(r) for r in rows]

        all_rows = await self.db.fetch_all(
            "SELECT status, started_at, completed_at, host_results_json FROM flow_drill_reports"
        )
        total = len(all_rows)
        passed = sum(1 for r in all_rows if r["status"] == "passed")
        warning = sum(1 for r in all_rows if r["status"] == "warning")
        failed = sum(1 for r in all_rows if r["status"] == "failed")
        skipped = sum(1 for r in all_rows if r["status"] == "skipped")
        aborted = sum(1 for r in all_rows if r["status"] == "aborted")
        running = sum(1 for r in all_rows if r["status"] == "running")

        denom = passed + warning + failed + aborted
        pass_rate = round((passed / denom) * 100.0, 1) if denom > 0 else None

        passed_dates = [
            str(r.get("completed_at") or r.get("started_at"))
            for r in all_rows
            if r["status"] == "passed"
        ]
        last_passed = max(passed_dates) if passed_dates else None

        sd_durations: list[int] = []
        wk_durations: list[int] = []
        for r in all_rows:
            try:
                h_list = json.loads(r.get("host_results_json") or "[]") or []
            except (json.JSONDecodeError, TypeError):
                h_list = []
            for h in h_list:
                if not isinstance(h, dict):
                    continue
                if h.get("shutdown_duration_sec") is not None:
                    sd_durations.append(int(h["shutdown_duration_sec"]))
                if h.get("wake_duration_sec") is not None:
                    wk_durations.append(int(h["wake_duration_sec"]))

        avg_sd = round(sum(sd_durations) / len(sd_durations), 1) if sd_durations else None
        avg_wk = round(sum(wk_durations) / len(wk_durations), 1) if wk_durations else None

        summary = FlowDrillReportsSummary(
            total=total,
            passed=passed,
            warning=warning,
            failed=failed,
            skipped=skipped,
            aborted=aborted,
            running=running,
            pass_rate_pct=pass_rate,
            avg_shutdown_sec=avg_sd,
            avg_wake_sec=avg_wk,
            last_passed_at=last_passed,
        )
        return FlowDrillReportsResponse(summary=summary, reports=reports)

    async def delete_report(self, report_id: int) -> bool:
        existing = await self.get_report(report_id)
        if existing is None:
            return False
        await self.db.execute("DELETE FROM flow_drill_reports WHERE id = ?", (report_id,))
        return True

    async def clear_reports(self) -> int:
        row = await self.db.fetch_one("SELECT COUNT(*) AS cnt FROM flow_drill_reports")
        count = int(row["cnt"]) if row else 0
        await self.db.execute("DELETE FROM flow_drill_reports WHERE status != 'running'")
        return count

    # --- Execution & Abort ---

    async def _resolve_target_hosts(
        self, target_scope: FlowDrillTargetScope, host_ids: list[int]
    ) -> list[dict[str, Any]]:
        clean_ids = [int(hid) for hid in host_ids if int(hid) > 0]
        if target_scope == "selected":
            if not clean_ids:
                raise ValueError("Select at least one host when target_scope is 'selected'")
            placeholders = ",".join("?" for _ in clean_ids)
            return await self.db.fetch_all(
                f"SELECT * FROM hosts WHERE enabled = 1 AND id IN ({placeholders}) ORDER BY boot_priority ASC, id ASC",
                tuple(clean_ids),
            )
        return await self.db.fetch_all(
            "SELECT * FROM hosts WHERE enabled = 1 ORDER BY boot_priority ASC, id ASC"
        )

    async def _save_report_state(
        self,
        report_id: int,
        *,
        status: str | None = None,
        current_stage: str | None = None,
        ups_name: str | None = None,
        ups_status_start: str | None = None,
        battery_start: float | None = None,
        battery_end: float | None = None,
        hosts_total: int | None = None,
        hosts_passed: int | None = None,
        hosts_failed: int | None = None,
        completed_at: str | None = None,
        duration_sec: int | None = None,
        summary_message: str | None = None,
        steps: list[FlowDrillStepEntry] | None = None,
        host_results: list[FlowDrillHostResult] | None = None,
    ) -> FlowDrillReportRecord:
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if current_stage is not None:
            sets.append("current_stage = ?")
            params.append(current_stage)
        if ups_name is not None:
            sets.append("ups_name = ?")
            params.append(ups_name)
        if ups_status_start is not None:
            sets.append("ups_status_start = ?")
            params.append(ups_status_start)
        if battery_start is not None:
            sets.append("battery_start = ?")
            params.append(battery_start)
        if battery_end is not None:
            sets.append("battery_end = ?")
            params.append(battery_end)
        if hosts_total is not None:
            sets.append("hosts_total = ?")
            params.append(hosts_total)
        if hosts_passed is not None:
            sets.append("hosts_passed = ?")
            params.append(hosts_passed)
        if hosts_failed is not None:
            sets.append("hosts_failed = ?")
            params.append(hosts_failed)
        if completed_at is not None:
            sets.append("completed_at = ?")
            params.append(completed_at)
        if duration_sec is not None:
            sets.append("duration_sec = ?")
            params.append(duration_sec)
        if summary_message is not None:
            sets.append("summary_message = ?")
            params.append(summary_message)
        if steps is not None:
            sets.append("steps_json = ?")
            params.append(json.dumps([s.model_dump() for s in steps]))
        if host_results is not None:
            sets.append("host_results_json = ?")
            params.append(json.dumps([h.model_dump() for h in host_results]))

        if sets:
            params.append(report_id)
            await self.db.execute(
                f"UPDATE flow_drill_reports SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
        rec = await self.get_report(report_id)
        assert rec is not None
        await self._broadcast_report(rec)
        return rec

    def _pick_primary_ups(self, monitored_ups: str) -> tuple[bool, dict[str, Any] | None]:
        tel = getattr(self.orchestrator, "latest_telemetry", {}) or {}
        connected = bool(tel.get("connected"))
        ups_list = list(tel.get("ups_list") or [])
        target = None
        if monitored_ups:
            for u in ups_list:
                if u.get("name") == monitored_ups:
                    target = u
                    break
        if target is None and ups_list:
            target = ups_list[0]
        return connected, target

    def _connected_nut_ips(self) -> set[str]:
        ips: set[str] = set()
        rep = getattr(self.orchestrator, "repeater", None)
        if rep is not None:
            for c in rep.get_connected_clients():
                ip = str(c.get("client_ip") or "").strip()
                if ip:
                    ips.add(ip)
        tel = getattr(self.orchestrator, "latest_telemetry", {}) or {}
        for u in tel.get("ups_list") or []:
            for cip in u.get("connected_clients") or []:
                ip = str(cip).strip()
                if ip:
                    ips.add(ip)
        return ips

    async def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleep up to `seconds`, returning True if aborted or stopped early."""
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            if self._abort_event.is_set() or self._stop_event.is_set():
                return True
            step = min(0.5, remaining)
            await asyncio.sleep(step)
            remaining -= step
        return self._abort_event.is_set() or self._stop_event.is_set()

    async def start_drill(
        self,
        *,
        mode: FlowDrillMode = "dry_run",
        target_scope: FlowDrillTargetScope = "all",
        host_ids: list[int] | None = None,
        min_battery_pct: float = 80.0,
        require_online: bool = True,
        shutdown_timeout_sec: int = 180,
        restore_delay_sec: int = 15,
        notify_on_complete: bool = True,
        notify_on_fail: bool = True,
        confirm_live: bool = False,
        trigger_type: str = "manual",
        schedule_id: int | None = None,
        schedule_name: str = "",
        wait_for_completion: bool = False,
    ) -> FlowDrillReportRecord:
        if mode == "live" and not confirm_live:
            raise ValueError("Live shutdown & restore flow drill requires confirm_live=True")
        if self._active_task is not None and not self._active_task.done():
            raise RuntimeError("A flow drill is already in progress")

        hosts = await self._resolve_target_hosts(target_scope, list(host_ids or []))
        started_at = utc_now_iso()
        initial_step = FlowDrillStepEntry(
            stage="precheck",
            status="running",
            started_at=started_at,
            message="Evaluating UPS health and host targets...",
        )
        report_id = await self.db.execute(
            """
            INSERT INTO flow_drill_reports (
                schedule_id, schedule_name, mode, trigger_type, status, current_stage,
                ups_name, ups_status_start, battery_start, battery_end,
                hosts_total, hosts_passed, hosts_failed, started_at,
                summary_message, steps_json, host_results_json
            ) VALUES (?, ?, ?, ?, 'running', 'precheck', '', '', NULL, NULL, ?, 0, 0, ?, 'Starting flow drill...', ?, '[]')
            """,
            (
                schedule_id,
                schedule_name,
                mode,
                trigger_type,
                len(hosts),
                started_at,
                json.dumps([initial_step.model_dump()]),
            ),
        )
        initial_report = await self.get_report(report_id)
        assert initial_report is not None
        await self._broadcast_report(initial_report)

        self._abort_event.clear()
        self._active_run_id = report_id

        coro = self._execute_drill(
            report_id=report_id,
            hosts=hosts,
            mode=mode,
            trigger_type=trigger_type,
            min_battery_pct=min_battery_pct,
            require_online=require_online,
            shutdown_timeout_sec=shutdown_timeout_sec,
            restore_delay_sec=restore_delay_sec,
            notify_on_complete=notify_on_complete,
            notify_on_fail=notify_on_fail,
            started_at_iso=started_at,
        )
        if wait_for_completion:
            task = asyncio.create_task(coro, name=f"flow-drill-{report_id}")
            self._active_task = task
            return await task

        self._active_task = asyncio.create_task(coro, name=f"flow-drill-{report_id}")
        return initial_report

    async def abort_drill(self, report_id: int) -> FlowDrillReportRecord | None:
        existing = await self.get_report(report_id)
        if existing is None:
            return None
        if (
            self._active_run_id == report_id
            and self._active_task is not None
            and not self._active_task.done()
        ):
            self._abort_event.set()
            try:
                await asyncio.wait_for(asyncio.shield(self._active_task), timeout=3.0)
            except asyncio.TimeoutError:
                self._active_task.cancel()
                try:
                    await self._active_task
                except (asyncio.CancelledError, Exception):
                    pass
        return await self.get_report(report_id)

    async def _execute_drill(
        self,
        *,
        report_id: int,
        hosts: list[dict[str, Any]],
        mode: FlowDrillMode,
        trigger_type: str,
        min_battery_pct: float,
        require_online: bool,
        shutdown_timeout_sec: int,
        restore_delay_sec: int,
        notify_on_complete: bool,
        notify_on_fail: bool,
        started_at_iso: str,
    ) -> FlowDrillReportRecord:
        started_dt = _parse_iso(started_at_iso) or datetime.now(timezone.utc)
        steps: list[FlowDrillStepEntry] = [
            FlowDrillStepEntry(
                stage="precheck",
                status="running",
                started_at=started_at_iso,
                message="Checking UPS status, battery charge, and host targets",
            )
        ]
        host_results: list[FlowDrillHostResult] = []
        touched_host_ids: set[int] = set()

        def finish_current_step(status: str, message: str) -> None:
            if steps:
                steps[-1].status = status  # type: ignore[assignment]
                steps[-1].completed_at = utc_now_iso()
                steps[-1].message = message

        try:
            cfg = await self.orchestrator.get_config()
            orch_state = await self.orchestrator.get_state()
            connected, primary_ups = self._pick_primary_ups(cfg.monitored_ups)
            ups_name = str(primary_ups.get("name") or "") if primary_ups else cfg.monitored_ups
            ups_status = str(primary_ups.get("status") or "UNKNOWN") if primary_ups else "DISCONNECTED"
            tokens = (
                set(primary_ups.get("status_tokens") or ups_status.split())
                if primary_ups
                else set()
            )
            battery_start = (
                float(primary_ups["battery_charge"])
                if primary_ups and primary_ups.get("battery_charge") is not None
                else None
            )

            await self._save_report_state(
                report_id,
                ups_name=ups_name,
                ups_status_start=ups_status,
                battery_start=battery_start,
            )

            # --- Stage 1: Safety Pre-Checks ---
            skip_reason: str | None = None
            if orch_state.state != "NORMAL":
                skip_reason = f"Orchestrator is currently in '{orch_state.state}' state (must be NORMAL)"
            elif not hosts:
                skip_reason = "No enabled target hosts selected for flow drill"
            elif require_online:
                if not connected or primary_ups is None:
                    skip_reason = "UPS is unreachable in live telemetry"
                elif (
                    primary_ups.get("node_online") is False
                    or "OFFLINE" in tokens
                    or "NOCOMM" in tokens
                    or "COMMBAD" in tokens
                    or "OB" in tokens
                    or "OL" not in tokens
                ):
                    skip_reason = f"UPS '{ups_name}' is not cleanly online (status={ups_status})"

            if skip_reason is None and min_battery_pct > 0:
                if battery_start is None or battery_start < min_battery_pct:
                    skip_reason = (
                        f"UPS '{ups_name}' battery {battery_start}% is below required minimum {min_battery_pct}%"
                    )

            if skip_reason is not None:
                now_iso = utc_now_iso()
                finish_current_step("skipped", skip_reason)
                dur = max(0, int((datetime.now(timezone.utc) - started_dt).total_seconds()))
                final = await self._save_report_state(
                    report_id,
                    status="skipped",
                    current_stage="precheck",
                    battery_end=battery_start,
                    completed_at=now_iso,
                    duration_sec=dur,
                    summary_message=f"Skipped flow drill: {skip_reason}",
                    steps=steps,
                    host_results=[],
                )
                await self.event_log.record(
                    "drill",
                    "warning",
                    "flow_drill_skipped",
                    final.summary_message,
                    {"report_id": report_id, "mode": mode, "ups_name": ups_name},
                )
                if notify_on_fail:
                    await self.dispatcher.dispatch_event(
                        "flow_drill_failed",
                        f"Flow Drill Skipped ({mode})",
                        final.summary_message,
                        {"report_id": report_id, "mode": mode},
                    )
                return final

            finish_current_step(
                "passed",
                f"Pre-checks passed ({len(hosts)} host(s), UPS={ups_name or 'N/A'}, battery={battery_start}%)",
            )

            nut_ips = self._connected_nut_ips()

            # --- Dry-Run Drill ---
            if mode == "dry_run":
                steps.append(
                    FlowDrillStepEntry(
                        stage="connectivity",
                        status="running",
                        started_at=utc_now_iso(),
                        message="Probing host reachability, NUT streams, SSH credentials & WoL MACs",
                    )
                )
                await self._save_report_state(
                    report_id, current_stage="connectivity", steps=steps
                )

                for h in hosts:
                    hid = int(h["id"])
                    hname = str(h["name"])
                    hip = str(h["ip_or_host"])
                    smode = str(h.get("shutdown_mode") or "nut_stream")
                    alive = await self.orchestrator.reachability_fn(
                        hip, str(h["check_method"]), h.get("check_port"), 2.0
                    )
                    nut_ok = hip.strip() in nut_ips
                    ssh_ok = bool(
                        str(h.get("ssh_username") or "").strip()
                        and str(h.get("ssh_password_enc") or "").strip()
                    )

                    mac_ok = True
                    mac_err = ""
                    try:
                        mac = normalize_mac(str(h["mac_address"]))
                        build_magic_packet(mac)
                    except Exception as exc:
                        mac_ok = False
                        mac_err = str(exc)

                    notes: list[str] = []
                    h_status = "passed"
                    if not mac_ok:
                        h_status = "failed"
                        notes.append(f"Invalid MAC ({mac_err})")
                    if smode == "disabled":
                        if h_status == "passed":
                            h_status = "warning"
                        notes.append("Shutdown disabled")
                    else:
                        if smode == "nut_stream" and not nut_ok:
                            if h_status == "passed":
                                h_status = "warning"
                            notes.append("NUT client not currently connected to Repeater")
                        if smode == "ssh" and not ssh_ok:
                            if h_status == "passed":
                                h_status = "warning"
                            notes.append("SSH shutdown credentials not saved")
                        if smode == "both" and not nut_ok and not ssh_ok:
                            if h_status == "passed":
                                h_status = "warning"
                            notes.append("Neither NUT client nor SSH credentials ready")

                    if not alive:
                        notes.append("Currently offline")

                    host_results.append(
                        FlowDrillHostResult(
                            host_id=hid,
                            host_name=hname,
                            ip_or_host=hip,
                            shutdown_mode=smode,
                            initial_online=alive,
                            nut_client_connected=nut_ok,
                            ssh_configured=ssh_ok,
                            status=h_status,  # type: ignore[arg-type]
                            message="; ".join(notes) if notes else "Ready for live shutdown & WoL restore",
                        )
                    )

                finish_current_step("passed", f"Probed {len(hosts)} target host(s)")
                steps.append(
                    FlowDrillStepEntry(
                        stage="shutdown_sim",
                        status="passed",
                        started_at=utc_now_iso(),
                        completed_at=utc_now_iso(),
                        message="Evaluated per-host NUT stream & SSH shutdown triggers (dry-run)",
                    )
                )
                steps.append(
                    FlowDrillStepEntry(
                        stage="wol_sim",
                        status="passed",
                        started_at=utc_now_iso(),
                        completed_at=utc_now_iso(),
                        message="Verified WoL magic packet payloads & boot priority ordering (dry-run)",
                    )
                )

                h_failed = sum(1 for r in host_results if r.status == "failed")
                h_warn = sum(1 for r in host_results if r.status == "warning")
                h_passed = sum(1 for r in host_results if r.status == "passed")
                overall = "failed" if h_failed > 0 else ("warning" if h_warn > 0 else "passed")
                now_iso = utc_now_iso()
                dur = max(0, int((datetime.now(timezone.utc) - started_dt).total_seconds()))
                summary_msg = (
                    f"Dry-run drill finished: {h_passed} ready, {h_warn} warning(s), {h_failed} failed"
                )
                _, end_ups = self._pick_primary_ups(cfg.monitored_ups)
                battery_end = (
                    float(end_ups["battery_charge"])
                    if end_ups and end_ups.get("battery_charge") is not None
                    else battery_start
                )
                final = await self._save_report_state(
                    report_id,
                    status=overall,
                    current_stage="complete",
                    battery_end=battery_end,
                    hosts_passed=h_passed,
                    hosts_failed=h_failed,
                    completed_at=now_iso,
                    duration_sec=dur,
                    summary_message=summary_msg,
                    steps=steps,
                    host_results=host_results,
                )
                await self.event_log.record(
                    "drill",
                    "error" if overall == "failed" else ("warning" if overall == "warning" else "success"),
                    "flow_drill_completed" if overall != "failed" else "flow_drill_failed",
                    summary_msg,
                    {"report_id": report_id, "mode": mode, "status": overall},
                )
                if overall == "failed" and notify_on_fail:
                    await self.dispatcher.dispatch_event(
                        "flow_drill_failed",
                        "Flow Drill Dry-Run Failed",
                        summary_msg,
                        {"report_id": report_id, "mode": mode},
                    )
                elif overall != "failed" and notify_on_complete:
                    await self.dispatcher.dispatch_event(
                        "flow_drill_completed",
                        f"Flow Drill Dry-Run ({overall.upper()})",
                        summary_msg,
                        {"report_id": report_id, "mode": mode},
                    )
                return final

            # --- Live End-to-End Drill ---
            steps.append(
                FlowDrillStepEntry(
                    stage="shutdown_trigger",
                    status="running",
                    started_at=utc_now_iso(),
                    message=f"Triggering graceful shutdown on {len(hosts)} host(s)",
                )
            )
            await self._save_report_state(
                report_id, current_stage="shutdown_trigger", steps=steps
            )

            host_map: dict[int, dict[str, Any]] = {int(h["id"]): h for h in hosts}
            pending_offline: dict[int, datetime] = {}

            for h in hosts:
                hid = int(h["id"])
                hname = str(h["name"])
                hip = str(h["ip_or_host"])
                smode = str(h.get("shutdown_mode") or "nut_stream")
                alive = await self.orchestrator.reachability_fn(
                    hip, str(h["check_method"]), h.get("check_port"), 2.0
                )
                nut_ok = hip.strip() in nut_ips
                ssh_ok = bool(
                    str(h.get("ssh_username") or "").strip()
                    and str(h.get("ssh_password_enc") or "").strip()
                )
                hr = FlowDrillHostResult(
                    host_id=hid,
                    host_name=hname,
                    ip_or_host=hip,
                    shutdown_mode=smode,
                    initial_online=alive,
                    nut_client_connected=nut_ok,
                    ssh_configured=ssh_ok,
                    status="passed",
                )
                if not alive:
                    hr.status = "skipped"
                    hr.message = "Host was already offline before drill started"
                elif smode == "disabled":
                    hr.status = "warning"
                    hr.message = "Shutdown disabled on host; skipped"
                else:
                    triggered = False
                    msgs: list[str] = []
                    if smode in ("nut_stream", "both"):
                        self.orchestrator.set_drill_host_override(hid, "OB LB FSD", "flow_drill")
                        touched_host_ids.add(hid)
                        triggered = True
                        msgs.append("Streamed OB LB FSD")
                    if smode in ("ssh", "both"):
                        ssh_user = str(h.get("ssh_username") or "").strip()
                        ssh_pw_enc = str(h.get("ssh_password_enc") or "")
                        ssh_sudo_enc = str(h.get("ssh_sudo_password_enc") or "")
                        sbox = getattr(self.orchestrator, "secret_box", None)
                        if ssh_user and ssh_pw_enc and sbox is not None:
                            pw = sbox.decrypt(ssh_pw_enc)
                            sudo_pw = sbox.decrypt(ssh_sudo_enc)
                            ok_ssh, detail_ssh = await self.orchestrator.ssh_shutdown_fn(
                                hip,
                                int(h.get("ssh_port") or 22),
                                ssh_user,
                                pw,
                                sudo_pw,
                                str(h.get("ssh_shutdown_cmd") or "sudo -S shutdown -h now"),
                            )
                            if ok_ssh:
                                triggered = True
                                msgs.append("SSH shutdown sent")
                            else:
                                msgs.append(f"SSH error: {detail_ssh}")
                        else:
                            msgs.append("Missing SSH credentials")

                    hr.shutdown_triggered = triggered
                    if triggered:
                        pending_offline[hid] = datetime.now(timezone.utc)
                        hr.message = "; ".join(msgs)
                    else:
                        hr.status = "failed"
                        hr.message = "; ".join(msgs) or "No shutdown trigger available"

                host_results.append(hr)

            if touched_host_ids:
                await self.orchestrator.sync_repeater_now()

            finish_current_step(
                "passed",
                f"Triggered shutdown on {len(pending_offline)} online host(s)",
            )

            # Stage 3: Verify hosts power off
            if pending_offline:
                steps.append(
                    FlowDrillStepEntry(
                        stage="verify_offline",
                        status="running",
                        started_at=utc_now_iso(),
                        message=f"Waiting up to {shutdown_timeout_sec}s for {len(pending_offline)} host(s) to go offline",
                    )
                )
                await self._save_report_state(
                    report_id,
                    current_stage="verify_offline",
                    steps=steps,
                    host_results=host_results,
                )

                sd_start = datetime.now(timezone.utc)
                while pending_offline:
                    if self._abort_event.is_set() or self._stop_event.is_set():
                        raise asyncio.CancelledError()

                    for hid in list(pending_offline.keys()):
                        h = host_map[hid]
                        alive = await self.orchestrator.reachability_fn(
                            str(h["ip_or_host"]),
                            str(h["check_method"]),
                            h.get("check_port"),
                            2.0,
                        )
                        if not alive:
                            t0 = pending_offline.pop(hid)
                            dur_sd = max(0, int((datetime.now(timezone.utc) - t0).total_seconds()))
                            for hr in host_results:
                                if hr.host_id == hid:
                                    hr.shutdown_verified_offline = True
                                    hr.shutdown_duration_sec = dur_sd
                                    break
                            self.orchestrator.clear_drill_host_override(hid)
                            await self.orchestrator.sync_repeater_now()

                    if not pending_offline:
                        break
                    elapsed_sd = (datetime.now(timezone.utc) - sd_start).total_seconds()
                    if elapsed_sd >= float(shutdown_timeout_sec):
                        break
                    await self._save_report_state(report_id, host_results=host_results)
                    if await self._sleep_interruptible(self.check_step_sec):
                        raise asyncio.CancelledError()

                # Mark any hosts that failed to shut down
                for hid in list(pending_offline.keys()):
                    self.orchestrator.clear_drill_host_override(hid)
                    for hr in host_results:
                        if hr.host_id == hid:
                            hr.status = "failed"
                            hr.message = f"Host remained online after {shutdown_timeout_sec}s shutdown timeout"
                            break
                await self.orchestrator.sync_repeater_now()

                off_cnt = sum(1 for hr in host_results if hr.shutdown_verified_offline)
                finish_current_step(
                    "failed" if pending_offline else "passed",
                    f"{off_cnt} host(s) verified offline"
                    + (f", {len(pending_offline)} timed out" if pending_offline else ""),
                )

            # Stage 4: Restore Hold before WoL
            wake_targets = [hr for hr in host_results if hr.shutdown_verified_offline]
            if wake_targets and restore_delay_sec > 0:
                steps.append(
                    FlowDrillStepEntry(
                        stage="restore_hold",
                        status="running",
                        started_at=utc_now_iso(),
                        message=f"Holding {restore_delay_sec}s before sending Wake-on-LAN",
                    )
                )
                await self._save_report_state(
                    report_id,
                    current_stage="restore_hold",
                    steps=steps,
                    host_results=host_results,
                )
                if await self._sleep_interruptible(float(restore_delay_sec)):
                    raise asyncio.CancelledError()
                finish_current_step("passed", f"Held {restore_delay_sec}s for NIC settle")

            # Stage 5: Staggered WoL Restore & Verification
            if wake_targets:
                steps.append(
                    FlowDrillStepEntry(
                        stage="wol_restore",
                        status="running",
                        started_at=utc_now_iso(),
                        message=f"Restoring {len(wake_targets)} host(s) via Wake-on-LAN",
                    )
                )
                await self._save_report_state(
                    report_id,
                    current_stage="wol_restore",
                    steps=steps,
                    host_results=host_results,
                )

                for idx, hr in enumerate(wake_targets):
                    if self._abort_event.is_set() or self._stop_event.is_set():
                        raise asyncio.CancelledError()
                    h = host_map[hr.host_id]
                    if idx > 0:
                        stagger = max(0, int(h.get("stagger_delay_sec") or 0))
                        if stagger > 0 and await self._sleep_interruptible(float(stagger)):
                            raise asyncio.CancelledError()

                    mac = str(h["mac_address"])
                    bcast = h.get("subnet_broadcast")
                    max_retries = max(1, int(h.get("max_retries") or 1))
                    boot_timeout = max(0, int(h.get("boot_timeout_sec") or 90))
                    retry_interval = max(0, int(h.get("retry_interval_sec") or 0))
                    wake_t0 = datetime.now(timezone.utc)

                    for attempt in range(1, max_retries + 1):
                        if self._abort_event.is_set() or self._stop_event.is_set():
                            raise asyncio.CancelledError()
                        try:
                            self.orchestrator.wol_sender(mac, bcast)
                            hr.wake_triggered = True
                            hr.wake_attempts = attempt
                        except Exception as exc:
                            hr.status = "failed"
                            hr.message = f"WoL send error: {exc}"
                            break

                        loop_start = datetime.now(timezone.utc)
                        while True:
                            if self._abort_event.is_set() or self._stop_event.is_set():
                                raise asyncio.CancelledError()
                            alive = await self.orchestrator.reachability_fn(
                                str(h["ip_or_host"]),
                                str(h["check_method"]),
                                h.get("check_port"),
                                2.0,
                            )
                            if alive:
                                now_w = utc_now_iso()
                                hr.wake_verified_online = True
                                hr.wake_duration_sec = max(
                                    0, int((datetime.now(timezone.utc) - wake_t0).total_seconds())
                                )
                                hr.status = "passed"
                                hr.message = f"Shutdown & WoL restore verified (attempt {attempt}/{max_retries})"
                                await self.db.execute(
                                    "UPDATE hosts SET is_online = 1, last_seen_at = ?, updated_at = ? WHERE id = ?",
                                    (now_w, now_w, hr.host_id),
                                )
                                break
                            elapsed_b = (datetime.now(timezone.utc) - loop_start).total_seconds()
                            if elapsed_b >= float(boot_timeout):
                                break
                            if await self._sleep_interruptible(self.check_step_sec):
                                raise asyncio.CancelledError()

                        if hr.wake_verified_online:
                            break
                        if attempt < max_retries and retry_interval > 0:
                            if await self._sleep_interruptible(float(retry_interval)):
                                raise asyncio.CancelledError()

                    if not hr.wake_verified_online and hr.status != "failed":
                        hr.status = "failed"
                        hr.message = f"Host did not come back online after {max_retries} WoL attempt(s)"
                    await self._save_report_state(report_id, host_results=host_results)

                woke_cnt = sum(1 for hr in wake_targets if hr.wake_verified_online)
                finish_current_step(
                    "passed" if woke_cnt == len(wake_targets) else "failed",
                    f"{woke_cnt}/{len(wake_targets)} host(s) restored online",
                )

            h_failed = sum(1 for hr in host_results if hr.status == "failed")
            h_warn = sum(1 for hr in host_results if hr.status == "warning")
            h_passed = sum(1 for hr in host_results if hr.status == "passed")
            overall = "failed" if h_failed > 0 else ("warning" if h_warn > 0 else "passed")
            now_iso = utc_now_iso()
            dur = max(0, int((datetime.now(timezone.utc) - started_dt).total_seconds()))
            _, end_ups = self._pick_primary_ups(cfg.monitored_ups)
            battery_end = (
                float(end_ups["battery_charge"])
                if end_ups and end_ups.get("battery_charge") is not None
                else battery_start
            )
            summary_msg = (
                f"Live flow drill finished: {h_passed} passed, {h_warn} warning(s), {h_failed} failed ({dur}s)"
            )
            final = await self._save_report_state(
                report_id,
                status=overall,
                current_stage="complete",
                battery_end=battery_end,
                hosts_passed=h_passed,
                hosts_failed=h_failed,
                completed_at=now_iso,
                duration_sec=dur,
                summary_message=summary_msg,
                steps=steps,
                host_results=host_results,
            )
            await self.event_log.record(
                "drill",
                "error" if overall == "failed" else ("warning" if overall == "warning" else "success"),
                "flow_drill_completed" if overall != "failed" else "flow_drill_failed",
                summary_msg,
                {"report_id": report_id, "mode": mode, "status": overall, "duration_sec": dur},
            )
            if overall == "failed" and notify_on_fail:
                await self.dispatcher.dispatch_event(
                    "flow_drill_failed",
                    "Live Flow Drill Failed",
                    summary_msg,
                    {"report_id": report_id, "mode": mode},
                )
            elif overall != "failed" and notify_on_complete:
                await self.dispatcher.dispatch_event(
                    "flow_drill_completed",
                    f"Live Flow Drill ({overall.upper()})",
                    summary_msg,
                    {"report_id": report_id, "mode": mode},
                )
            return final

        except asyncio.CancelledError:
            finish_current_step("aborted", "Flow drill aborted")
            for hr in host_results:
                if hr.status == "passed" and hr.shutdown_triggered and not hr.wake_verified_online:
                    hr.status = "aborted"
                    hr.message = "Aborted mid-drill"
            now_iso = utc_now_iso()
            dur = max(0, int((datetime.now(timezone.utc) - started_dt).total_seconds()))
            final = await self._save_report_state(
                report_id,
                status="aborted",
                current_stage="aborted",
                hosts_passed=sum(1 for r in host_results if r.status == "passed"),
                hosts_failed=sum(1 for r in host_results if r.status == "failed"),
                completed_at=now_iso,
                duration_sec=dur,
                summary_message="Flow drill aborted; all NUT shutdown overrides cleared",
                steps=steps,
                host_results=host_results,
            )
            await self.event_log.record(
                "drill",
                "warning",
                "flow_drill_aborted",
                final.summary_message,
                {"report_id": report_id, "mode": mode},
            )
            return final
        except Exception as exc:
            logger.exception("Flow drill %s failed with exception: %s", report_id, exc)
            finish_current_step("failed", str(exc))
            now_iso = utc_now_iso()
            dur = max(0, int((datetime.now(timezone.utc) - started_dt).total_seconds()))
            final = await self._save_report_state(
                report_id,
                status="failed",
                current_stage="complete",
                completed_at=now_iso,
                duration_sec=dur,
                summary_message=f"Flow drill error: {exc}",
                steps=steps,
                host_results=host_results,
            )
            if notify_on_fail:
                await self.dispatcher.dispatch_event(
                    "flow_drill_failed",
                    "Flow Drill Error",
                    final.summary_message,
                    {"report_id": report_id, "mode": mode},
                )
            return final
        finally:
            for hid in touched_host_ids:
                self.orchestrator.clear_drill_host_override(hid)
            if touched_host_ids:
                try:
                    await self.orchestrator.sync_repeater_now()
                except Exception:
                    pass
            if self._active_run_id == report_id:
                self._active_run_id = None
                self._active_task = None

    # --- Scheduler Loop ---

    async def process_due_schedules(self) -> int:
        now_dt = datetime.now(timezone.utc)
        rows = await self.db.fetch_all(
            "SELECT * FROM flow_drill_schedules WHERE enabled = 1 ORDER BY id ASC"
        )
        launched = 0
        for row in rows:
            sched = _row_to_schedule(row)
            if not sched.next_run_at:
                next_ts = compute_next_run(
                    sched.frequency,
                    sched.day_of_week,
                    sched.day_of_month,
                    sched.time_hhmm,
                    after_dt=now_dt,
                )
                await self.db.execute(
                    "UPDATE flow_drill_schedules SET next_run_at = ?, updated_at = ? WHERE id = ?",
                    (next_ts, now_dt.isoformat(), sched.id),
                )
                continue

            due_dt = _parse_iso(sched.next_run_at)
            if due_dt is None or due_dt > now_dt:
                continue

            next_ts = compute_next_run(
                sched.frequency,
                sched.day_of_week,
                sched.day_of_month,
                sched.time_hhmm,
                after_dt=now_dt,
            )
            await self.db.execute(
                "UPDATE flow_drill_schedules SET last_run_at = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
                (now_dt.isoformat(), next_ts, now_dt.isoformat(), sched.id),
            )
            try:
                await self.start_drill(
                    mode=sched.mode,
                    target_scope=sched.target_scope,
                    host_ids=sched.host_ids,
                    min_battery_pct=sched.min_battery_pct,
                    require_online=sched.require_online,
                    shutdown_timeout_sec=sched.shutdown_timeout_sec,
                    restore_delay_sec=sched.restore_delay_sec,
                    notify_on_complete=sched.notify_on_complete,
                    notify_on_fail=sched.notify_on_fail,
                    confirm_live=True,
                    trigger_type="scheduled",
                    schedule_id=sched.id,
                    schedule_name=sched.name,
                )
                launched += 1
            except RuntimeError as exc:
                logger.warning(
                    "Skipped scheduled flow drill '%s': %s", sched.name, exc
                )
        return launched

    async def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.process_due_schedules()
            except Exception as exc:
                logger.exception("FlowDrillScheduler loop failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_sec)
            except asyncio.TimeoutError:
                pass
