"""Built-in Smart NUT Repeater (RFC 9271 TCP server) with per-host virtual UPS streams."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def _escape_nut(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


@dataclass(slots=True)
class VirtualUpsOverride:
    virtual_name: str
    host_id: int
    host_name: str
    upstream_ups: str
    forced_status: str | None = None
    reason: str = ""


@dataclass(slots=True)
class RepeaterSession:
    session_id: int
    client_ip: str
    username: str = ""
    logged_in_ups: str | None = None
    connected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class NutRepeaterServer:
    """Async NUT TCP protocol server mirroring upstream UPSes and streaming per-host virtual UPSes."""

    def __init__(self, bind_host: str = "0.0.0.0", port: int = 3493) -> None:
        self.bind_host = bind_host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self._running = False
        self._error: str | None = None

        self._upstream_ups: dict[str, dict] = {}
        self._virtual_hosts: dict[str, VirtualUpsOverride] = {}
        self._sessions: dict[int, RepeaterSession] = {}
        self._session_seq = 0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def error(self) -> str | None:
        return self._error

    def update_state(
        self,
        upstream_ups_list: list[dict],
        virtual_hosts: list[VirtualUpsOverride],
    ) -> None:
        """Refresh mirrored upstream UPS telemetry and per-host virtual UPS overrides."""
        self._upstream_ups = {
            str(u["name"]): u for u in upstream_ups_list if u.get("name")
        }
        self._virtual_hosts = {v.virtual_name: v for v in virtual_hosts if v.virtual_name}

    def get_connected_clients(self) -> list[dict]:
        return [
            {
                "ups_name": s.logged_in_ups or "",
                "client_ip": s.client_ip,
                "username": s.username,
                "connected_at": s.connected_at,
            }
            for s in self._sessions.values()
            if s.logged_in_ups
        ]

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "port": self.port,
            "error": self._error,
            "mirrored_ups_count": len(self._upstream_ups),
            "virtual_ups_count": len(self._virtual_hosts),
            "connected_clients": self.get_connected_clients(),
        }

    async def start(self, port: int | None = None) -> None:
        if port is not None:
            self.port = int(port)
        await self.stop()
        try:
            self._server = await asyncio.start_server(
                self._handle_client,
                host=self.bind_host,
                port=self.port,
            )
            sock = self._server.sockets[0] if self._server.sockets else None
            if sock is not None:
                self.port = int(sock.getsockname()[1])
            self._running = True
            self._error = None
            logger.info("Smart NUT Repeater listening on %s:%s", self.bind_host, self.port)
        except OSError as exc:
            self._running = False
            self._error = (
                f"Cannot bind Smart NUT Repeater on port {self.port}: {exc}. "
                "If local upsd or host nut-server is already bound to 3493, adjust upsd.conf or Repeater Port."
            )
            logger.warning(self._error)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        self._running = False
        self._sessions.clear()

    def _resolve_ups(self, ups_name: str) -> tuple[str, dict[str, str]] | None:
        """Return (description, raw_vars) for a mirrored upstream UPS or virtual host UPS."""
        if ups_name in self._virtual_hosts:
            v = self._virtual_hosts[ups_name]
            base = self._upstream_ups.get(v.upstream_ups)
            if base is None and self._upstream_ups:
                base = next(iter(self._upstream_ups.values()))
            vars_copy = dict(base.get("raw_vars") or {}) if base else {}
            if v.forced_status:
                vars_copy["ups.status"] = v.forced_status
            elif "ups.status" not in vars_copy:
                vars_copy["ups.status"] = "OL"
            desc = f"WebNUT Virtual Stream for {v.host_name}"
            return desc, vars_copy

        if ups_name in self._upstream_ups:
            base = self._upstream_ups[ups_name]
            return str(base.get("description") or ""), dict(base.get("raw_vars") or {})
        return None

    def _list_all_ups_entries(self) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for name, base in self._upstream_ups.items():
            entries.append((name, str(base.get("description") or "")))
        for vname, v in self._virtual_hosts.items():
            if vname not in self._upstream_ups:
                entries.append((vname, f"WebNUT Virtual Stream for {v.host_name}"))
        return entries

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        client_ip = str(peer[0]) if isinstance(peer, tuple) and peer else "unknown"
        self._session_seq += 1
        sid = self._session_seq
        session = RepeaterSession(session_id=sid, client_ip=client_ip)
        self._sessions[sid] = session

        def send_line(text: str) -> None:
            writer.write(f"{text}\n".encode("utf-8"))

        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                parts = line.split()
                cmd = parts[0].upper()

                if cmd == "VER":
                    send_line("Network UPS Tools upsd 2.8.0 (WebNUT Repeater)")
                elif cmd == "NETVER":
                    send_line("1.3")
                elif cmd == "STARTTLS":
                    send_line("ERR FEATURE-NOT-CONFIGURED")
                elif cmd == "USERNAME" and len(parts) >= 2:
                    session.username = parts[1]
                    send_line("OK")
                elif cmd == "PASSWORD" and len(parts) >= 2:
                    send_line("OK")
                elif cmd == "LOGIN" and len(parts) >= 2:
                    ups_name = parts[1]
                    if self._resolve_ups(ups_name) is None and ups_name not in self._virtual_hosts:
                        send_line("ERR UNKNOWN-UPS")
                    else:
                        session.logged_in_ups = ups_name
                        send_line("OK")
                elif cmd == "LOGOUT":
                    send_line("OK Goodbye")
                    await writer.drain()
                    break
                elif cmd == "GET" and len(parts) >= 3:
                    sub = parts[1].upper()
                    ups_name = parts[2]
                    if sub == "NUMLOGINS":
                        cnt = sum(
                            1 for s in self._sessions.values() if s.logged_in_ups == ups_name
                        )
                        send_line(f"NUMLOGINS {ups_name} {cnt}")
                    elif sub == "VAR" and len(parts) >= 4:
                        var_name = parts[3]
                        resolved = self._resolve_ups(ups_name)
                        if resolved is None:
                            send_line("ERR UNKNOWN-UPS")
                        elif var_name not in resolved[1]:
                            send_line("ERR VAR-NOT-SUPPORTED")
                        else:
                            send_line(
                                f'VAR {ups_name} {var_name} "{_escape_nut(str(resolved[1][var_name]))}"'
                            )
                    else:
                        send_line("ERR INVALID-ARGUMENT")
                elif cmd == "LIST" and len(parts) >= 2:
                    sub = parts[1].upper()
                    if sub == "UPS":
                        send_line("BEGIN LIST UPS")
                        for uname, desc in self._list_all_ups_entries():
                            send_line(f'UPS {uname} "{_escape_nut(desc)}"')
                        send_line("END LIST UPS")
                    elif sub == "VAR" and len(parts) >= 3:
                        ups_name = parts[2]
                        resolved = self._resolve_ups(ups_name)
                        if resolved is None:
                            send_line("ERR UNKNOWN-UPS")
                        else:
                            send_line(f"BEGIN LIST VAR {ups_name}")
                            for k, v in resolved[1].items():
                                send_line(f'VAR {ups_name} {k} "{_escape_nut(str(v))}"')
                            send_line(f"END LIST VAR {ups_name}")
                    elif sub == "CLIENT" and len(parts) >= 3:
                        ups_name = parts[2]
                        send_line(f"BEGIN LIST CLIENT {ups_name}")
                        seen_ips: set[str] = set()
                        for s in self._sessions.values():
                            if s.logged_in_ups == ups_name and s.client_ip not in seen_ips:
                                seen_ips.add(s.client_ip)
                                send_line(f"CLIENT {ups_name} {s.client_ip}")
                        send_line(f"END LIST CLIENT {ups_name}")
                    elif sub in ("CMD", "RW", "ENUM", "RANGE") and len(parts) >= 3:
                        ups_name = parts[2]
                        send_line(f"BEGIN LIST {sub} {ups_name}")
                        send_line(f"END LIST {sub} {ups_name}")
                    else:
                        send_line("ERR INVALID-ARGUMENT")
                else:
                    send_line("ERR CMD-NOT-SUPPORTED")

                await writer.drain()
        except Exception:
            pass
        finally:
            self._sessions.pop(sid, None)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
