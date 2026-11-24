"""Tests for WoL magic packets, ARP parsing, host monitoring, and recovery state machine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import pytest

from app.database import utc_now_iso
from app.notifications.dispatcher import NotificationDispatcher
from app.nut.client import NutTcpClient
from app.wol.magic_packet import build_magic_packet, normalize_mac
from app.wol.monitor import HostMonitor
from app.wol.network import parse_ip_neigh, parse_proc_net_arp
from app.wol.orchestrator import RecoveryOrchestrator


def test_magic_packet_and_mac_normalization() -> None:
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("aabb.ccdd.eeff") == "AA:BB:CC:DD:EE:FF"
    pkt = build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(pkt) == 102
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:] == bytes.fromhex("AABBCCDDEEFF") * 16

    with pytest.raises(ValueError):
        normalize_mac("not-a-mac")


def test_arp_parsers() -> None:
    proc_arp = """IP address       HW type     Flags       HW address            Mask     Device
192.168.1.10     0x1         0x2         aa:bb:cc:11:22:33     *        eth0
192.168.1.20     0x1         0x0         00:00:00:00:00:00     *        eth0
"""
    assert parse_proc_net_arp(proc_arp, "192.168.1.10") == "AA:BB:CC:11:22:33"
    assert parse_proc_net_arp(proc_arp, "192.168.1.20") is None

    ip_neigh = "192.168.1.50 dev br0 lladdr de:ad:be:ef:00:01 REACHABLE\n"
    assert parse_ip_neigh(ip_neigh, "192.168.1.50") == "DE:AD:BE:EF:00:01"


async def _insert_host(
    db,
    *,
    name: str,
    ip: str,
    mac: str,
    priority: int = 10,
    stagger: int = 0,
    was_online: int = 1,
    always_wake: int = 0,
    boot_timeout: int = 1,
    retry_interval: int = 0,
    max_retries: int = 3,
) -> int:
    now = utc_now_iso()
    return await db.execute(
        """
        INSERT INTO hosts (
            name, ip_or_host, mac_address, boot_priority, stagger_delay_sec,
            check_method, boot_timeout_sec, retry_interval_sec, max_retries,
            always_wake, enabled, is_online, was_online_before_outage,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'icmp', ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (
            name,
            ip,
            mac,
            priority,
            stagger,
            boot_timeout,
            retry_interval,
            max_retries,
            always_wake,
            was_online,
            was_online,
            now,
            now,
        ),
    )


def _snapshot(status: str, charge: float = 100.0) -> dict:
    return {
        "connected": True,
        "error": None,
        "updated_at": utc_now_iso(),
        "ups_list": [
            {
                "name": "ups1",
                "description": "Test UPS",
                "status": status,
                "status_tokens": status.split(),
                "battery_charge": charge,
                "battery_runtime": 1200.0,
                "ups_load": 20.0,
                "input_voltage": 120.0 if "OL" in status else 0.0,
                "output_voltage": 120.0,
                "ups_mfr": "APC",
                "ups_model": "Test",
                "raw_vars": {},
            }
        ],
    }


@pytest.mark.asyncio
async def test_ob_to_ol_recovery_stagger_and_pre_outage_filter(db, event_log) -> None:
    # host1 was online, host2 was offline (should NOT wake), host3 offline but always_wake=1
    await _insert_host(
        db,
        name="nas",
        ip="10.0.0.10",
        mac="AA:BB:CC:00:00:01",
        priority=1,
        was_online=1,
        boot_timeout=1,
        max_retries=2,
    )
    await _insert_host(
        db,
        name="workstation",
        ip="10.0.0.20",
        mac="AA:BB:CC:00:00:02",
        priority=2,
        was_online=0,
        always_wake=0,
    )
    await _insert_host(
        db,
        name="proxmox",
        ip="10.0.0.30",
        mac="AA:BB:CC:00:00:03",
        priority=3,
        was_online=0,
        always_wake=1,
        boot_timeout=1,
        max_retries=2,
    )

    await db.execute(
        "UPDATE orchestrator_config SET min_battery_charge = 25.0, stability_hold_sec = 0 WHERE id = 1"
    )

    sent_packets: list[str] = []
    online_ips: set[str] = set()

    def fake_wol(mac: str, _bcast: str | None) -> list[str]:
        sent_packets.append(mac)
        # Host comes online after magic packet is dispatched
        if mac == "AA:BB:CC:00:00:01":
            online_ips.add("10.0.0.10")
        elif mac == "AA:BB:CC:00:00:03":
            online_ips.add("10.0.0.30")
        return ["255.255.255.255:9"]

    async def fake_reach(ip: str, _m: str, _p: int | None, _t: float) -> bool:
        return ip in online_ips

    dispatcher = NotificationDispatcher(db, event_log)
    orch = RecoveryOrchestrator(
        db,
        NutTcpClient(),
        dispatcher,
        event_log,
        reachability_fn=fake_reach,
        wol_sender=fake_wol,
        check_poll_step=0.02,
    )

    # 1. UPS goes On Battery -> freezes snapshot
    await orch.evaluate_telemetry(_snapshot("OB DISCHRG", charge=80.0))
    st = await orch.get_state()
    assert st.state == "ON_BATTERY"
    assert st.snapshot_frozen is True

    # Verify HostMonitor does NOT overwrite was_online_before_outage while frozen
    monitor = HostMonitor(db, reachability_fn=fake_reach)
    await monitor.poll_once()
    nas_row = await db.fetch_one("SELECT is_online, was_online_before_outage FROM hosts WHERE name = 'nas'")
    assert nas_row["is_online"] == 0
    assert nas_row["was_online_before_outage"] == 1

    # 2. Power returns OL, but battery charge too low (15% < 25%) -> stay ON_BATTERY
    await orch.evaluate_telemetry(_snapshot("OL CHRG", charge=15.0))
    assert (await orch.get_state()).state == "ON_BATTERY"
    assert sent_packets == []

    # 3. Battery charge reaches 30% >= 25% -> triggers recovery
    await orch.evaluate_telemetry(_snapshot("OL CHRG", charge=30.0))
    assert orch._recovery_task is not None
    await orch._recovery_task

    assert sent_packets == ["AA:BB:CC:00:00:01", "AA:BB:CC:00:00:03"]
    assert "AA:BB:CC:00:00:02" not in sent_packets

    st_final = await orch.get_state()
    assert st_final.state == "NORMAL"
    assert st_final.snapshot_frozen is False

    nas_after = await db.fetch_one("SELECT wake_state FROM hosts WHERE name = 'nas'")
    ws_after = await db.fetch_one("SELECT wake_state FROM hosts WHERE name = 'workstation'")
    pve_after = await db.fetch_one("SELECT wake_state FROM hosts WHERE name = 'proxmox'")
    assert nas_after["wake_state"] == "WAKE_SUCCEEDED"
    assert ws_after["wake_state"] == "IDLE"
    assert pve_after["wake_state"] == "WAKE_SUCCEEDED"


