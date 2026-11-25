"""Atomic NUT configuration manager with timestamped backup and rollback."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.schemas import NutConfigFile
from app.nut.parser import (
    merge_masked_secrets,
    parse_config_file,
    parse_nut_conf,
    parse_ups_conf,
    parse_upsmon_conf,
    validate_and_serialize,
)
from app.nut.supervisor import NutSupervisor
from app.services.event_log import EventLogService


class NutConfigApplyError(Exception):
    """Raised when applying a NUT config fails and triggers a rollback."""

    def __init__(self, message: str, stderr: str = "", rolled_back: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.stderr = stderr
        self.rolled_back = rolled_back


def _apply_nut_permissions(path: Path) -> None:
    """Enforce nut:nut ownership and 0640 mode when running in Linux container."""
    try:
        os.chmod(path, 0o640)
    except OSError:
        pass
    try:
        import grp
        import pwd

        uid = pwd.getpwnam("nut").pw_uid
        gid = grp.getgrnam("nut").gr_gid
        os.chown(path, uid, gid)
    except (ImportError, KeyError, PermissionError, OSError):
        pass


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        _apply_nut_permissions(tmp_path)
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


class NutConfigManager:
    """Reads, validates, backs up, writes, and reloads /etc/nut configuration files."""

    def __init__(
        self,
        nut_dir: Path,
        supervisor: NutSupervisor,
        event_log: EventLogService,
    ) -> None:
        self.nut_dir = nut_dir
        self.backup_dir = nut_dir / ".backups"
        self.supervisor = supervisor
        self.event_log = event_log

    def _file_path(self, filename: NutConfigFile) -> Path:
        return self.nut_dir / filename

    def _read_raw(self, filename: NutConfigFile) -> str:
        path = self._file_path(filename)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def read_config(self, filename: NutConfigFile, mask_secrets: bool = True) -> tuple[dict[str, Any], str]:
        raw = self._read_raw(filename)
        parsed = parse_config_file(filename, raw, mask_secrets=mask_secrets)
        display_raw = validate_and_serialize(filename, parsed) if mask_secrets and raw else raw
        return parsed, display_raw

    def preview_config(self, filename: NutConfigFile, payload: dict[str, Any]) -> str:
        return validate_and_serialize(filename, payload)

    def _create_backup(self, filename: NutConfigFile) -> Path | None:
        src = self._file_path(filename)
        if not src.exists():
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest_dir = self.backup_dir / stamp
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        shutil.copy2(src, dest)
        return dest

    async def _apply_daemon_reload(self, filename: NutConfigFile) -> tuple[bool, str]:
        nut_conf = parse_nut_conf(self._read_raw("nut.conf"))
        ups_conf = parse_ups_conf(self._read_raw("ups.conf"))
        upsmon_conf = parse_upsmon_conf(self._read_raw("upsmon.conf"), mask_secrets=False)

        has_ups = len(ups_conf.ups_list) > 0
        has_monitors = len(upsmon_conf.monitors) > 0

        if filename == "nut.conf":
            return await self.supervisor.sync_services(nut_conf.mode, has_ups, has_monitors)
        if filename == "ups.conf":
            if nut_conf.mode in ("standalone", "netserver") and has_ups:
                ok_drv, msg_drv = await self.supervisor.restart_service("drivers")
                if not ok_drv:
                    return False, msg_drv
                return await self.supervisor.restart_service("upsd")
            return await self.supervisor.sync_services(nut_conf.mode, has_ups, has_monitors)
        if filename in ("upsd.conf", "upsd.users"):
            if nut_conf.mode in ("standalone", "netserver") and has_ups:
                return await self.supervisor.restart_service("upsd")
            return True, "upsd idle (no active UPS or netclient mode)"
        if filename == "upsmon.conf":
            if nut_conf.mode != "none" and has_monitors:
                return await self.supervisor.restart_service("upsmon")
            await self.supervisor.stop_service("upsmon")
            return True, "upsmon stopped (no MONITOR entries)"
        return True, "No reload needed"

    async def save_config(
        self,
        filename: NutConfigFile,
        payload: dict[str, Any],
        apply_reload: bool = True,
    ) -> tuple[str, bool, str]:
        existing_raw = self._read_raw(filename)
        merged = merge_masked_secrets(filename, payload, existing_raw)
        serialized = validate_and_serialize(filename, merged)

        backup_path = self._create_backup(filename)
        target_path = self._file_path(filename)
        if filename == "ups.conf" and apply_reload and existing_raw.strip():
            prev_ups = parse_ups_conf(existing_raw)
            if prev_ups.ups_list:
                # Stop active drivers before overwriting ups.conf so removed/renamed UPSes release ports
                await self.supervisor.stop_service("drivers")
        _atomic_write_text(target_path, serialized)

        if not apply_reload:
            await self.event_log.record(
                "nut_config",
                "info",
                "nut_config_saved",
                f"Saved {filename} without daemon reload",
                {"filename": filename},
            )
            return serialized, False, f"Saved {filename}"

        ok, detail = await self._apply_daemon_reload(filename)
        if not ok:
            rolled_back = False
            if backup_path is not None and backup_path.exists():
                _atomic_write_text(target_path, backup_path.read_text(encoding="utf-8"))
                await self._apply_daemon_reload(filename)
                rolled_back = True
            await self.event_log.record(
                "nut_config",
                "error",
                "nut_config_rollback",
                f"Failed to apply {filename}; rolled back to previous config",
                {"filename": filename, "stderr": detail, "rolled_back": rolled_back},
            )
            raise NutConfigApplyError(
                message=f"NUT daemon failed after saving {filename}; configuration rolled back",
                stderr=detail,
                rolled_back=rolled_back,
            )

        await self.event_log.record(
            "nut_config",
            "success",
            "nut_config_applied",
            f"Saved and applied {filename}",
            {"filename": filename, "detail": detail},
        )
        # Return masked display text for secret-bearing files
        masked_parsed = parse_config_file(filename, serialized, mask_secrets=True)
        display_raw = validate_and_serialize(filename, masked_parsed)
        return display_raw, True, detail
