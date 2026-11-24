"""USB UPS auto-discovery wrapper around nut-scanner."""

from __future__ import annotations

import asyncio
import re

from app.models.schemas import ScannedUsbDevice


def _slugify_ups_name(raw: str, index: int) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw.strip()).strip("_").lower()
    if not cleaned:
        return f"ups{index}"
    return cleaned[:48]


def parse_nut_scanner_output(stdout: str) -> list[ScannedUsbDevice]:
    """Parse INI-style blocks emitted by `nut-scanner -U`."""
    devices: list[ScannedUsbDevice] = []
    current: dict[str, str] | None = None

    def flush_current() -> None:
        if not current:
            return
        driver = current.get("driver", "").strip()
        if not driver:
            return
        idx = len(devices) + 1
        raw_name = current.get("product") or current.get("vendor") or f"ups{idx}"
        devices.append(
            ScannedUsbDevice(
                suggested_name=_slugify_ups_name(raw_name, idx),
                driver=driver,
                port=current.get("port", "auto").strip() or "auto",
                vendorid=current.get("vendorid"),
                productid=current.get("productid"),
                product=current.get("product"),
                serial=current.get("serial"),
                vendor=current.get("vendor"),
                bus=current.get("bus"),
                device=current.get("device"),
            )
        )

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush_current()
            current = {"section": line[1:-1].strip()}
            continue
        if current is not None and "=" in line:
            key, val = line.split("=", 1)
            k = key.strip().lower()
            v = val.strip().strip('"').strip("'")
            current[k] = v

    flush_current()
    return devices


async def scan_usb_ups(timeout: float = 15.0) -> list[ScannedUsbDevice]:
    """Run `nut-scanner -U` safely without a shell and parse discovered USB UPS devices."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nut-scanner",
            "-U",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "nut-scanner binary not found. Ensure nut/libnutscan2 are installed in the container."
        ) from exc

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"nut-scanner timed out after {timeout:.0f}s") from exc

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

    # nut-scanner returns 0 even when 0 devices found, or non-zero on fatal errors
    devices = parse_nut_scanner_output(stdout)
    if proc.returncode != 0 and not devices:
        raise RuntimeError(stderr or f"nut-scanner exited with code {proc.returncode}")
    return devices