@pytest.mark.asyncio
async def test_stability_hold_flicker_resets_to_on_battery(db, event_log) -> None:
    await db.execute(
        "UPDATE orchestrator_config SET min_battery_charge = 25.0, stability_hold_sec = 30 WHERE id = 1"
    )
    orch = RecoveryOrchestrator(db, NutTcpClient(), NotificationDispatcher(db, event_log), event_log)

    await orch.evaluate_telemetry(_snapshot("OB", charge=70.0))
    assert (await orch.get_state()).state == "ON_BATTERY"

    await orch.evaluate_telemetry(_snapshot("OL CHRG", charge=50.0))
    assert (await orch.get_state()).state == "STABILIZING"

    # Power flickers back to OB -> resets to ON_BATTERY
    await orch.evaluate_telemetry(_snapshot("OB", charge=49.0))
    st = await orch.get_state()
    assert st.state == "ON_BATTERY"
    assert st.stabilizing_started_at is None


@pytest.mark.asyncio
async def test_retry_loop_exhaustion_marks_wake_failed(db, event_log) -> None:
    host_id = await _insert_host(
        db,
        name="stubborn-node",
        ip="10.0.0.99",
        mac="11:22:33:44:55:66",
        boot_timeout=0,
        retry_interval=0,
        max_retries=3,
    )

    attempts: list[str] = []

    def fake_wol(mac: str, _bcast: str | None) -> list[str]:
        attempts.append(mac)
        return ["255.255.255.255:9"]

    async def always_down(_ip: str, _m: str, _p: int | None, _t: float) -> bool:
        return False

    orch = RecoveryOrchestrator(
        db,
        NutTcpClient(),
        NotificationDispatcher(db, event_log),
        event_log,
        reachability_fn=always_down,
        wol_sender=fake_wol,
        check_poll_step=0.01,
    )

    await orch.manual_wake_host(host_id, track_retries=True)
    await asyncio.sleep(0.05)
    if host_id in orch._manual_tasks:
        await orch._manual_tasks[host_id]

    assert len(attempts) == 3
    row = await db.fetch_one("SELECT wake_state, wake_attempt, last_wake_error FROM hosts WHERE id = ?", (host_id,))
    assert row["wake_state"] == "WAKE_FAILED"
    assert row["wake_attempt"] == 3
    assert "3 attempt(s)" in row["last_wake_error"]


@pytest.mark.asyncio
async def test_reboot_reconciliation_clears_interrupted_recovery(db, event_log) -> None:
    host_id = await _insert_host(
        db,
        name="node-a",
        ip="10.0.0.15",
        mac="AA:11:22:33:44:55",
    )
    now = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    await db.execute(
        "UPDATE hosts SET wake_state = 'WAKING', wake_attempt = 2 WHERE id = ?",
        (host_id,),
    )
    await db.execute(
        "UPDATE orchestrator_state SET state = 'RECOVERING', snapshot_frozen = 1, recovery_started_at = ? WHERE id = 1",
        (now,),
    )

    orch = RecoveryOrchestrator(db, NutTcpClient(), NotificationDispatcher(db, event_log), event_log)
    await orch.reconcile_on_startup()

    st = await orch.get_state()
    assert st.state == "NORMAL"
    assert st.snapshot_frozen is False
    host = await db.fetch_one("SELECT wake_state, wake_attempt FROM hosts WHERE id = ?", (host_id,))
    assert host["wake_state"] == "IDLE"
    assert host["wake_attempt"] == 0
