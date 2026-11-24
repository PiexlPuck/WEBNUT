"""Async host reachability checks (ICMP/TCP) and Layer-2 ARP MAC resolution."""

from __future__ import annotations

import asyncio
import ipaddress
import platform
import re
import socket
from pathlib import Path

from app.models.schemas import ArpLookupResponse
from app.wol.magic_packet import normalize_mac

_MAC_IN_TEXT_RE = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")


async def check_host_reachable(
    ip_or_host: str,
    method: str = "icmp",
    port: int | None = None,
    timeout: float = 2.0,
) -> bool:
    """Check whether a host is reachable via ICMP ping or TCP connect."""
    target = ip_or_host.strip()
    if not target:
        return False

    if method == "tcp":
        target_port = port or 22
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(target, target_port),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    # ICMP Ping without shell=True
    is_windows = platform.system().lower() == "windows"
    if is_windows:
        cmd = ["ping", "-n", "1", "-w", str(max(200, int(timeout * 1000))), target]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), target]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await asyncio.wait_for(proc.wait(), timeout=timeout + 1.5)
        return rc == 0
    except Exception:
        return False


def parse_proc_net_arp(content: str, target_ip: str) -> str | None:
    """Extract normalized MAC address for target_ip from /proc/net/arp content."""
    lines = content.splitlines()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == target_ip:
            candidate = parts[3]
            if candidate == "00:00:00:00:00:00":
                continue
            try:
                return normalize_mac(candidate)
            except ValueError:
                continue
    return None


def parse_ip_neigh(content: str, target_ip: str) -> str | None:
    """Extract normalized MAC address for target_ip from `ip neigh show` output."""
    for line in content.splitlines():
        tokens = line.split()
        if not tokens or tokens[0] != target_ip:
            continue
        if "lladdr" in tokens:
            idx = tokens.index("lladdr")
            if idx + 1 < len(tokens):
                try:
                    return normalize_mac(tokens[idx + 1])
                except ValueError:
                    continue
        m = _MAC_IN_TEXT_RE.search(line)
        if m:
            try:
                return normalize_mac(m.group(1))
            except ValueError:
                continue
    return None


async def _resolve_ipv4(ip_or_host: str) -> str | None:
    try:
        ipaddress.IPv4Address(ip_or_host.strip())
        return ip_or_host.strip()
    except ipaddress.AddressValueError:
        pass
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(ip_or_host.strip(), None, family=socket.AF_INET)
        if infos:
            return str(infos[0][4][0])
    except Exception:
        return None
    return None


async def resolve_mac_from_arp(ip_or_host: str) -> ArpLookupResponse:
    """Resolve IP/hostname to a Layer-2 MAC address via /proc/net/arp or `ip neigh`."""
    resolved_ip = await _resolve_ipv4(ip_or_host)
    if not resolved_ip:
        return ArpLookupResponse(
            ip_or_host=ip_or_host,
            resolved_ip=None,
            mac_address=None,
            found=False,
            source="dns_failed",
        )

    # Best-effort ping to populate the kernel ARP cache
    await check_host_reachable(resolved_ip, method="icmp", timeout=1.0)

    proc_arp = Path("/proc/net/arp")
    if proc_arp.exists():
        try:
            mac = parse_proc_net_arp(proc_arp.read_text(encoding="utf-8", errors="replace"), resolved_ip)
            if mac:
                return ArpLookupResponse(
                    ip_or_host=ip_or_host,
                    resolved_ip=resolved_ip,
                    mac_address=mac,
                    found=True,
                    source="/proc/net/arp",
                )
        except OSError:
            pass

    # Fallback: `ip neigh show <ip>`
    try:
        proc = await asyncio.create_subprocess_exec(
            "ip",
            "neigh",
            "show",
            resolved_ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        mac = parse_ip_neigh(stdout_b.decode("utf-8", errors="replace"), resolved_ip)
        if mac:
            return ArpLookupResponse(
                ip_or_host=ip_or_host,
                resolved_ip=resolved_ip,
                mac_address=mac,
                found=True,
                source="ip_neigh",
            )
    except Exception:
        pass

    return ArpLookupResponse(
        ip_or_host=ip_or_host,
        resolved_ip=resolved_ip,
        mac_address=None,
        found=False,
        source="not_found",
    )
