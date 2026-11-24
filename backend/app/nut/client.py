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
