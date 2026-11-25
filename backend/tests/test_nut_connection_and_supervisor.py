"""Tests for NUT 2.8.0-safe driver status checks and external NUT server connection mode."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from app.nut.client import NutTcpClient
from app.nut.supervisor import NutSupervisor


def test_driver_status_external_netclient_and_empty_ups(tmp_path: Path) -> None:
    nut_dir = tmp_path / "nut"
    run_dir = tmp_path / "run_nut"
    nut_dir.mkdir()
    run_dir.mkdir()

    sup = NutSupervisor(nut_dir=nut_dir, run_dirs=(run_dir,))

    # External mode -> stopped without running upsdrvctl status
    st_ext = sup._check_drivers_status(external_mode=True)
    assert st_ext.status == "stopped"
    assert "external" in st_ext.detail.lower()

    # Empty ups.conf -> stopped
    (nut_dir / "nut.conf").write_text("MODE=netserver\n", encoding="utf-8")
    (nut_dir / "ups.conf").write_text("maxretry = 3\n", encoding="utf-8")
    st_empty = sup._check_drivers_status(external_mode=False)
    assert st_empty.status == "stopped"
    assert "no local ups" in st_empty.detail.lower()

    # netclient mode -> stopped
    (nut_dir / "nut.conf").write_text("MODE=netclient\n", encoding="utf-8")
    st_netclient = sup._check_drivers_status(external_mode=False)
    assert st_netclient.status == "stopped"
    assert "netclient" in st_netclient.detail.lower()


def test_driver_status_pid_liveness_checks(tmp_path: Path) -> None:
    nut_dir = tmp_path / "nut"
    run_dir = tmp_path / "run_nut"
    nut_dir.mkdir()
    run_dir.mkdir()
    (nut_dir / "nut.conf").write_text("MODE=netserver\n", encoding="utf-8")
    (nut_dir / "ups.conf").write_text(
        "[ups1]\n  driver = usbhid-ups\n  port = auto\n\n[ups2]\n  driver = apcsmart\n  port = /dev/ttyUSB0\n",
        encoding="utf-8",
    )

    sup = NutSupervisor(nut_dir=nut_dir, run_dirs=(run_dir,))

    # Neither PID present -> stopped
    st_none = sup._check_drivers_status(external_mode=False)
    assert st_none.status == "stopped"

    # Only ups1 alive -> partial error
    (run_dir / "usbhid-ups-ups1.pid").write_text(str(os.getpid()), encoding="utf-8")
    st_partial = sup._check_drivers_status(external_mode=False)
    assert st_partial.status == "error"
    assert "ups2" in st_partial.detail

    # Both alive -> running
    (run_dir / "apcsmart-ups2.pid").write_text(str(os.getpid()), encoding="utf-8")
    st_all = sup._check_drivers_status(external_mode=False)
    assert st_all.status == "running"
    assert "2/2" in st_all.detail


@pytest.mark.asyncio
async def test_migration_v4_nut_connection_and_client_reconfigure(db) -> None:
    row = await db.fetch_one("SELECT * FROM nut_connection_config WHERE id = 1")
    assert row is not None
    assert row["mode"] == "local"
    assert row["host"] == "127.0.0.1"
    assert int(row["port"]) == 3493

    await db.execute(
        "UPDATE nut_connection_config SET mode = 'external', host = '192.168.1.99', port = 3494 WHERE id = 1"
    )
    updated = await db.fetch_one("SELECT * FROM nut_connection_config WHERE id = 1")
    assert updated["mode"] == "external"
    assert updated["host"] == "192.168.1.99"
    assert int(updated["port"]) == 3494

    client = NutTcpClient()
    client.configure("192.168.1.99", 3494)
    assert client.host == "192.168.1.99"
    assert client.port == 3494


@pytest.mark.asyncio
async def test_restart_and_stop_drivers_use_root_user(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    sup = NutSupervisor(nut_dir=tmp_path)

    async def fake_run(*args: str, timeout: float = 15.0) -> tuple[int, str, str]:
        calls.append(args)
        return 0, "ok", ""

    sup._run = fake_run  # type: ignore[method-assign]
    ok, msg = await sup.restart_service("drivers")
    assert ok is True
    assert msg == "ok"
    assert calls == [
        ("upsdrvctl", "-u", "root", "stop"),
        ("upsdrvctl", "-u", "root", "start"),
    ]

    calls.clear()
    ok_stop, _ = await sup.stop_service("drivers")
    assert ok_stop is True
    assert calls == [("upsdrvctl", "-u", "root", "stop")]


@pytest.mark.asyncio
async def test_supervisor_run_does_not_hang_on_daemonized_child(tmp_path: Path) -> None:
    import sys
    import time

    sup = NutSupervisor(nut_dir=tmp_path)
    # Parent prints and spawns a short-lived grandchild holding stdout/stderr open, then exits immediately.
    script = (
        "import subprocess, sys; "
        "print('driver starting'); sys.stdout.flush(); "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'])"
    )
    t0 = time.monotonic()
    rc, out, err = await sup._run(sys.executable, "-c", script, timeout=1.5)
    elapsed = time.monotonic() - t0
    assert rc == 0
    assert "driver starting" in out
    assert err == ""
    assert elapsed < 2.0
