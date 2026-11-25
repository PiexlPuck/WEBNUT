"""Safe supervisorctl & upsdrvctl wrapper for managing NUT daemons."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from app.models.schemas import NutServiceStatus
from app.nut.parser import parse_nut_conf, parse_ups_conf


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class NutSupervisor:
    """Executes supervisorctl and upsdrvctl safely without a shell."""

    def __init__(
        self,
        supervisor_conf: Path = Path("/etc/supervisord.conf"),
        nut_dir: Path = Path("/etc/nut"),
        run_dirs: tuple[Path, ...] = (Path("/run/nut"), Path("/var/run/nut")),
    ) -> None:
        self.supervisor_conf = supervisor_conf
        self.nut_dir = nut_dir
        self.run_dirs = run_dirs

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

    def _find_driver_pid(self, ups_name: str, driver_name: str) -> int | None:
        for run_dir in self.run_dirs:
            if not run_dir.exists():
                continue
            exact = run_dir / f"{driver_name}-{ups_name}.pid"
            candidates = [exact] if exact.is_file() else sorted(run_dir.glob(f"*-{ups_name}.pid"))
            for pid_file in candidates:
                try:
                    pid = int(pid_file.read_text(encoding="utf-8", errors="replace").strip())
                    if _is_pid_alive(pid):
                        return pid
                except (OSError, ValueError):
                    continue
        return None

    def _check_drivers_status(self, external_mode: bool = False) -> NutServiceStatus:
        """Check NUT driver status via config + PID files (compatible with NUT 2.8.0)."""
        if external_mode:
            return NutServiceStatus(
                name="drivers",
                status="stopped",
                detail="Unused (connected to external NUT server)",
            )

        nut_conf_path = self.nut_dir / "nut.conf"
        ups_conf_path = self.nut_dir / "ups.conf"

        if nut_conf_path.exists():
            nut_mode = parse_nut_conf(
                nut_conf_path.read_text(encoding="utf-8", errors="replace")
            ).mode
            if nut_mode in ("none", "netclient"):
                return NutServiceStatus(
                    name="drivers",
                    status="stopped",
                    detail=f"Not used in MODE={nut_mode}",
                )

        if not ups_conf_path.exists():
            return NutServiceStatus(
                name="drivers",
                status="stopped",
                detail="No ups.conf found",
            )

        ups_conf = parse_ups_conf(ups_conf_path.read_text(encoding="utf-8", errors="replace"))
        if not ups_conf.ups_list:
            return NutServiceStatus(
                name="drivers",
                status="stopped",
                detail="No local UPS defined in ups.conf",
            )

        alive_names: list[str] = []
        missing_names: list[str] = []
        for ups in ups_conf.ups_list:
            pid = self._find_driver_pid(ups.name, ups.driver)
            if pid is not None:
                alive_names.append(ups.name)
            else:
                missing_names.append(ups.name)

        total = len(ups_conf.ups_list)
        if len(alive_names) == total:
            return NutServiceStatus(
                name="drivers",
                status="running",
                detail=f"{total}/{total} driver(s) active ({', '.join(alive_names)})",
            )
        if not alive_names:
            return NutServiceStatus(
                name="drivers",
                status="stopped",
                detail=f"Configured driver(s) not started ({', '.join(missing_names)})",
            )
        return NutServiceStatus(
            name="drivers",
            status="error",
            detail=f"Missing driver(s): {', '.join(missing_names)}",
        )

    async def get_services_status(self, external_mode: bool = False) -> list[NutServiceStatus]:
        statuses: list[NutServiceStatus] = []
        if shutil.which("supervisorctl") is None:
            return [
                NutServiceStatus(name="upsd", status="unavailable", detail="supervisorctl unavailable"),
                NutServiceStatus(name="upsmon", status="unavailable", detail="supervisorctl unavailable"),
                self._check_drivers_status(external_mode=external_mode),
            ]

        for svc in ("upsd", "upsmon"):
            rc, out, err = await self._run(*self._sup_args("status", svc))
            if rc == 127 or "unix:///var/run/supervisor.sock no such file" in (out + err).lower():
                statuses.append(
                    NutServiceStatus(name=svc, status="unavailable", detail=err or out or "supervisord not running")
                )
            else:
                mapped, detail = self._map_supervisor_state(out or err)
                if external_mode and svc == "upsd" and mapped == "stopped":
                    detail = "Idle (using external NUT server)"
                statuses.append(NutServiceStatus(name=svc, status=mapped, detail=detail))  # type: ignore[arg-type]

        statuses.append(self._check_drivers_status(external_mode=external_mode))
        return statuses

    async def restart_service(self, service: str) -> tuple[bool, str]:
        """Restart `upsd`, `upsmon`, or `drivers` (`upsdrvctl stop` + `upsdrvctl start`)."""
        if service == "drivers":
            await self._run("upsdrvctl", "-u", "root", "stop")
            rc, out, err = await self._run("upsdrvctl", "-u", "root", "start")
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
            rc, out, err = await self._run("upsdrvctl", "-u", "root", "stop")
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
