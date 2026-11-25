"""Tests for NUT client config generator, LIST CLIENT parsing, and migration v2."""

from __future__ import annotations

from pathlib import Path
import pytest

from app.models.schemas import NutClientConfigRequest
from app.nut.client import build_ups_telemetry, parse_nut_list_line
from app.nut.client_config import generate_client_config
from app.nut.manager import NutConfigManager
from app.nut.parser import parse_upsd_users


def test_list_client_parsing_and_telemetry() -> None:
    parts = parse_nut_list_line("CLIENT ups1 192.168.1.50", "CLIENT ")
    assert parts == ["CLIENT", "ups1", "192.168.1.50"]

    tel = build_ups_telemetry(
        "ups1",
        "Rack",
        {"ups.status": "OL"},
        connected_clients=["192.168.1.50", "192.168.1.51"],
    )
    assert tel.connected_clients == ["192.168.1.50", "192.168.1.51"]


@pytest.mark.parametrize(
    "platform",
    ["linux_debian", "linux_rhel", "truenas", "synology", "windows"],
)
def test_generate_client_config_all_platforms(platform) -> None:
    req = NutClientConfigRequest(
        platform=platform,
        server_host="192.168.1.10",
        nut_port=3493,
        ups_name="apc_rack",
        username="wolnut_client",
        password="strong-password-123",
        final_delay=10,
    )
    res = generate_client_config(req)
    assert res.platform == platform
    assert len(res.files) >= 1

    if platform in ("linux_debian", "linux_rhel"):
        assert res.one_liner is not None
        assert "nut-monitor" in res.one_liner
        assert "MONITOR apc_rack@192.168.1.10 1 wolnut_client strong-password-123 secondary" in res.files[1].content
    else:
        assert res.one_liner is None
        assert len(res.gui_fields) >= 3


def test_generate_client_config_rejects_injection() -> None:
    req = NutClientConfigRequest(
        platform="linux_debian",
        server_host="192.168.1.10\nSHUTDOWNCMD /bin/evil",
        nut_port=3493,
        ups_name="ups1",
        username="monuser",
        password="secret",
    )
    with pytest.raises(ValueError):
        generate_client_config(req)


class _OkSupervisor:
    async def restart_service(self, _svc: str) -> tuple[bool, str]:
        return True, "running"

    async def stop_service(self, _svc: str) -> tuple[bool, str]:
        return True, "stopped"

    async def sync_services(self, _mode: str, _u: bool, _m: bool) -> tuple[bool, str]:
        return True, "synced"


@pytest.mark.asyncio
async def test_migration_v2_and_secondary_user_creation(db, event_log, tmp_path: Path) -> None:
    # Verify v2 columns exist on hosts table
    row_id = await db.execute(
        """
        INSERT INTO hosts (
            name, ip_or_host, mac_address,
            nut_client_enabled, nut_client_platform, nut_ups_name, nut_client_user,
            created_at, updated_at
        ) VALUES ('pve1', '192.168.1.50', 'AA:BB:CC:DD:EE:FF', 1, 'linux_debian', 'ups1', 'wolnut_client', '2026-01-01', '2026-01-01')
        """
    )
    host = await db.fetch_one("SELECT * FROM hosts WHERE id = ?", (row_id,))
    assert host is not None
    assert host["nut_client_enabled"] == 1
    assert host["nut_client_platform"] == "linux_debian"
    assert host["nut_ups_name"] == "ups1"
    assert host["nut_client_user"] == "wolnut_client"

    # Verify NutConfigManager saves secondary user cleanly without leaking password in event_logs
    nut_dir = tmp_path / "nut"
    nut_dir.mkdir()
    (nut_dir / "nut.conf").write_text("MODE=netserver\n", encoding="utf-8")
    (nut_dir / "ups.conf").write_text("[ups1]\n  driver = usbhid-ups\n  port = auto\n", encoding="utf-8")
    (nut_dir / "upsd.users").write_text("[admin]\n  password = adminpw\n  actions = SET\n", encoding="utf-8")

    mgr = NutConfigManager(nut_dir, _OkSupervisor(), event_log)  # type: ignore[arg-type]
    parsed, _ = mgr.read_config("upsd.users", mask_secrets=True)
    parsed["users"].append(
        {
            "username": "wolnut_client",
            "password": "my_generated_secret_99",
            "actions": [],
            "instcmds": [],
            "upsmon_role": "secondary",
        }
    )
    await mgr.save_config("upsd.users", parsed, apply_reload=True)

    disk_users = parse_upsd_users((nut_dir / "upsd.users").read_text(encoding="utf-8"), mask_secrets=False)
    names = {u.username: u for u in disk_users.users}
    assert "wolnut_client" in names
    assert names["wolnut_client"].upsmon_role == "secondary"
    assert names["wolnut_client"].password == "my_generated_secret_99"

    logs = await event_log.query(category="nut_config")
    for item in logs.items:
        assert "my_generated_secret_99" not in item.message


