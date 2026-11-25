"""Async NUT TCP protocol (RFC 9271) client for live UPS telemetry."""

from __future__ import annotations

import asyncio
import shlex
from datetime import datetime, timezone

from app.models.schemas import TelemetrySnapshot, UpsTelemetry


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _validate_nut_token(value: str, label: str) -> str:
    """Validate a single NUT protocol token, rejecting whitespace and control characters."""
    if not value:
        raise ValueError(f"{label} cannot be empty")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label} cannot contain whitespace or control characters")
    return value


def _validate_nut_scalar(value: str, label: str) -> str:
    """Validate a NUT string value (password or SET VAR value), rejecting control characters."""
    if any(ch in ("\r", "\n", "\x00") or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label} cannot contain control characters or newlines")
    return value


def _escape_nut_quoted(value: str) -> str:
    """Escape backslashes and double quotes for NUT quoted protocol arguments."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _classify_command(command: str) -> tuple[str, str]:
    """Classify a NUT instant command into (risk, category)."""
    lower = command.lower()
    dangerous_markers = ("load.off", "shutdown", "reboot", "reset", "bypass")
    risk = "dangerous" if any(marker in lower for marker in dangerous_markers) else "safe"

    if "test." in lower or "selftest" in lower:
        category = "selftest"
    elif "beeper." in lower or lower == "beeper":
        category = "beeper"
    elif "calibrate." in lower or "calibration" in lower:
        category = "calibration"
    elif (
        "load." in lower
        or "shutdown." in lower
        or "outlet." in lower
        or "bypass." in lower
        or "reboot" in lower
        or "reset" in lower
    ):
        category = "power"
    else:
        category = "other"

    return risk, category


def parse_nut_list_line(line: str, expected_prefix: str) -> list[str] | None:
    """Parse a NUT protocol line like `VAR ups1 battery.charge "100"`."""
    stripped = line.strip()
    if not stripped or not stripped.startswith(expected_prefix):
        return None
    try:
        return shlex.split(stripped)
    except ValueError:
        return stripped.split()


def build_ups_telemetry(
    name: str,
    description: str,
    raw_vars: dict[str, str],
    connected_clients: list[str] | None = None,
) -> UpsTelemetry:
    """Convert raw NUT variable dictionary into a structured UpsTelemetry model."""
    status = raw_vars.get("ups.status", "UNKNOWN").strip() or "UNKNOWN"
    tokens = [tok for tok in status.split() if tok]
    return UpsTelemetry(
        name=name,
        description=description,
        status=status,
        status_tokens=tokens,
        battery_charge=_parse_float(raw_vars.get("battery.charge")),
        battery_runtime=_parse_float(raw_vars.get("battery.runtime")),
        ups_load=_parse_float(raw_vars.get("ups.load")),
        input_voltage=_parse_float(raw_vars.get("input.voltage")),
        output_voltage=_parse_float(raw_vars.get("output.voltage")),
        ups_mfr=raw_vars.get("ups.mfr") or raw_vars.get("device.mfr"),
        ups_model=raw_vars.get("ups.model") or raw_vars.get("device.model"),
        raw_vars=raw_vars,
        connected_clients=connected_clients or [],
    )


class NutTcpClient:
    """Lightweight async TCP client talking directly to upsd."""

    def __init__(self, host: str = "127.0.0.1", port: int = 3493, timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    def configure(self, host: str, port: int) -> None:
        """Update target upsd host and port dynamically at runtime."""
        self.host = host.strip()
        self.port = int(port)

    async def _read_list_block(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        command: str,
    ) -> list[str]:
        writer.write(f"{command}\n".encode("utf-8"))
        await writer.drain()

        lines: list[str] = []
        started = False
        while True:
            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            if not raw:
                raise ConnectionError("Unexpected EOF from upsd")
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("ERR "):
                raise RuntimeError(f"NUT error on '{command}': {line}")
            if line.startswith("BEGIN LIST "):
                started = True
                continue
            if line.startswith("END LIST "):
                break
            if started:
                lines.append(line)
        return lines

    async def fetch_telemetry(self) -> TelemetrySnapshot:
        """Query upsd for all configured UPS units and their variables."""
        now = datetime.now(timezone.utc).isoformat()
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
            ups_lines = await self._read_list_block(reader, writer, "LIST UPS")
            ups_entries: list[tuple[str, str]] = []
            for line in ups_lines:
                parts = parse_nut_list_line(line, "UPS ")
                if parts and len(parts) >= 2:
                    name = parts[1]
                    desc = parts[2] if len(parts) >= 3 else ""
                    ups_entries.append((name, desc))

            ups_list: list[UpsTelemetry] = []
            for name, desc in ups_entries:
                var_lines = await self._read_list_block(reader, writer, f"LIST VAR {name}")
                raw_vars: dict[str, str] = {}
                for vline in var_lines:
                    parts = parse_nut_list_line(vline, "VAR ")
                    if parts and len(parts) >= 4:
                        raw_vars[parts[2]] = parts[3]

                clients: list[str] = []
                try:
                    client_lines = await self._read_list_block(reader, writer, f"LIST CLIENT {name}")
                    seen_clients: set[str] = set()
                    for cline in client_lines:
                        cparts = parse_nut_list_line(cline, "CLIENT ")
                        if cparts and len(cparts) >= 3:
                            cip = cparts[2]
                            if cip not in seen_clients:
                                seen_clients.add(cip)
                                clients.append(cip)
                except Exception:
                    clients = []

                ups_list.append(build_ups_telemetry(name, desc, raw_vars, connected_clients=clients))

            try:
                writer.write(b"LOGOUT\n")
                await writer.drain()
            except Exception:
                pass

            return TelemetrySnapshot(
                connected=True,
                error=None,
                ups_list=ups_list,
                updated_at=now,
            )
        except Exception as exc:
            return TelemetrySnapshot(
                connected=False,
                error=str(exc),
                ups_list=[],
                updated_at=now,
            )
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    async def _open_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )

    async def _close_connection(self, writer: asyncio.StreamWriter | None) -> None:
        if writer is None:
            return
        try:
            writer.write(b"LOGOUT\n")
            await writer.drain()
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    async def _send_line(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        line: str,
    ) -> str:
        writer.write(f"{line}\n".encode("utf-8"))
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        if not raw:
            raise ConnectionError("Unexpected EOF from upsd")
        return raw.decode("utf-8", errors="replace").strip()

    async def _authenticate(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        username: str,
        password: str,
    ) -> tuple[bool, str]:
        user_tok = _validate_nut_token(username, "username")
        pwd_val = _validate_nut_scalar(password, "password")
        resp_user = await self._send_line(reader, writer, f"USERNAME {user_tok}")
        if not resp_user.startswith("OK"):
            return False, resp_user

        if any(ch.isspace() or ch in ('"', "\\") for ch in pwd_val) or not pwd_val:
            pwd_arg = f'"{_escape_nut_quoted(pwd_val)}"'
        else:
            pwd_arg = pwd_val
        resp_pass = await self._send_line(reader, writer, f"PASSWORD {pwd_arg}")
        if not resp_pass.startswith("OK"):
            return False, resp_pass
        return True, "OK"

    async def list_commands(self, ups_name: str) -> list[dict]:
        """Query `LIST CMD <ups>` and `GET CMDDESC` for each supported instant command."""
        safe_ups = _validate_nut_token(ups_name, "ups_name")
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await self._open_connection()
            cmd_lines = await self._read_list_block(reader, writer, f"LIST CMD {safe_ups}")
            commands: list[dict] = []
            seen: set[str] = set()
            for line in cmd_lines:
                parts = parse_nut_list_line(line, "CMD ")
                if not parts or len(parts) < 3:
                    continue
                cmd_name = parts[2]
                if cmd_name in seen:
                    continue
                seen.add(cmd_name)

                desc = ""
                try:
                    desc_line = await self._send_line(
                        reader, writer, f"GET CMDDESC {safe_ups} {cmd_name}"
                    )
                    dparts = parse_nut_list_line(desc_line, "CMDDESC ")
                    if dparts and len(dparts) >= 4:
                        desc = dparts[3]
                except Exception:
                    desc = ""

                risk, category = _classify_command(cmd_name)
                commands.append(
                    {
                        "name": cmd_name,
                        "description": desc,
                        "risk": risk,
                        "category": category,
                    }
                )
            return commands
        finally:
            await self._close_connection(writer)

    async def list_writable_vars(self, ups_name: str) -> list[dict]:
        """Query `LIST RW <ups>` along with variable descriptions, types, enums, and ranges."""
        safe_ups = _validate_nut_token(ups_name, "ups_name")
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await self._open_connection()
            rw_lines = await self._read_list_block(reader, writer, f"LIST RW {safe_ups}")
            results: list[dict] = []
            for line in rw_lines:
                parts = parse_nut_list_line(line, "RW ")
                if not parts or len(parts) < 3:
                    continue
                var_name = parts[2]
                value = parts[3] if len(parts) >= 4 else ""

                description = ""
                try:
                    desc_line = await self._send_line(
                        reader, writer, f"GET DESC {safe_ups} {var_name}"
                    )
                    dparts = parse_nut_list_line(desc_line, "DESC ")
                    if dparts and len(dparts) >= 4:
                        description = dparts[3]
                except Exception:
                    description = ""

                var_type = ""
                type_tokens: list[str] = []
                try:
                    type_line = await self._send_line(
                        reader, writer, f"GET TYPE {safe_ups} {var_name}"
                    )
                    tparts = parse_nut_list_line(type_line, "TYPE ")
                    if tparts and len(tparts) >= 4:
                        type_tokens = [tok.upper() for tok in tparts[3:]]
                        var_type = " ".join(tparts[3:])
                except Exception:
                    var_type = ""

                enum_values: list[str] = []
                if "ENUM" in type_tokens:
                    try:
                        enum_lines = await self._read_list_block(
                            reader, writer, f"LIST ENUM {safe_ups} {var_name}"
                        )
                        for eline in enum_lines:
                            eparts = parse_nut_list_line(eline, "ENUM ")
                            if eparts and len(eparts) >= 4:
                                enum_values.append(eparts[3])
                    except Exception:
                        enum_values = []

                range_min: float | None = None
                range_max: float | None = None
                if "RANGE" in type_tokens:
                    try:
                        range_lines = await self._read_list_block(
                            reader, writer, f"LIST RANGE {safe_ups} {var_name}"
                        )
                        for rline in range_lines:
                            rparts = parse_nut_list_line(rline, "RANGE ")
                            if rparts and len(rparts) >= 5:
                                rmin = _parse_float(rparts[3])
                                rmax = _parse_float(rparts[4])
                                if rmin is not None:
                                    range_min = rmin if range_min is None else min(range_min, rmin)
                                if rmax is not None:
                                    range_max = rmax if range_max is None else max(range_max, rmax)
                    except Exception:
                        range_min = None
                        range_max = None

                results.append(
                    {
                        "name": var_name,
                        "value": value,
                        "description": description,
                        "var_type": var_type,
                        "enum_values": enum_values,
                        "range_min": range_min,
                        "range_max": range_max,
                    }
                )
            return results
        finally:
            await self._close_connection(writer)

    async def run_instant_command(
        self,
        ups_name: str,
        command: str,
        username: str,
        password: str,
    ) -> tuple[bool, str]:
        """Authenticate and execute `INSTCMD <ups> <command>` via RFC 9271."""
        writer: asyncio.StreamWriter | None = None
        try:
            safe_ups = _validate_nut_token(ups_name, "ups_name")
            safe_cmd = _validate_nut_token(command, "command")
            reader, writer = await self._open_connection()
            auth_ok, auth_msg = await self._authenticate(reader, writer, username, password)
            if not auth_ok:
                return False, auth_msg
            resp = await self._send_line(reader, writer, f"INSTCMD {safe_ups} {safe_cmd}")
            if resp.startswith("OK"):
                return True, "OK"
            return False, resp
        except Exception as exc:
            return False, str(exc)
        finally:
            await self._close_connection(writer)

    async def set_var(
        self,
        ups_name: str,
        var_name: str,
        value: str,
        username: str,
        password: str,
    ) -> tuple[bool, str]:
        """Authenticate and execute `SET VAR <ups> <var_name> "<escaped>"` via RFC 9271."""
        writer: asyncio.StreamWriter | None = None
        try:
            safe_ups = _validate_nut_token(ups_name, "ups_name")
            safe_var = _validate_nut_token(var_name, "var_name")
            safe_val = _validate_nut_scalar(value, "value")
            escaped = _escape_nut_quoted(safe_val)
            reader, writer = await self._open_connection()
            auth_ok, auth_msg = await self._authenticate(reader, writer, username, password)
            if not auth_ok:
                return False, auth_msg
            resp = await self._send_line(
                reader, writer, f'SET VAR {safe_ups} {safe_var} "{escaped}"'
            )
            if resp.startswith("OK"):
                return True, "OK"
            return False, resp
        except Exception as exc:
            return False, str(exc)
        finally:
            await self._close_connection(writer)
