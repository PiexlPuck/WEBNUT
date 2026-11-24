"""Unit and integration tests for NUT config parsing, validation, and atomic rollback."""

from __future__ import annotations

from pathlib import Path
import pytest

from app.models.schemas import SECRET_MASK
from app.nut.manager import NutConfigApplyError, NutConfigManager
from app.nut.parser import (
    merge_masked_secrets,
    parse_nut_conf,
    parse_ups_conf,
    parse_upsd_conf,
    parse_upsd_users,
    parse_upsmon_conf,
    serialize_nut_conf,
    serialize_ups_conf,
    serialize_upsd_conf,
    serialize_upsd_users,
    serialize_upsmon_conf,
)


def test_nut_conf_roundtrip() -> None:
    raw = "MODE=netserver\n"
    parsed = parse_nut_conf(raw)
    assert parsed.mode == "netserver"
    out = serialize_nut_conf(parsed)
    assert "MODE=netserver" in out


def test_ups_conf_roundtrip_with_quotes_and_directives() -> None:
    raw = """
maxretry = 3

[apc_rack]
  driver = usbhid-ups
  port = auto
  desc = "Main Rack UPS"
  vendorid = 051d
  pollinterval = 10
"""
    parsed = parse_ups_conf(raw)
    assert len(parsed.global_directives) == 1
    assert parsed.global_directives[0].key == "maxretry"
    assert len(parsed.ups_list) == 1
    ups = parsed.ups_list[0]
    assert ups.name == "apc_rack"
    assert ups.driver == "usbhid-ups"
    assert ups.desc == "Main Rack UPS"
    assert {d.key: d.value for d in ups.directives} == {
        "vendorid": "051d",
        "pollinterval": "10",
    }

    serialized = serialize_ups_conf(parsed)
    reparsed = parse_ups_conf(serialized)
    assert reparsed == parsed


def test_upsd_conf_roundtrip() -> None:
    raw = """
LISTEN 0.0.0.0 3493
LISTEN ::1 3493
MAXAGE 20
MAXCONN 256
"""
    parsed = parse_upsd_conf(raw)
    assert len(parsed.listens) == 2
    assert parsed.maxage == 20
    assert parsed.maxconn == 256
    reparsed = parse_upsd_conf(serialize_upsd_conf(parsed))
    assert reparsed == parsed


def test_upsd_users_masking_and_merge() -> None:
    raw = """
[admin]
  password = supersecret
  actions = SET
  instcmds = ALL

[monuser]
  password = monsecret
  upsmon primary
"""
    masked = parse_upsd_users(raw, mask_secrets=True)
    assert masked.users[0].password == SECRET_MASK
    assert masked.users[1].password == SECRET_MASK
    assert masked.users[1].upsmon_role == "primary"

    incoming = masked.model_dump()
    incoming["users"][0]["password"] = SECRET_MASK
    incoming["users"][1]["password"] = "newmonpass"
    merged = merge_masked_secrets("upsd.users", incoming, raw)
    assert merged["users"][0]["password"] == "supersecret"
    assert merged["users"][1]["password"] == "newmonpass"

    serialized = serialize_upsd_users(parse_upsd_users(raw, mask_secrets=False))
    assert "supersecret" in serialized


def test_upsmon_conf_roundtrip_and_legacy_roles() -> None:
    raw = """
RUN_AS_USER nut
MINSUPPLIES 1
SHUTDOWNCMD "/sbin/shutdown -h +0"
POLLFREQ 5
MONITOR ups1@127.0.0.1 1 monuser secretpass master
NOTIFYFLAG ONLINE SYSLOG+WALL
"""
    parsed = parse_upsmon_conf(raw, mask_secrets=False)
    assert parsed.monitors[0].role == "primary"
    assert parsed.monitors[0].password == "secretpass"
    assert parsed.shutdowncmd == "/sbin/shutdown -h +0"
    out = serialize_upsmon_conf(parsed)
    assert "MONITOR ups1@127.0.0.1 1 monuser secretpass primary" in out


def test_parser_rejects_newline_injection() -> None:
    raw = "[ups1]\n  driver = usbhid-ups\n  port = auto\n"
    parsed = parse_ups_conf(raw)
    parsed.ups_list[0].desc = "Bad\n[injected]\ndriver=dummy"
    with pytest.raises(ValueError):
        serialize_ups_conf(parsed)


class FakeSupervisor:
    """Stub supervisor for testing atomic writes and automatic rollback."""

    def __init__(self, fail_on_restart: bool = False) -> None:
        self.fail_on_restart = fail_on_restart
        self.calls: list[tuple[str, str]] = []

    async def restart_service(self, service: str) -> tuple[bool, str]:
        self.calls.append(("restart", service))
        if self.fail_on_restart:
            self.fail_on_restart = False  # Succeed on rollback restart
            return False, "upsd: invalid directive in ups.conf"
        return True, "running"

    async def stop_service(self, service: str) -> tuple[bool, str]:
        self.calls.append(("stop", service))
        return True, "stopped"

    async def sync_services(self, nut_mode: str, has_ups: bool, has_monitors: bool) -> tuple[bool, str]:
        self.calls.append(("sync", nut_mode))
        return True, "synced"


@pytest.mark.asyncio
async def test_atomic_save_and_rollback_on_daemon_failure(tmp_path: Path, event_log) -> None:
    nut_dir = tmp_path / "nut"
    nut_dir.mkdir()
    (nut_dir / "nut.conf").write_text("MODE=netserver\n", encoding="utf-8")
    original_ups = "[good_ups]\n  driver = usbhid-ups\n  port = auto\n"
    (nut_dir / "ups.conf").write_text(original_ups, encoding="utf-8")

    sup = FakeSupervisor(fail_on_restart=True)
    mgr = NutConfigManager(nut_dir, sup, event_log)  # type: ignore[arg-type]

    new_payload = {
        "global_directives": [],
        "ups_list": [
            {
                "name": "bad_ups",
                "driver": "usbhid-ups",
                "port": "auto",
                "desc": "Broken",
                "directives": [],
            }
        ],
    }

    with pytest.raises(NutConfigApplyError) as exc_info:
        await mgr.save_config("ups.conf", new_payload, apply_reload=True)

    assert exc_info.value.rolled_back is True
    assert "invalid directive" in exc_info.value.stderr
    # File contents must be restored to original
    assert (nut_dir / "ups.conf").read_text(encoding="utf-8") == original_ups
    # Backup file must exist
    backups = list((nut_dir / ".backups").rglob("ups.conf"))
    assert len(backups) == 1
