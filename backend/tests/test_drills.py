"""Tests for end-to-end flow drills (dry-run, live shutdown->restore, abort, schedules & API)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.drills import router as drills_router
from app.database import Database, utc_now_iso
from app.models.schemas import FlowDrillScheduleCreate
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.nut.repeater import NutRepeaterServer
from app.services.drill_scheduler import FlowDrillScheduler
from app.services.event_log import EventLogService
from app.services.ws_hub import WebSocketHub
from app.wol.orchestrator import RecoveryOrchestrator


def _online_snapshot(status: str = "OL", charge: float = 100.0) -> dict:
    return {
        "connected": True,
        "error": None,
        "updated_at": utc_now_iso(),
        "ups_list": [
            {
                "name": "ups1",
                "description": "Main UPS",
                "status": status,
                "status_tokens": status.split(),
                "battery_charge": charge,
                "battery_runtime": 1800.0,
                "ups_load": 20.0,
                "raw_vars": {"ups.status": status},
            }
        ],
    }


async def _seed_host(
    db: Database,
    *,
    name: str,
    ip: str,
    mac: str,
    virtual_ups_name: str,
    shutdown_mode: str = "nut_stream",
) -> int:
    now = utc_now_iso()
    return await db.execute(
        """
        INSERT INTO hosts (
            name, ip_or_host, mac_address, virtual_ups_name, shutdown_mode,
            boot_priority, stagger_delay_sec, check_method,
            boot_timeout_sec, retry_interval_sec, max_retries,
            enabled, is_online, was_online_before_outage, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 1, 0, 'icmp', 1, 0, 2, 1, 1, 1, ?, ?)
        """,
        (name, ip, mac, virtual_ups_name, shutdown_mode, now, now),
    )


@pytest.mark.asyncio
async def test_dry_run_drill_checks_readiness_and_precheck_skips(
    db: Database, event_log: EventLogService
) -> None:
    await _seed_host(
        db,
        name="pve1",
        ip="10.0.0.11",
        mac="AA:BB:CC:00:00:11",
        virtual_ups_name="pve_1",
        shutdown_mode="nut_stream",
    )
    await _seed_host(
        db,
        name="nas1",
        ip="10.0.0.12",
        mac="AA:BB:CC:00:00:12",
        virtual_ups_name="nas_1",
        shutdown_mode="disabled",
    )

    async def fake_reach(_ip: str, _m: str, _p: int | None, _t: float) -> bool:
        return True

    dispatcher = NotificationDispatcher(db, event_log)
    dispatcher.dispatch_event = AsyncMock()  # type: ignore[method-assign]
    repeater = NutRepeaterServer(bind_host="127.0.0.1", port=0)
    orch = RecoveryOrchestrator(
        db,
        NutTcpClient(),
        dispatcher,
        event_log,
        reachability_fn=fake_reach,
        repeater=repeater,
    )
    await orch.evaluate_telemetry(_online_snapshot("OL", charge=95.0))

    scheduler = FlowDrillScheduler(
        db, orch, dispatcher, event_log, check_step_sec=0.01
    )

    # 1. Dry-run drill completes with warning (no connected NUT clients & disabled host)
    rep = await scheduler.start_drill(
        mode="dry_run",
        target_scope="all",
        min_battery_pct=80.0,
        require_online=True,
        wait_for_completion=True,
    )
    assert rep.status == "warning"
    assert rep.hosts_total == 2
    assert len(rep.steps) == 4
    assert len(rep.host_results) == 2
    dispatcher.dispatch_event.assert_called()

    # 2. Pre-check skip when battery is below minimum
    await orch.evaluate_telemetry(_online_snapshot("OL", charge=40.0))
    skipped = await scheduler.start_drill(
        mode="dry_run",
        target_scope="all",
        min_battery_pct=80.0,
        require_online=True,
        wait_for_completion=True,
    )
    assert skipped.status == "skipped"
    assert "below required minimum" in skipped.summary_message


@pytest.mark.asyncio
async def test_live_full_flow_drill_shutdown_verify_and_wol_restore(
    db: Database, event_log: EventLogService
) -> None:
    hid = await _seed_host(
        db,
        name="proxmox",
        ip="10.0.0.21",
        mac="AA:BB:CC:00:00:21",
        virtual_ups_name="pve_main",
        shutdown_mode="nut_stream",
    )

    repeater = NutRepeaterServer(bind_host="127.0.0.1", port=0)
    await repeater.start()
    try:
        nut_client = NutTcpClient(host="127.0.0.1", port=repeater.port)
        dispatcher = NotificationDispatcher(db, event_log)
        dispatcher.dispatch_event = AsyncMock()  # type: ignore[method-assign]

        host_online = {"state": True}
        sent_wol: list[str] = []

        async def fake_reach(ip: str, _m: str, _p: int | None, _t: float) -> bool:
            if ip == "10.0.0.21":
                # Simulate server shutting down once virtual UPS streams OB LB FSD
                resolved = repeater._resolve_ups("pve_main")
                if resolved and resolved[1].get("ups.status") == "OB LB FSD":
                    host_online["state"] = False
                return host_online["state"]
            return False

        def fake_wol(mac: str, _bcast: str | None) -> list[str]:
            sent_wol.append(mac)
            host_online["state"] = True
            return ["255.255.255.255:9"]

        orch = RecoveryOrchestrator(
            db,
            nut_client,
            dispatcher,
            event_log,
            reachability_fn=fake_reach,
            wol_sender=fake_wol,
            repeater=repeater,
            check_poll_step=0.01,
        )
        await orch.evaluate_telemetry(_online_snapshot("OL", charge=100.0))

        scheduler = FlowDrillScheduler(
            db, orch, dispatcher, event_log, check_step_sec=0.01
        )
        # Live drill without confirm_live=True must fail validation
        with pytest.raises(ValueError):
            await scheduler.start_drill(mode="live", confirm_live=False)

        rep = await scheduler.start_drill(
            mode="live",
            target_scope="selected",
            host_ids=[hid],
            min_battery_pct=80.0,
            require_online=True,
            shutdown_timeout_sec=10,
            restore_delay_sec=0,
            confirm_live=True,
            wait_for_completion=True,
        )

        assert rep.status == "passed"
        assert rep.hosts_passed == 1
        assert rep.hosts_failed == 0
        assert sent_wol == ["AA:BB:CC:00:00:21"]
        hr = rep.host_results[0]
        assert hr.shutdown_triggered is True
        assert hr.shutdown_verified_offline is True
        assert hr.wake_triggered is True
        assert hr.wake_verified_online is True
        # Drill override MUST be cleared back to OL after completion
        assert hid not in orch._drill_host_overrides
        assert repeater._resolve_ups("pve_main")[1]["ups.status"] == "OL"

        summary = (await scheduler.list_reports()).summary
        assert summary.total == 1
        assert summary.passed == 1
        assert summary.pass_rate_pct == 100.0
    finally:
        await repeater.stop()


@pytest.mark.asyncio
async def test_live_drill_abort_clears_overrides(
    db: Database, event_log: EventLogService
) -> None:
    hid = await _seed_host(
        db,
        name="stuck-host",
        ip="10.0.0.22",
        mac="AA:BB:CC:00:00:22",
        virtual_ups_name="stuck_host",
        shutdown_mode="nut_stream",
    )

    repeater = NutRepeaterServer(bind_host="127.0.0.1", port=0)
    await repeater.start()
    try:
        dispatcher = NotificationDispatcher(db, event_log)
        dispatcher.dispatch_event = AsyncMock()  # type: ignore[method-assign]

        async def always_online(_ip: str, _m: str, _p: int | None, _t: float) -> bool:
            return True

        orch = RecoveryOrchestrator(
            db,
            NutTcpClient(),
            dispatcher,
            event_log,
            reachability_fn=always_online,
            repeater=repeater,
        )
        await orch.evaluate_telemetry(_online_snapshot("OL", charge=100.0))

        scheduler = FlowDrillScheduler(
            db, orch, dispatcher, event_log, check_step_sec=0.05
        )
        rep = await scheduler.start_drill(
            mode="live",
            target_scope="selected",
            host_ids=[hid],
            shutdown_timeout_sec=30,
            restore_delay_sec=5,
            confirm_live=True,
            wait_for_completion=False,
        )
        await asyncio.sleep(0.1)
        assert hid in orch._drill_host_overrides

        aborted = await scheduler.abort_drill(rep.id)
        assert aborted is not None
        assert aborted.status == "aborted"
        assert hid not in orch._drill_host_overrides
        assert repeater._resolve_ups("stuck_host")[1]["ups.status"] == "OL"
    finally:
        await repeater.stop()


@pytest.mark.asyncio
async def test_drill_schedules_and_api_endpoints(
    db: Database, event_log: EventLogService
) -> None:
    await _seed_host(
        db,
        name="lab",
        ip="10.0.0.31",
        mac="AA:BB:CC:00:00:31",
        virtual_ups_name="lab_host",
    )

    async def fake_reach(_ip: str, _m: str, _p: int | None, _t: float) -> bool:
        return True

    dispatcher = NotificationDispatcher(db, event_log)
    dispatcher.dispatch_event = AsyncMock()  # type: ignore[method-assign]
    orch = RecoveryOrchestrator(
        db,
        NutTcpClient(),
        dispatcher,
        event_log,
        reachability_fn=fake_reach,
    )
    await orch.evaluate_telemetry(_online_snapshot("OL", charge=90.0))

    scheduler = FlowDrillScheduler(
        db, orch, dispatcher, event_log, ws_hub=WebSocketHub(), check_step_sec=0.01
    )

    # Due schedule triggers automatically in process_due_schedules
    sched = await scheduler.create_schedule(
        FlowDrillScheduleCreate(
            name="Nightly Readiness",
            mode="dry_run",
            target_scope="all",
            frequency="daily",
            time_hhmm="03:00",
        )
    )
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    await db.execute("UPDATE flow_drill_schedules SET next_run_at = ? WHERE id = ?", (past, sched.id))
    launched = await scheduler.process_due_schedules()
    assert launched == 1
    if scheduler._active_task is not None:
        await scheduler._active_task

    app = FastAPI()
    app.include_router(drills_router)
    app.state.drill_scheduler = scheduler

    with TestClient(app) as client:
        assert client.get("/api/drills/schedules").status_code == 200
        reps = client.get("/api/drills/reports").json()
        assert reps["summary"]["total"] == 1
        assert reps["reports"][0]["trigger_type"] == "scheduled"

        # Invalid live run without confirm_live -> 422
        bad = client.post("/api/drills/run", json={"mode": "live", "confirm_live": False})
        assert bad.status_code == 422

        cleared = client.delete("/api/drills/reports")
        assert cleared.status_code == 200
        assert cleared.json()["cleared"] == 1
