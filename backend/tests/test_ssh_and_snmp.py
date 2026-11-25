"""Tests for Fernet SecretBox, SNMP nut-scanner parsing, and SSH/SFTP client deployment."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.nut.scanner import parse_snmp_scanner_output
from app.services.crypto import SecretBox
from app.services.ssh_deployer import build_remote_client_files, deploy_nut_client_ssh


def test_secret_box_roundtrip_and_persistence(tmp_path: Path) -> None:
    box1 = SecretBox(tmp_path)
    token = box1.encrypt("SuperSecret!Pass#2026")
    assert token != "SuperSecret!Pass#2026"
    assert (tmp_path / ".secret.key").is_file()

    # Second instance loading the same .secret.key must decrypt identically
    box2 = SecretBox(tmp_path)
    assert box2.decrypt(token) == "SuperSecret!Pass#2026"
    assert box2.decrypt("") == ""
    assert box2.encrypt("") == ""


def test_parse_snmp_scanner_output() -> None:
    sample = """
[nutdev1]
\tdriver = "snmp-ups"
\tport = "192.168.10.50"
\tdesc = "APC Smart-UPS 3000"
\tmibs = "apcc"
\tcommunity = "private"
\tsnmp_version = "v2c"

[nutdev2]
\tdriver = "snmp-ups"
\tport = "192.168.10.51"
\tmibs = "pw"
"""
    devices = parse_snmp_scanner_output(sample)
    assert len(devices) == 2
    assert devices[0].driver == "snmp-ups"
    assert devices[0].port == "192.168.10.50"
    assert devices[0].mibs == "apcc"
    assert devices[0].community == "private"
    assert devices[0].snmp_version == "v2c"
    assert devices[0].desc == "APC Smart-UPS 3000"
    assert devices[1].port == "192.168.10.51"
    assert devices[1].mibs == "pw"
    assert devices[1].community == "public"


def test_build_remote_client_files_targets_repeater() -> None:
    nut_conf, upsmon_conf = build_remote_client_files(
        server_host="192.168.1.250",
        nut_port=3493,
        ups_name="proxmox_1",
        username="upsmon_user",
        password="secret",
        final_delay=8,
        shutdown_cmd="/sbin/shutdown -h +0",
    )
    assert "MODE=netclient" in nut_conf
    assert "MONITOR proxmox_1@192.168.1.250 1 upsmon_user secret secondary" in upsmon_conf
    assert "FINALDELAY 8" in upsmon_conf

    _, custom_port_conf = build_remote_client_files(
        server_host="192.168.1.250",
        nut_port=4493,
        ups_name="proxmox_2",
        username="u",
        password="p",
    )
    assert "MONITOR proxmox_2@192.168.1.250:4493 1 u p secondary" in custom_port_conf


@pytest.mark.asyncio
async def test_deploy_nut_client_ssh_mocked() -> None:
    uploaded: dict[str, io.StringIO] = {}
    commands_run: list[str] = []

    class FakeSftpFile:
        def __init__(self, path: str) -> None:
            self.path = path
            self.buf = io.StringIO()
            uploaded[path] = self.buf

        def write(self, data: str) -> int:
            return self.buf.write(data)

        def __enter__(self) -> FakeSftpFile:
            return self

        def __exit__(self, *_args) -> None:
            return None

    fake_sftp = MagicMock()
    fake_sftp.file.side_effect = lambda p, _mode: FakeSftpFile(p)

    def fake_exec(cmd: str, timeout: float = 120.0):
        commands_run.append(cmd)
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        if "cat /etc/os-release" in cmd:
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = b'ID=debian\nNAME="Debian GNU/Linux"\n'
            stderr.read.return_value = b""
        elif "command -v upsmon" in cmd:
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = b"/sbin/upsmon\n"
            stderr.read.return_value = b""
        else:
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = b"active\n"
            stderr.read.return_value = b""
        return stdin, stdout, stderr

    fake_client = MagicMock()
    fake_client.exec_command.side_effect = fake_exec
    fake_client.open_sftp.return_value = fake_sftp

    with patch("app.services.ssh_deployer.paramiko.SSHClient", return_value=fake_client):
        res = await deploy_nut_client_ssh(
            host="10.10.0.50",
            port=22,
            ssh_username="admin",
            ssh_password="ssh-pw-secret",
            sudo_password="",
            server_host="10.10.0.2",
            nut_port=3493,
            ups_name="proxmox_1",
            nut_username="upsmon_remote",
            nut_password="nut-pw-secret",
            final_delay=5,
            shutdown_cmd="/sbin/shutdown -h +0",
        )

    assert res.ok is True
    assert res.error is None
    assert len(res.steps) >= 4
    assert "/tmp/webnut-nut.conf" in uploaded
    assert "/tmp/webnut-upsmon.conf" in uploaded
    assert "MODE=netclient" in uploaded["/tmp/webnut-nut.conf"].getvalue()
    assert (
        "MONITOR proxmox_1@10.10.0.2 1 upsmon_remote nut-pw-secret secondary"
        in uploaded["/tmp/webnut-upsmon.conf"].getvalue()
    )
    assert any("systemctl enable --now nut-monitor" in c for c in commands_run)
    fake_client.close.assert_called_once()
