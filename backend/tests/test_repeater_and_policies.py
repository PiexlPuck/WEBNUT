"""Tests for Smart NUT Repeater, per-host shutdown streaming, disconnect grace, and staged battery WoL."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import pytest

from app.database import utc_now_iso
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.nut.repeater import NutRepeaterServer, VirtualUpsOverride
from app.wol.orchestrator import RecoveryOrchestrator


def _make_snapshot(status: str, charge: float = 100.0, connected: bool = True) -> dict:
    if not connected:
        return {
            "connected": False,
            "error": "Connection refused",
            "updated_at": utc_now_iso(),
            "ups_list": [],
        }
    return {
        "connected": True,
        "error": None,
        "updated_at": utc_now_iso(),
        "ups_list": [
            {
                "name": "ups1",
                "description": "Main APC UPS",
                "status": status,
                "status_tokens": status.split(),
                "battery_charge": charge,
                "battery_runtime": 900.0,
                "ups_load": 25.0,
                "input_voltage": 230.0 if "OL" in status else 0.0,
                "output_voltage": 230.0,
                "ups_mfr": "APC",
                "ups_model": "Smart-UPS",
                "raw_vars": {
                    "ups.status": status,
                    "battery.charge": str(int(charge)),
                    "battery.runtime": "900",
                    "ups.load": "25",
                    "ups.mfr": "APC",
                    "ups.model": "Smart-UPS",
                },
            }
        ],
    }


async def _insert_v2_host(
    db,
    *,
    name: str,
    ip: str,
    mac: str,
    virtual_ups_name: str,
    wake_min_battery_charge: float | None = None,
    shutdown_mode: str = "nut_stream",
    shutdown_battery_pct: float | None = None,
    shutdown_ob_delay_sec: int | None = None,
    disconnect_policy: str = "inherit",
    disconnect_grace_sec: int | None = None,
    priority: int = 10,
    was_online: int = 1,
) -> int:
    now = utc_now_iso()
    return await db.execute(
        """
        INSERT INTO hosts (
            name, ip_or_host, mac_address, virtual_ups_name,
            wake_min_battery_charge, shutdown_mode, shutdown_battery_pct,
            shutdown_ob_delay_sec, disconnect_policy, disconnect_grace_sec,
            boot_priority, stagger_delay_sec, check_method,
            boot_timeout_sec, retry_interval_sec, max_retries,
            always_wake, enabled, is_online, was_online_before_outage,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'icmp', 1, 0, 2, 0, 1, ?, ?, ?, ?)
        """,
        (
            name,
            ip,
            mac,
            virtual_ups_name,
            wake_min_battery_charge,
            shutdown_mode,
            shutdown_battery_pct,
            shutdown_ob_delay_sec,
            disconnect_policy,
            disconnect_grace_sec,
            priority,
            was_online,
            was_online,
            now,
            now,
        ),
    )


@pytest.mark.asyncio
async def test_repeater_server_mirrors_upstream_and_virtual_streams() -> None:
    repeater = NutRepeaterServer(bind_host="127.0.0.1", port=0)
    await repeater.start()
    assert repeater.running is True
    assert repeater.port > 0

    try:
        upstream = _make_snapshot("OB", charge=35.0)["ups_list"]
        repeater.update_state(
            upstream,
            [
                VirtualUpsOverride(
                    virtual_name="proxmox_1",
                    host_id=1,
                    host_name="Proxmox 1",
                    upstream_ups="ups1",
                    forced_status="OB LB FSD",
                    reason="Battery 35% <= 40%",
                ),
                VirtualUpsOverride(
                    virtual_name="proxmox_2",
                    host_id=2,
                    host_name="Proxmox 2",
                    upstream_ups="ups1",
                    forced_status="OB",
                    reason="waiting_host_threshold",
                ),
            ],
        )

        client = NutTcpClient(host="127.0.0.1", port=repeater.port, timeout=3.0)
        snap = await client.fetch_telemetry()
        assert snap.connected is True
        by_name = {u.name: u for u in snap.ups_list}
        assert set(by_name.keys()) == {"ups1", "proxmox_1", "proxmox_2"}
        assert by_name["ups1"].status == "OB"
        assert by_name["proxmox_1"].status == "OB LB FSD"
        assert by_name["proxmox_2"].status == "OB"
        assert by_name["proxmox_1"].battery_charge == 35.0

        # Also test LOGIN / LIST CLIENT tracking over raw TCP
        reader, writer = await asyncio.open_connection("127.0.0.1", repeater.port)
        writer.write(b"USERNAME monuser\nLOGIN proxmox_1\nLIST CLIENT proxmox_1\nLOGOUT\n")
        await writer.drain()
        lines = []
        for _ in range(6):
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            lines.append(line.decode("utf-8").strip())
        writer.close()
        await writer.wait_closed()
        text = "\n".join(lines)
        assert "BEGIN LIST CLIENT proxmox_1" in text
        assert "CLIENT proxmox_1 127.0.0.1" in text
    finally:
        await repeater.stop()


@pytest.mark.asyncio
async def test_per_host_shutdown_streaming_and_disconnect_grace(db, event_log) -> None:
    await _insert_v2_host(
        db,
        name="Proxmox 1",
        ip="10.0.1.10",
        mac="AA:BB:CC:11:00:01",
        virtual_ups_name="proxmox_1",
        shutdown_battery_pct=40.0,
        disconnect_policy="shutdown_after_grace",
        disconnect_grace_sec=600,
    )
    await _insert_v2_host(
        db,
        name="Proxmox 2",
        ip="10.0.1.20",
        mac="AA:BB:CC:11:00:02",
        virtual_ups_name="proxmox_2",
        shutdown_battery_pct=15.0,
        disconnect_policy="keep_running",
    )

    repeater = NutRepeaterServer(bind_host="127.0.0.1", port=0)
    await repeater.start()
    try:
        nut_client = NutTcpClient(host="127.0.0.1", port=repeater.port)
        dispatcher = NotificationDispatcher(db, event_log)
        orch = RecoveryOrchestrator(db=db, nut_client=nut_client, dispatcher=dispatcher, event_log=event_log, repeater=repeater)

        # 1. On battery at 70% -> neither host hits threshold yet
        await orch.evaluate_telemetry(_make_snapshot("OB", charge=70.0))
        client = NutTcpClient(host="127.0.0.1", port=repeater.port)
        snap = await client.fetch_telemetry()
        by_name = {u.name: u for u in snap.ups_list}
        assert by_name["proxmox_1"].status == "OB"
        assert by_name["proxmox_2"].status == "OB"

        # 2. Drop to 38% -> proxmox_1 flips to OB LB FSD while proxmox_2 stays OB
        await orch.evaluate_telemetry(_make_snapshot("OB", charge=38.0))
        snap = await client.fetch_telemetry()
        by_name = {u.name: u for u in snap.ups_list}
        assert by_name["proxmox_1"].status == "OB LB FSD"
        assert by_name["proxmox_2"].status == "OB"

        # 3. Utility returns OL -> forced overrides clear
        await orch.evaluate_telemetry(_make_snapshot("OL", charge=100.0))
        snap = await client.fetch_telemetry()
        by_name = {u.name: u for u in snap.ups_list}
        assert by_name["proxmox_1"].status == "OL"
        assert by_name["proxmox_2"].status == "OL"

        # 4. Simulate COMMBAD disconnect -> within 600s grace both hold OL
        await orch.evaluate_telemetry(_make_snapshot("COMMBAD", charge=100.0))
        snap = await client.fetch_telemetry()
        by_name = {u.name: u for u in snap.ups_list}
        assert by_name["proxmox_1"].status == "OL"
        assert by_name["proxmox_2"].status == "OL"

        # 5. Fast-forward disconnect start beyond 600s -> proxmox_1 (shutdown_after_grace) gets OB LB FSD,
        # while proxmox_2 (keep_running) continues holding OL
        orch._disconnect_started_at = datetime.now(timezone.utc) - timedelta(seconds=610)
        await orch.evaluate_telemetry(_make_snapshot("COMMBAD", charge=100.0))
        snap = await client.fetch_telemetry()
        by_name = {u.name: u for u in snap.ups_list}
        assert by_name["proxmox_1"].status == "OB LB FSD"
        assert by_name["proxmox_2"].status == "OL"
    finally:
        await repeater.stop()


@pytest.mark.asyncio
async def test_staged_battery_wol_recovery_per_host(db, event_log) -> None:
    await _insert_v2_host(
        db,
        name="Proxmox 1",
        ip="10.0.2.10",
        mac="AA:BB:CC:22:00:01",
        virtual_ups_name="proxmox_1",
        wake_min_battery_charge=10.0,
        priority=1,
        was_online=1,
    )
    await _insert_v2_host(
        db,
        name="Proxmox 2",
        ip="10.0.2.20",
        mac="AA:BB:CC:22:00:02",
        virtual_ups_name="proxmox_2",
        wake_min_battery_charge=50.0,
        priority=2,
        was_online=1,
    )
    await db.execute(
        "UPDATE orchestrator_config SET min_battery_charge = 50.0, stability_hold_sec = 0 WHERE id = 1"
    )

    sent_packets: list[str] = []
    online_ips: set[str] = set()

    def fake_wol(mac: str, _bcast: str | None) -> list[str]:
        sent_packets.append(mac)
        if mac == "AA:BB:CC:22:00:01":
            online_ips.add("10.0.2.10")
        elif mac == "AA:BB:CC:22:00:02":
            online_ips.add("10.0.2.20")
        return ["255.255.255.255"]

    async def fake_reach(ip: str, _m: str, _p: int | None, _t: float) -> bool:
        return ip in online_ips

    dispatcher = NotificationDispatcher(db, event_log)
    orch = RecoveryOrchestrator(
        db=db,
        nut_client=NutTcpClient(),
        dispatcher=dispatcher,
        event_log=event_log,
        reachability_fn=fake_reach,
        wol_sender=fake_wol,
        check_poll_step=0.05,
    )

    # Power goes OB
    await orch.evaluate_telemetry(_make_snapshot("OB", charge=5.0))
    # Utility returns OL at 15% (>= 10% for Proxmox 1, < 50% for Proxmox 2)
    await orch.evaluate_telemetry(_make_snapshot("OL", charge=15.0))
    # stability_hold_sec=0 moves STABILIZING -> RECOVERING on next OL poll
    await orch.evaluate_telemetry(_make_snapshot("OL", charge=15.0))

    # Give recovery loop time to wake Proxmox 1 while Proxmox 2 waits at 15%
    await asyncio.sleep(0.2)
    assert "AA:BB:CC:22:00:01" in sent_packets
    assert "AA:BB:CC:22:00:02" not in sent_packets

    row2 = await db.fetch_one("SELECT wake_state FROM hosts WHERE ip_or_host = '10.0.2.20'")
    assert row2["wake_state"] == "WAITING_BATTERY"

    # Battery recharges to 50% -> Proxmox 2 wakes up and sequence completes
    await orch.evaluate_telemetry(_make_snapshot("OL", charge=50.0))
    if orch._recovery_task is not None:
        await asyncio.wait_for(orch._recovery_task, timeout=3.0)

    assert "AA:BB:CC:22:00:02" in sent_packets
    hosts = await db.fetch_all("SELECT name, wake_state FROM hosts ORDER BY boot_priority")
    assert {h["name"]: h["wake_state"] for h in hosts} == {
        "Proxmox 1": "WAKE_SUCCEEDED",
        "Proxmox 2": "WAKE_SUCCEEDED",
    }


@pytest.mark.asyncio
async def test_individual_host_and_bulk_test_runs(db, event_log) -> None:
    hid1 = await _insert_v2_host(
        db,
        name="Node Alpha",
        ip="10.0.3.10",
        mac="AA:BB:CC:33:00:01",
        virtual_ups_name="node_alpha",
        wake_min_battery_charge=15.0,
        shutdown_battery_pct=30.0,
        disconnect_policy="keep_running",
        priority=1,
        was_online=0,
    )
    hid2 = await _insert_v2_host(
        db,
        name="Node Beta",
        ip="10.0.3.20",
        mac="AA:BB:CC:33:00:02",
        virtual_ups_name="node_beta",
        wake_min_battery_charge=20.0,
        shutdown_ob_delay_sec=180,
        disconnect_policy="shutdown_after_grace",
        disconnect_grace_sec=300,
        priority=2,
        was_online=0,
    )

    sent_packets: list[str] = []
    online_ips: set[str] = {"10.0.3.10"}

    def fake_wol(mac: str, _bcast: str | None) -> list[str]:
        sent_packets.append(mac)
        online_ips.add("10.0.3.20")
        return ["255.255.255.255"]

    async def fake_reach(ip: str, _m: str, _p: int | None, _t: float) -> bool:
        return ip in online_ips

    dispatcher = NotificationDispatcher(db, event_log)
    orch = RecoveryOrchestrator(
        db=db,
        nut_client=NutTcpClient(),
        dispatcher=dispatcher,
        event_log=event_log,
        reachability_fn=fake_reach,
        wol_sender=fake_wol,
        check_poll_step=0.05,
    )

    # 1. Individual host dry-run reports diagnostics without sending WoL
    dry_res = await orch.test_run_host(hid1, dry_run=True)
    assert dry_res["host_id"] == hid1
    assert dry_res["dry_run"] is True
    assert dry_res["reachable"] is True
    assert dry_res["started_live_wake"] is False
    assert "30%" in dry_res["shutdown_rule_summary"]
    assert "Keep running" in dry_res["disconnect_rule_summary"]
    assert sent_packets == []

    # 2. Individual host live test sends WoL only for hid2
    live_res = await orch.test_run_host(hid2, dry_run=False)
    assert live_res["started_live_wake"] is True
    if hid2 in orch._manual_tasks:
        await asyncio.wait_for(orch._manual_tasks[hid2], timeout=3.0)
    assert sent_packets == ["AA:BB:CC:33:00:02"]

    # 3. Bulk dry-run targeting only specific host IDs runs cleanly without sending WoL
    sent_packets.clear()
    await orch.simulate_recovery(dry_run=True, scope="bulk", host_ids=[hid1])
    if orch._recovery_task is not None:
        await asyncio.wait_for(orch._recovery_task, timeout=3.0)
    assert sent_packets == []

