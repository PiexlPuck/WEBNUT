"""Paramiko SSH/SFTP remote nut-client deployer and fallback shutdown executor."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import shlex

import paramiko


@dataclass(slots=True)
class SshDeployResult:
    ok: bool
    steps: list[str] = field(default_factory=list)
    error: str | None = None


def build_remote_client_files(
    server_host: str,
    nut_port: int,
    ups_name: str,
    username: str,
    password: str,
    final_delay: int = 5,
    shutdown_cmd: str = "/sbin/shutdown -h +0",
) -> tuple[str, str]:
    """Build nut.conf and upsmon.conf for a secondary NUT client pointing at WebNUT Repeater."""
    nut_conf = "# Managed by WebNUT SSH Deployer\nMODE=netclient\n"
    host_port = server_host if nut_port == 3493 else f"{server_host}:{nut_port}"
    escaped_shutdown = shutdown_cmd.replace('"', '\\"')
    upsmon_conf = "\n".join(
        [
            "# Managed by WebNUT SSH Deployer",
            f"MONITOR {ups_name}@{host_port} 1 {username} {password} secondary",
            "MINSUPPLIES 1",
            f'SHUTDOWNCMD "{escaped_shutdown}"',
            "POLLFREQ 5",
            "POLLFREQALERT 5",
            "HOSTSYNC 15",
            "DEADTIME 15",
            "POWERDOWNFLAG /etc/killpower",
            f"FINALDELAY {final_delay}",
            "NOTIFYFLAG ONLINE SYSLOG+WALL",
            "NOTIFYFLAG ONBATT SYSLOG+WALL",
            "NOTIFYFLAG LOWBATT SYSLOG+WALL",
            "NOTIFYFLAG FSD SYSLOG+WALL",
            "NOTIFYFLAG COMMOK SYSLOG+WALL",
            "NOTIFYFLAG COMMBAD SYSLOG+WALL",
            "NOTIFYFLAG SHUTDOWN SYSLOG+WALL",
        ]
    ) + "\n"
    return nut_conf, upsmon_conf


def _exec_cmd(
    client: paramiko.SSHClient,
    command: str,
    sudo_password: str = "",
    timeout: float = 120.0,
) -> tuple[int, str, str]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    if sudo_password:
        stdin.write(sudo_password + "\n")
        stdin.flush()
    stdin.channel.shutdown_write()
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return rc, out, err


def _deploy_sync(
    host: str,
    port: int,
    ssh_username: str,
    ssh_password: str,
    sudo_password: str,
    server_host: str,
    nut_port: int,
    ups_name: str,
    nut_username: str,
    nut_password: str,
    final_delay: int,
    shutdown_cmd: str,
) -> SshDeployResult:
    steps: list[str] = []
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    effective_sudo = sudo_password or ssh_password
    is_root = ssh_username.strip() == "root"

    def run_priv(cmd: str, timeout: float = 120.0) -> tuple[int, str, str]:
        if is_root:
            return _exec_cmd(client, cmd, "", timeout=timeout)
        quoted = shlex.quote(cmd)
        return _exec_cmd(client, f"sudo -S -p '' sh -c {quoted}", effective_sudo, timeout=timeout)

    try:
        client.connect(
            hostname=host,
            port=port,
            username=ssh_username,
            password=ssh_password,
            timeout=15.0,
            look_for_keys=False,
            allow_agent=False,
        )
        steps.append(f"Connected via SSH to {ssh_username}@{host}:{port}")

        # Detect OS family (/etc/nut vs /etc/ups)
        _, os_rel, _ = _exec_cmd(client, "cat /etc/os-release 2>/dev/null || true", timeout=10.0)
        os_lower = os_rel.lower()
        is_rhel = any(x in os_lower for x in ("rhel", "rocky", "almalinux", "centos", "fedora"))
        etc_dir = "/etc/ups" if is_rhel else "/etc/nut"
        steps.append(f"Detected target NUT config directory: {etc_dir}")

        # Install nut-client if upsmon is missing
        rc_chk, _, _ = _exec_cmd(client, "command -v upsmon >/dev/null 2>&1", timeout=10.0)
        if rc_chk != 0:
            if is_rhel:
                install_cmd = "dnf install -y nut-client || yum install -y nut-client"
            else:
                install_cmd = "DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y nut-client"
            rc_inst, _, err_inst = run_priv(install_cmd, timeout=180.0)
            if rc_inst != 0:
                return SshDeployResult(
                    ok=False,
                    steps=steps,
                    error=f"Failed to install nut-client: {err_inst}",
                )
            steps.append("Installed nut-client package on remote host")
        else:
            steps.append("nut-client (upsmon) already installed on remote host")

        nut_conf, upsmon_conf = build_remote_client_files(
            server_host=server_host,
            nut_port=nut_port,
            ups_name=ups_name,
            username=nut_username,
            password=nut_password,
            final_delay=final_delay,
            shutdown_cmd=shutdown_cmd,
        )

        # Upload via SFTP to temp paths
        tmp_nut = "/tmp/webnut-nut.conf"
        tmp_upsmon = "/tmp/webnut-upsmon.conf"
        sftp = client.open_sftp()
        try:
            with sftp.file(tmp_nut, "w") as fh:
                fh.write(nut_conf)
            with sftp.file(tmp_upsmon, "w") as fh:
                fh.write(upsmon_conf)
        finally:
            sftp.close()
        steps.append("Uploaded nut.conf and upsmon.conf via SFTP")

        apply_cmd = (
            f"mkdir -p {etc_dir} && "
            f"mv {tmp_nut} {etc_dir}/nut.conf && "
            f"mv {tmp_upsmon} {etc_dir}/upsmon.conf && "
            f"(chown root:nut {etc_dir}/nut.conf {etc_dir}/upsmon.conf 2>/dev/null || true) && "
            f"chmod 640 {etc_dir}/nut.conf {etc_dir}/upsmon.conf && "
            "systemctl enable --now nut-monitor && "
            "systemctl restart nut-monitor && "
            "systemctl is-active nut-monitor"
        )
        rc_app, out_app, err_app = run_priv(apply_cmd, timeout=60.0)
        if rc_app != 0:
            return SshDeployResult(
                ok=False,
                steps=steps,
                error=err_app or out_app or f"Remote service setup exited with {rc_app}",
            )
        steps.append(
            f"Applied permissions, enabled & started nut-monitor ({out_app or 'active'})"
        )
        return SshDeployResult(ok=True, steps=steps)
    except Exception as exc:
        return SshDeployResult(ok=False, steps=steps, error=str(exc))
    finally:
        client.close()


async def deploy_nut_client_ssh(**kwargs) -> SshDeployResult:
    """Deploy nut-client configuration to a remote host via SSH/SFTP asynchronously."""
    return await asyncio.to_thread(_deploy_sync, **kwargs)


def _test_ssh_sync(host: str, port: int, username: str, password: str) -> tuple[bool, str]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=10.0,
            look_for_keys=False,
            allow_agent=False,
        )
        rc, out, err = _exec_cmd(client, "uname -sr", timeout=10.0)
        if rc == 0:
            return True, f"SSH OK ({out})"
        return False, err or f"SSH command exited with {rc}"
    except Exception as exc:
        return False, str(exc)
    finally:
        client.close()


async def test_ssh_connection(host: str, port: int, username: str, password: str) -> tuple[bool, str]:
    return await asyncio.to_thread(_test_ssh_sync, host, port, username, password)


def _shutdown_ssh_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    sudo_password: str,
    shutdown_cmd: str,
) -> tuple[bool, str]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=10.0,
            look_for_keys=False,
            allow_agent=False,
        )
        effective_sudo = sudo_password or password
        rc, out, err = _exec_cmd(
            client,
            shutdown_cmd or "sudo -S shutdown -h now",
            effective_sudo if "sudo" in (shutdown_cmd or "sudo") else "",
            timeout=15.0,
        )
        if rc == 0 or "closed" in err.lower():
            return True, out or "Remote shutdown command dispatched"
        return False, err or out or f"Shutdown command exited with {rc}"
    except Exception as exc:
        return False, str(exc)
    finally:
        client.close()


async def execute_ssh_shutdown(
    host: str,
    port: int,
    username: str,
    password: str,
    sudo_password: str = "",
    shutdown_cmd: str = "sudo -S shutdown -h now",
) -> tuple[bool, str]:
    return await asyncio.to_thread(
        _shutdown_ssh_sync, host, port, username, password, sudo_password, shutdown_cmd
    )
