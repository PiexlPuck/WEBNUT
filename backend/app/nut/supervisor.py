"""Safe supervisorctl & upsdrvctl wrapper for managing NUT daemons."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from app.models.schemas import NutServiceStatus


class NutSupervisor:
    """Executes supervisorctl and upsdrvctl safely without a shell."""

    def __init__(self, supervisor_conf: Path = Path("/etc/supervisord.conf")) -> None:
        self.supervisor_conf = supervisor_conf

    async def _run(self, *args: str, timeout: float = 15.0) -> tuple[int, str, str]:
        exe = shutil.which(args[0])
        if exe is None:
            return 127, "", f"Executable '{args[0]}' not found"
        proc = await asyncio.create_subprocess_exec(
            exe,
            *args[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "", f"Command '{' '.join(args)}' timed out after {timeout:.0f}s"
        return (
            proc.returncode or 0,
            stdout_b.decode("utf-8", errors="replace").strip(),
            stderr_b.decode("utf-8", errors="replace").strip(),
        )

    def _sup_args(self, *cmd: str) -> tuple[str, ...]:
        if self.supervisor_conf.exists():
            return ("supervisorctl", "-c", str(self.supervisor_conf), *cmd)
        return ("supervisorctl", *cmd)

    @staticmethod
    def _map_supervisor_state(line: str) -> tuple[str, str]:
        upper = line.upper()
        if "RUNNING" in upper:
            return "running", line
        if "STOPPED" in upper or "EXITED" in upper:
            return "stopped", line
        if "FATAL" in upper or "BACKOFF" in upper:
            return "error", line
        return "stopped", line

    async def get_services_status(self) -> list[NutServiceStatus]:
        statuses: list[NutServiceStatus] = []
        if shutil.which("supervisorctl") is None:
            return [
                NutServiceStatus(name="upsd", status="unavailable", detail="supervisorctl unavailable"),
                NutServiceStatus(name="upsmon", status="unavailable", detail="supervisorctl unavailable"),
                NutServiceStatus(name="drivers", status="unavailable", detail="upsdrvctl unavailable"),
            ]

        for svc in ("upsd", "upsmon"):
            rc, out, err = await self._run(*self._sup_args("status", svc))
            if rc == 127 or "unix:///var/run/supervisor.sock no such file" in (out + err).lower():
                statuses.append(
                    NutServiceStatus(name=svc, status="unavailable", detail=err or out or "supervisord not running")
                )
            else:
                mapped, detail = self._map_supervisor_state(out or err)
                statuses.append(NutServiceStatus(name=svc, status=mapped, detail=detail))  # type: ignore[arg-type]

        rc_drv, out_drv, err_drv = await self._run("upsdrvctl", "-D", "status")
        if rc_drv == 127:
            statuses.append(NutServiceStatus(name="drivers", status="unavailable", detail=err_drv))
        elif rc_drv == 0:
            statuses.append(NutServiceStatus(name="drivers", status="running", detail=out_drv or "Drivers active"))
        else:
            detail = err_drv or out_drv or "Drivers stopped"
            state = "stopped" if "no ups" in detail.lower() or "can't open" in detail.lower() else "error"
            statuses.append(NutServiceStatus(name="drivers", status=state, detail=detail))  # type: ignore[arg-type]

        return statuses

    async def restart_service(self, service: str) -> tuple[bool, str]:
        """Restart `upsd`, `upsmon`, or `drivers` (`upsdrvctl stop` + `upsdrvctl start`)."""
        if service == "drivers":
            await self._run("upsdrvctl", "stop")
            rc, out, err = await self._run("upsdrvctl", "start")
            if rc != 0:
                return False, err or out or f"upsdrvctl start exited with {rc}"
            return True, out or "UPS drivers started"

        if service in ("upsd", "upsmon"):
            rc, out, err = await self._run(*self._sup_args("restart", service))
            if rc != 0:
                return False, err or out or f"supervisorctl restart {service} exited with {rc}"
            # Verify running
            st_rc, st_out, st_err = await self._run(*self._sup_args("status", service))
            mapped, detail = self._map_supervisor_state(st_out or st_err)
            if mapped != "running":
                return False, detail or f"{service} failed to stay running (rc={st_rc})"
            return True, detail

        return False, f"Unknown service '{service}'"

    async def stop_service(self, service: str) -> tuple[bool, str]:
        if service == "drivers":
            rc, out, err = await self._run("upsdrvctl", "stop")
            return rc == 0, err or out
        if service in ("upsd", "upsmon"):
            rc, out, err = await self._run(*self._sup_args("stop", service))
            return rc == 0, err or out
        return False, f"Unknown service '{service}'"

    async def sync_services(self, nut_mode: str, has_ups: bool, has_monitors: bool) -> tuple[bool, str]:
        """Start or stop NUT daemons cleanly according to current config."""
        errors: list[str] = []

        if nut_mode in ("standalone", "netserver") and has_ups:
            ok_drv, msg_drv = await self.restart_service("drivers")
            if not ok_drv:
                errors.append(f"drivers: {msg_drv}")
            ok_upsd, msg_upsd = await self.restart_service("upsd")
            if not ok_upsd:
                errors.append(f"upsd: {msg_upsd}")
        else:
            await self.stop_service("upsd")
            await self.stop_service("drivers")

        if nut_mode != "none" and has_monitors:
            ok_mon, msg_mon = await self.restart_service("upsmon")
            if not ok_mon:
                errors.append(f"upsmon: {msg_mon}")
        else:
            await self.stop_service("upsmon")

        if errors:
            return False, "; ".join(errors)
        return True, "NUT services synchronized"
