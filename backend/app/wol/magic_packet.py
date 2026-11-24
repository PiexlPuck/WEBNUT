"""Pure-Python Wake-on-LAN magic packet builder and UDP broadcaster."""

from __future__ import annotations

import re
import socket

_HEX_MAC_RE = re.compile(r"^[0-9a-fA-F]{12}$")


def normalize_mac(mac_address: str) -> str:
    """Validate and normalize a MAC address into AA:BB:CC:DD:EE:FF format."""
    cleaned = re.sub(r"[:\-.]", "", mac_address.strip())
    if not _HEX_MAC_RE.match(cleaned):
        raise ValueError(f"Invalid MAC address: '{mac_address}'")
    upper = cleaned.upper()
    return ":".join(upper[i : i + 2] for i in range(0, 12, 2))


def build_magic_packet(mac_address: str) -> bytes:
    """Construct a standard 102-byte Wake-on-LAN magic packet."""
    normalized = normalize_mac(mac_address)
    mac_bytes = bytes.fromhex(normalized.replace(":", ""))
    return b"\xff" * 6 + mac_bytes * 16


def send_wol_packet(
    mac_address: str,
    subnet_broadcast: str | None = None,
    port: int = 9,
) -> list[str]:
    """Broadcast a WoL magic packet via UDP port 9 to 255.255.255.255 and optional subnet broadcast."""
    packet = build_magic_packet(mac_address)
    targets = ["255.255.255.255"]
    if subnet_broadcast and subnet_broadcast.strip() and subnet_broadcast.strip() != "255.255.255.255":
        targets.append(subnet_broadcast.strip())

    sent_targets: list[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in targets:
            sock.sendto(packet, (target, port))
            sent_targets.append(f"{target}:{port}")
    return sent_targets
