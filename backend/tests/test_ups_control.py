"""Tests for NUT RFC 9271 control methods, Migration v9, SelfTestScheduler, and /api/control API."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.api.control import router as control_router
from app.database import utc_now_iso
from app.models.schemas import SelfTestScheduleCreate
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient, _validate_nut_token
from app.services.event_log import EventLogService
from app.services.selftest_scheduler import (
    SelfTestScheduler,
    compute_next_run,
    resolve_control_credentials,
)


class _MockNutServer:
    """Minimal async RFC 9271 mock upsd server for testing commands and writable vars."""

    def __init__(self) -> None:
        self.server: asyncio.AbstractServer | None = None
        self.port: int = 0
        self.ups_status: str = "OL"
        self.battery_charge: str = "100"
        self.battery_runtime: str = "2400"
        self.ups_load: str = "25"
        self.ups_test_result: str = "No test initiated"
        self.writable_vars: dict[str, str] = {
            "battery.charge.low": "20",
            "ups.delay.shutdown": "30",
            "ups.beeper.status": "enabled",
        }
        self.commands: list[tuple[str, str]] = [
            ("test.battery.start.quick", "Start a quick battery test"),
            ("test.battery.start.deep", "Start a deep battery test"),
            ("test.battery.stop", "Stop the battery test"),
            ("beeper.toggle", "Toggle the UPS beeper"),
            ("test.panel.start", "Start testing the UPS panel"),
            ("load.off", "Turn off the load immediately"),
            ("shutdown.return", "Turn off the load and return when power is back"),
        ]
        self.expected_user = "admin"
        self.expected_pass = "s3cr3tPass!"
        self.received_commands: list[str] = []
        self.on_instcmd: list[str] = []

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        sock = self.server.sockets[0]
        self.port = int(sock.getsockname()[1])
        return self.port

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        authed_user = ""
        authed_ok = False
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                self.received_commands.append(line)

                if line == "LOGOUT":
                    writer.write(b"OK Goodbye\n")
                    await writer.drain()
                    break

                if line == "LIST UPS":
                    writer.write(b'BEGIN LIST UPS\nUPS main_ups "Main Rack UPS"\nEND LIST UPS\n')
                elif line == "LIST VAR main_ups":
                    lines = [
                        "BEGIN LIST VAR main_ups",
                        f'VAR main_ups ups.status "{self.ups_status}"',
                        f'VAR main_ups battery.charge "{self.battery_charge}"',
                        f'VAR main_ups battery.runtime "{self.battery_runtime}"',
                        f'VAR main_ups ups.load "{self.ups_load}"',
                        f'VAR main_ups ups.test.result "{self.ups_test_result}"',
                    ]
                    for k, v in self.writable_vars.items():
                        lines.append(f'VAR main_ups {k} "{v}"')
                    lines.append("END LIST VAR main_ups")
                    writer.write(("\n".join(lines) + "\n").encode("utf-8"))
                elif line == "LIST CLIENT main_ups":
                    writer.write(b"BEGIN LIST CLIENT main_ups\nEND LIST CLIENT main_ups\n")
                elif line == "LIST CMD main_ups":
                    lines = ["BEGIN LIST CMD main_ups"]
                    for cname, _ in self.commands:
                        lines.append(f"CMD main_ups {cname}")
                    lines.append("END LIST CMD main_ups")
                    writer.write(("\n".join(lines) + "\n").encode("utf-8"))
                elif line.startswith("GET CMDDESC main_ups "):
                    cname = line.split(" ", 3)[3]
                    desc = next((d for c, d in self.commands if c == cname), "")
                    if desc:
                        writer.write(f'CMDDESC main_ups {cname} "{desc}"\n'.encode("utf-8"))
                    else:
                        writer.write(b"ERR CMD-NOT-SUPPORTED\n")
                elif line == "LIST RW main_ups":
                    lines = ["BEGIN LIST RW main_ups"]
                    for k, v in self.writable_vars.items():
                        lines.append(f'RW main_ups {k} "{v}"')
                    lines.append("END LIST RW main_ups")
                    writer.write(("\n".join(lines) + "\n").encode("utf-8"))
                elif line.startswith("GET DESC main_ups "):
                    vname = line.split(" ", 3)[3]
                    writer.write(f'DESC main_ups {vname} "Description for {vname}"\n'.encode("utf-8"))
                elif line.startswith("GET TYPE main_ups "):
                    vname = line.split(" ", 3)[3]
                    if vname == "ups.beeper.status":
                        writer.write(f"TYPE main_ups {vname} ENUM\n".encode("utf-8"))
                    elif vname == "battery.charge.low":
                        writer.write(f"TYPE main_ups {vname} RANGE\n".encode("utf-8"))
                    else:
                        writer.write(f"TYPE main_ups {vname} STRING:10\n".encode("utf-8"))
                elif line.startswith("LIST ENUM main_ups "):
                    vname = line.split(" ", 3)[3]
                    if vname == "ups.beeper.status":
                        writer.write(
                            (
                                f"BEGIN LIST ENUM main_ups {vname}\n"
                                f'ENUM main_ups {vname} "enabled"\n'
                                f'ENUM main_ups {vname} "disabled"\n'
                                f'ENUM main_ups {vname} "muted"\n'
                                f"END LIST ENUM main_ups {vname}\n"
                            ).encode("utf-8")
                        )
                    else:
                        writer.write(b"ERR VAR-NOT-SUPPORTED\n")
                elif line.startswith("LIST RANGE main_ups "):
                    vname = line.split(" ", 3)[3]
                    if vname == "battery.charge.low":
                        writer.write(
                            (
                                f"BEGIN LIST RANGE main_ups {vname}\n"
                                f'RANGE main_ups {vname} "10" "90"\n'
                                f"END LIST RANGE main_ups {vname}\n"
                            ).encode("utf-8")
                        )
                    else:
                        writer.write(b"ERR VAR-NOT-SUPPORTED\n")
                elif line.startswith("USERNAME "):
                    authed_user = line.split(" ", 1)[1].strip('"')
                    writer.write(b"OK\n")
                elif line.startswith("PASSWORD "):
                    pwd = line.split(" ", 1)[1].strip('"')
                    if authed_user == self.expected_user and pwd == self.expected_pass:
                        authed_ok = True
                        writer.write(b"OK\n")
                    else:
                        writer.write(b"ERR ACCESS-DENIED\n")
                elif line.startswith("INSTCMD main_ups "):
                    if not authed_ok:
                        writer.write(b"ERR ACCESS-DENIED\n")
                    else:
                        cmd_name = line.split(" ", 2)[2]
                        self.on_instcmd.append(cmd_name)
                        if cmd_name == "test.battery.start.quick":
                            self.ups_status = "OL CAL"
                            self.ups_test_result = "In progress"
                        writer.write(b"OK\n")
                elif line.startswith("SET VAR main_ups "):
                    if not authed_ok:
                        writer.write(b"ERR ACCESS-DENIED\n")
                    else:
                        rest = line[len("SET VAR main_ups ") :]
                        vname, raw_val = rest.split(" ", 1)
                        val = raw_val.strip()
                        if val.startswith('"') and val.endswith('"'):
                            val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                        self.writable_vars[vname] = val
                        writer.write(b"OK\n")
                else:
                    writer.write(b"ERR UNKNOWN-COMMAND\n")
                await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class _StubNutManager:
    def __init__(self, users: list[dict] | None = None) -> None:
        self._users = users or []

    def read_config(self, filename: str, mask_secrets: bool = True):
        if filename == "upsd.users":
            return {"users": self._users}, ""
        return {}, ""


class _StubOrchestrator:
    def __init__(self, telemetry: dict) -> None:
        self.latest_telemetry = telemetry


@pytest.mark.asyncio
async def test_nut_tcp_client_control_methods_and_validation(caplog) -> None:
    for bad in ("", "ups 1", "ups\n1", "ups\r1", "ups\x00"):
        with pytest.raises(ValueError):
            _validate_nut_token(bad, "ups_name")

    srv = _MockNutServer()
    port = await srv.start()
    try:
        client = NutTcpClient(host="127.0.0.1", port=port, timeout=2.0)

        cmds = await client.list_commands("main_ups")
        by_name = {c["name"]: c for c in cmds}
        assert by_name["test.battery.start.quick"]["risk"] == "safe"
        assert by_name["test.battery.start.quick"]["category"] == "selftest"
        assert by_name["beeper.toggle"]["category"] == "beeper"
        assert by_name["load.off"]["risk"] == "dangerous"
        assert by_name["shutdown.return"]["risk"] == "dangerous"
        assert by_name["shutdown.return"]["category"] == "power"

        rw_vars = await client.list_writable_vars("main_ups")
        vars_map = {v["name"]: v for v in rw_vars}
        assert vars_map["ups.beeper.status"]["enum_values"] == ["enabled", "disabled", "muted"]
        assert vars_map["battery.charge.low"]["range_min"] == 10.0
        assert vars_map["battery.charge.low"]["range_max"] == 90.0
        assert vars_map["ups.delay.shutdown"]["value"] == "30"

        with caplog.at_level(logging.DEBUG):
            ok, msg = await client.run_instant_command(
                "main_ups", "beeper.toggle", "admin", "s3cr3tPass!"
            )
            assert ok is True
            assert msg == "OK"

            ok_var, msg_var = await client.set_var(
                "main_ups", "ups.delay.shutdown", '45 "sec"\\test', "admin", "s3cr3tPass!"
            )
            assert ok_var is True
            assert msg_var == "OK"
            assert srv.writable_vars["ups.delay.shutdown"] == '45 "sec"\\test'

            ok_bad, err_bad = await client.run_instant_command(
                "main_ups", "beeper.toggle", "admin", "wrong"
            )
            assert ok_bad is False
            assert "ACCESS-DENIED" in err_bad

        assert "s3cr3tPass!" not in caplog.text
    finally:
        await srv.stop()


def test_compute_next_run_all_frequencies() -> None:
    base = datetime(2026, 1, 31, 10, 0, tzinfo=timezone.utc)  # Saturday

    # Daily: 03:00 already passed -> next day
    nxt_daily = compute_next_run("daily", 0, 1, "03:00", after_dt=base)
    assert nxt_daily == datetime(2026, 2, 1, 3, 0, tzinfo=timezone.utc).isoformat()

    # Daily: 12:00 later today
    nxt_daily_same = compute_next_run("daily", 0, 1, "12:00", after_dt=base)
    assert nxt_daily_same == datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc).isoformat()

    # Weekly: Monday (0) after Saturday Jan 31 -> Monday Feb 2
    nxt_weekly = compute_next_run("weekly", 0, 1, "03:00", after_dt=base)
    assert nxt_weekly == datetime(2026, 2, 2, 3, 0, tzinfo=timezone.utc).isoformat()

    # Weekly: same weekday after time passed -> +7 days
    sat_base = datetime(2026, 1, 31, 10, 0, tzinfo=timezone.utc)
    nxt_weekly_same = compute_next_run("weekly", 5, 1, "03:00", after_dt=sat_base)
    assert nxt_weekly_same == datetime(2026, 2, 7, 3, 0, tzinfo=timezone.utc).isoformat()

    # Biweekly: same weekday after time passed -> +14 days
    nxt_biweekly = compute_next_run("biweekly", 5, 1, "03:00", after_dt=sat_base)
    assert nxt_biweekly == datetime(2026, 2, 14, 3, 0, tzinfo=timezone.utc).isoformat()

    # Monthly: day 31 in Jan after 10:00 -> Feb clamped to Feb 28
    nxt_monthly = compute_next_run("monthly", 0, 31, "03:00", after_dt=base)
    assert nxt_monthly == datetime(2026, 2, 28, 3, 0, tzinfo=timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_migration_v9_and_selftest_scheduler_flow(db, event_log) -> None:
    # Migration v9 tables & default notification rules exist
    rules = await db.fetch_all(
        "SELECT event_type, enabled, priority FROM notification_rules WHERE event_type LIKE 'ups_selftest_%'"
    )
    rules_map = {r["event_type"]: r for r in rules}
    assert rules_map["ups_selftest_completed"]["enabled"] == 1
    assert rules_map["ups_selftest_completed"]["priority"] == 0
    assert rules_map["ups_selftest_failed"]["enabled"] == 1
    assert rules_map["ups_selftest_failed"]["priority"] == 1

    srv = _MockNutServer()
    port = await srv.start()
    try:
        client = NutTcpClient(host="127.0.0.1", port=port, timeout=2.0)
        nut_manager = _StubNutManager(
            users=[
                {
                    "username": "admin",
                    "password": srv.expected_pass,
                    "actions": ["SET"],
                    "instcmds": ["ALL"],
                    "upsmon_role": "primary",
                }
            ]
        )
        u, p = await resolve_control_credentials(db, nut_manager)
        assert (u, p) == ("admin", srv.expected_pass)

        orchestrator = _StubOrchestrator(
            {
                "connected": True,
                "ups_list": [
                    {
                        "name": "main_ups",
                        "status": "OB",
                        "status_tokens": ["OB"],
                        "battery_charge": 95.0,
                        "battery_runtime": 2000.0,
                        "ups_load": 25.0,
                        "raw_vars": {"ups.test.result": "No test initiated"},
                    }
                ],
            }
        )
        dispatcher = NotificationDispatcher(db, event_log)
        scheduler = SelfTestScheduler(
            db,
            client,
            nut_manager,
            orchestrator,
            dispatcher,
            event_log,
            poll_interval_sec=60.0,
            monitor_step_sec=0.05,
            monitor_max_wait_sec=2.0,
        )

        # 1. Safety skip when UPS is On Battery (OB)
        skipped_ob = await scheduler.execute_selftest(
            ups_name="main_ups",
            test_command="test.battery.start.quick",
            trigger_type="scheduled",
            min_battery_pct=80.0,
            require_online=True,
        )
        assert skipped_ob.status == "skipped"
        assert "not online" in skipped_ob.message

        # 2. Safety skip when battery is below min_battery_pct
        orchestrator.latest_telemetry["ups_list"][0]["status"] = "OL"
        orchestrator.latest_telemetry["ups_list"][0]["status_tokens"] = ["OL"]
        orchestrator.latest_telemetry["ups_list"][0]["battery_charge"] = 60.0
        skipped_low = await scheduler.execute_selftest(
            ups_name="main_ups",
            test_command="test.battery.start.quick",
            trigger_type="scheduled",
            min_battery_pct=80.0,
            require_online=True,
        )
        assert skipped_low.status == "skipped"
        assert "below minimum" in skipped_low.message

        # 3. Due schedule execution & async monitoring to completion
        orchestrator.latest_telemetry["ups_list"][0]["battery_charge"] = 100.0
        sched = await scheduler.create_schedule(
            SelfTestScheduleCreate(
                ups_name="main_ups",
                test_command="test.battery.start.quick",
                frequency="weekly",
                day_of_week=0,
                time_hhmm="03:00",
                min_battery_pct=80.0,
                require_online=True,
                enabled=True,
            )
        )
        # Force next_run_at to the past so process_due_schedules triggers it
        past_iso = "2020-01-01T00:00:00+00:00"
        await db.execute(
            "UPDATE ups_selftest_schedules SET next_run_at = ? WHERE id = ?",
            (past_iso, sched.id),
        )

        triggered = await scheduler.process_due_schedules(
            now_dt=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        )
        assert len(triggered) == 1
        assert triggered[0].status == "running"
        assert triggered[0].trigger_type == "scheduled"
        assert triggered[0].battery_before == 100.0

        # Simulate UPS finishing the self-test
        await asyncio.sleep(0.1)
        srv.ups_status = "OL"
        srv.battery_charge = "97"
        srv.battery_runtime = "2250"
        srv.ups_test_result = "Done and passed"

        for _ in range(30):
            rep = await scheduler.get_report(triggered[0].id)
            if rep and rep.status != "running":
                break
            await asyncio.sleep(0.05)

        final_rep = await scheduler.get_report(triggered[0].id)
        assert final_rep is not None
        assert final_rep.status == "passed"
        assert final_rep.battery_before == 100.0
        assert final_rep.battery_after == 97.0
        assert final_rep.runtime_after == 2250.0
        assert final_rep.ups_test_result == "Done and passed"

        reports_resp = await scheduler.list_reports(ups_name="main_ups")
        assert reports_resp.summary.total == 3
        assert reports_resp.summary.passed == 1
        assert reports_resp.summary.skipped == 2
        assert reports_resp.summary.pass_rate_pct == 100.0
        assert reports_resp.summary.avg_battery_drop == 3.0
        assert reports_resp.summary.last_passed_at is not None

        await scheduler.stop()
    finally:
        await srv.stop()


@pytest.mark.asyncio
async def test_control_api_endpoints_and_dangerous_guard(db, event_log) -> None:
    srv = _MockNutServer()
    port = await srv.start()
    try:
        client = NutTcpClient(host="127.0.0.1", port=port, timeout=2.0)
        nut_manager = _StubNutManager(
            users=[
                {
                    "username": "admin",
                    "password": srv.expected_pass,
                    "actions": ["SET"],
                    "instcmds": ["ALL"],
                    "upsmon_role": "primary",
                }
            ]
        )
        orchestrator = _StubOrchestrator(
            {
                "connected": True,
                "ups_list": [
                    {
                        "name": "main_ups",
                        "status": "OL",
                        "status_tokens": ["OL"],
                        "battery_charge": 100.0,
                        "battery_runtime": 2400.0,
                        "ups_load": 25.0,
                        "raw_vars": {"ups.test.result": "No test initiated"},
                    }
                ],
            }
        )
        dispatcher = NotificationDispatcher(db, event_log)
        scheduler = SelfTestScheduler(
            db,
            client,
            nut_manager,
            orchestrator,
            dispatcher,
            event_log,
            monitor_step_sec=0.05,
            monitor_max_wait_sec=1.0,
        )

        app = FastAPI()
        app.include_router(control_router)
        app.state.db = db
        app.state.nut_client = client
        app.state.nut_manager = nut_manager
        app.state.orchestrator = orchestrator
        app.state.dispatcher = dispatcher
        app.state.event_log = event_log
        app.state.selftest_scheduler = scheduler

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            caps_res = await ac.get("/api/control/main_ups/capabilities")
            assert caps_res.status_code == 200
            caps = caps_res.json()
            assert caps["credentials_configured"] is True
            assert len(caps["commands"]) == 7
            assert len(caps["writable_vars"]) == 3

            # Dangerous command without confirmation must fail with HTTP 400
            bad_cmd = await ac.post(
                "/api/control/main_ups/command",
                json={"command": "load.off", "confirm_dangerous": False},
            )
            assert bad_cmd.status_code == 400
            assert "load.off" not in srv.on_instcmd

            # Dangerous command with confirmation succeeds
            ok_danger = await ac.post(
                "/api/control/main_ups/command",
                json={"command": "load.off", "confirm_dangerous": True},
            )
            assert ok_danger.status_code == 200
            assert ok_danger.json()["ok"] is True
            assert "load.off" in srv.on_instcmd

            # Battery test command creates and returns tracked report_id
            test_cmd = await ac.post(
                "/api/control/main_ups/command",
                json={"command": "test.battery.start.quick", "confirm_dangerous": False},
            )
            assert test_cmd.status_code == 200
            assert test_cmd.json()["ok"] is True
            report_id = test_cmd.json()["report_id"]
            assert isinstance(report_id, int)

            # Set writable var
            var_res = await ac.post(
                "/api/control/main_ups/var",
                json={"var_name": "battery.charge.low", "value": "30"},
            )
            assert var_res.status_code == 200
            assert var_res.json()["ok"] is True
            assert srv.writable_vars["battery.charge.low"] == "30"

            # Schedules CRUD + run-now
            create_sched = await ac.post(
                "/api/control/schedules",
                json={
                    "ups_name": "main_ups",
                    "test_command": "test.battery.start.quick",
                    "frequency": "daily",
                    "day_of_week": 0,
                    "day_of_month": 1,
                    "time_hhmm": "04:30",
                    "min_battery_pct": 80,
                    "require_online": True,
                    "notify_on_complete": True,
                    "notify_on_fail": True,
                    "enabled": True,
                },
            )
            assert create_sched.status_code == 200
            sched_id = create_sched.json()["id"]

            list_sched = await ac.get("/api/control/schedules")
            assert list_sched.status_code == 200
            assert len(list_sched.json()) == 1

            srv.ups_status = "OL"
            run_now = await ac.post(f"/api/control/schedules/{sched_id}/run-now")
            assert run_now.status_code == 200
            assert run_now.json()["schedule_id"] == sched_id

            # Finish monitoring before cleanup
            srv.ups_status = "OL"
            srv.ups_test_result = "Done and passed"
            await asyncio.sleep(0.25)

            reports_res = await ac.get("/api/control/reports")
            assert reports_res.status_code == 200
            assert reports_res.json()["summary"]["total"] >= 2

            del_rep = await ac.delete(f"/api/control/reports/{report_id}")
            assert del_rep.status_code == 200

            clear_rep = await ac.delete("/api/control/reports")
            assert clear_rep.status_code == 200

            del_sched = await ac.delete(f"/api/control/schedules/{sched_id}")
            assert del_sched.status_code == 200

        await scheduler.stop()
    finally:
        await srv.stop()