@pytest.mark.asyncio
async def test_monitor_account_persistence_reveal_and_delete(db, event_log, tmp_path: Path) -> None:
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.nut import router as nut_router
    from app.services.crypto import SecretBox
    from app.wol.orchestrator import RecoveryOrchestrator
    from app.notifications.dispatcher import NotificationDispatcher
    from app.nut.client import NutTcpClient

    nut_dir = tmp_path / "nut_accounts"
    nut_dir.mkdir()
    (nut_dir / "nut.conf").write_text("MODE=netserver\n", encoding="utf-8")
    (nut_dir / "ups.conf").write_text("[ups1]\n  driver = usbhid-ups\n  port = auto\n", encoding="utf-8")
    (nut_dir / "upsd.conf").write_text("LISTEN 127.0.0.1 3494\n", encoding="utf-8")
    (nut_dir / "upsd.users").write_text("[admin]\n  password = adminpw\n  actions = SET\n", encoding="utf-8")
    (nut_dir / "upsmon.conf").write_text("MONITOR ups1@127.0.0.1:3494 1 admin adminpw primary\n", encoding="utf-8")

    mgr = NutConfigManager(nut_dir, _OkSupervisor(), event_log)  # type: ignore[arg-type]
    secret_box = SecretBox(tmp_path)
    dispatcher = NotificationDispatcher(db, event_log)
    orch = RecoveryOrchestrator(db=db, nut_client=NutTcpClient(), dispatcher=dispatcher, event_log=event_log)

    app = FastAPI()
    app.include_router(nut_router)
    app.state.db = db
    app.state.nut_manager = mgr
    app.state.secret_box = secret_box
    app.state.orchestrator = orch
    app.state.event_log = event_log
    app.state.supervisor = _OkSupervisor()
    app.state.nut_client = NutTcpClient()
    app.state.repeater = None
    app.state.ws_hub = SimpleNamespace(broadcast=lambda *_args, **_kwargs: None)

    with TestClient(app) as client:
        # 1. Create monitor account with note
        create_res = client.post(
            "/api/nut/clients/create-user",
            json={"username": "proxmox_mon", "password": "VaultedPassword!2026", "notes": "Proxmox Cluster"},
        )
        assert create_res.status_code == 200
        created = create_res.json()
        assert created["username"] == "proxmox_mon"
        assert created["notes"] == "Proxmox Cluster"

        # 2. Verify encrypted in SQLite (not stored as plaintext)
        row = await db.fetch_one("SELECT * FROM nut_client_accounts WHERE username = ?", ("proxmox_mon",))
        assert row is not None
        assert row["password_enc"] != "VaultedPassword!2026"
        assert secret_box.decrypt(row["password_enc"]) == "VaultedPassword!2026"

        # 3. Overview lists saved_accounts without leaking password
        overview = client.get("/api/nut/clients").json()
        saved = {a["username"]: a for a in overview["saved_accounts"]}
        assert "proxmox_mon" in saved
        assert saved["proxmox_mon"]["notes"] == "Proxmox Cluster"
        assert saved["proxmox_mon"]["has_password"] is True
        assert "password" not in saved["proxmox_mon"]

        # 4. Reveal returns decrypted password for easy copy/use
        rev = client.get("/api/nut/clients/accounts/proxmox_mon/reveal").json()
        assert rev["username"] == "proxmox_mon"
        assert rev["password"] == "VaultedPassword!2026"
        assert rev["notes"] == "Proxmox Cluster"

        # 5. Cannot delete admin, can delete saved monitor account
        assert client.delete("/api/nut/clients/accounts/admin").status_code == 400
        del_res = client.delete("/api/nut/clients/accounts/proxmox_mon")
        assert del_res.status_code == 200
        assert del_res.json()["deleted"] is True

        # Verify removed from DB & upsd.users
        assert await db.fetch_one("SELECT * FROM nut_client_accounts WHERE username = ?", ("proxmox_mon",)) is None
        disk_users = parse_upsd_users((nut_dir / "upsd.users").read_text(encoding="utf-8"), mask_secrets=False)
        assert all(u.username != "proxmox_mon" for u in disk_users.users)

