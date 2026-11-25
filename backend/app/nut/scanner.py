"""USB & Serial UPS auto-discovery wrapper around nut-scanner, sysfs, and lsusb."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from app.models.schemas import (
    HardwareScanResponse,
    RawUsbDevice,
    ScannedSnmpDevice,
    ScannedUsbDevice,
    SerialDeviceInfo,
)

COMMON_SNMP_MIBS: list[str] = [
    "auto",
    "apcc",
    "ietf",
    "pw",
    "cyberpower",
    "eaton_ats",
    "raritan",
    "tripplite",
    "bestpower",
    "netvision",
]

COMMON_SERIAL_DRIVERS: list[str] = [
    "nutdrv_qx",
    "apcsmart",
    "blazer_ser",
    "bcmxcp",
    "mge-shut",
    "riello_ser",
    "tripplite",
    "upscode2",
    "genericups",
    "solis",
    "powerpanel",
    "bestups",
]

_LSUSB_RE = re.compile(
    r"^Bus\s+(\d+)\s+Device\s+(\d+):\s+ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\s*(.*)$"
)


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


def parse_lsusb_output(stdout: str) -> list[RawUsbDevice]:
    """Parse standard `lsusb` output lines."""
    devices: list[RawUsbDevice] = []
    for raw_line in stdout.splitlines():
        m = _LSUSB_RE.match(raw_line.strip())
        if not m:
            continue
        bus, dev, vid, pid, desc = m.groups()
        devices.append(
            RawUsbDevice(
                bus=bus,
                device=dev,
                vendor_id=vid.lower(),
                product_id=pid.lower(),
                description=desc.strip() or f"USB {vid}:{pid}",
            )
        )
    return devices


def _read_sysfs_text(path: Path) -> str | None:
    try:
        if path.is_file():
            txt = path.read_text(encoding="utf-8", errors="replace").strip()
            return txt or None
    except OSError:
        return None
    return None


def _read_usb_meta_for_tty(tty_name: str, sys_tty_root: Path) -> dict[str, str | None]:
    meta: dict[str, str | None] = {
        "vendor_id": None,
        "product_id": None,
        "manufacturer": None,
        "product": None,
        "serial": None,
    }
    dev_link = sys_tty_root / tty_name / "device"
    if not dev_link.exists():
        return meta

    try:
        cur = dev_link.resolve()
    except OSError:
        return meta

    for _ in range(8):
        vid = _read_sysfs_text(cur / "idVendor")
        pid = _read_sysfs_text(cur / "idProduct")
        if vid and pid:
            meta["vendor_id"] = vid.lower()
            meta["product_id"] = pid.lower()
            meta["manufacturer"] = _read_sysfs_text(cur / "manufacturer")
            meta["product"] = _read_sysfs_text(cur / "product")
            meta["serial"] = _read_sysfs_text(cur / "serial")
            return meta
        if cur.parent == cur:
            break
        cur = cur.parent
    return meta


def discover_serial_devices(
    dev_root: Path = Path("/dev"),
    sys_tty_root: Path = Path("/sys/class/tty"),
) -> list[SerialDeviceInfo]:
    """Discover passed-through serial and USB-serial TTY devices inside the container."""
    if not dev_root.exists():
        return []

    by_id_map: dict[str, str] = {}
    by_id_dir = dev_root / "serial" / "by-id"
    if by_id_dir.exists():
        for entry in sorted(by_id_dir.iterdir()):
            try:
                real = str(entry.resolve())
                by_id_map.setdefault(real, str(entry))
            except OSError:
                continue

    candidates: list[Path] = []
    # Prefer stable udev aliases first, then by-id, then raw USB/ACM/serial nodes
    for p in sorted(dev_root.glob("webnut-ups-*")):
        candidates.append(p)
    if by_id_dir.exists():
        for p in sorted(by_id_dir.iterdir()):
            candidates.append(p)
    for pattern in ("ttyUSB*", "ttyACM*"):
        for p in sorted(dev_root.glob(pattern)):
            candidates.append(p)
    for p in sorted(dev_root.glob("ttyS*")):
        # Include hardware/active ttyS* nodes backed by a device driver in sysfs
        if (sys_tty_root / p.name / "device").exists():
            candidates.append(p)

    results: list[SerialDeviceInfo] = []
    seen_real: set[str] = set()

    for cand in candidates:
        if not cand.exists():
            continue
        try:
            real_path = str(cand.resolve())
        except OSError:
            real_path = str(cand)

        if real_path in seen_real:
            continue
        seen_real.add(real_path)

        tty_name = Path(real_path).name
        meta = _read_usb_meta_for_tty(tty_name, sys_tty_root)
        idx = len(results) + 1
        raw_label = meta["product"] or meta["manufacturer"] or cand.name
        accessible = os.access(real_path, os.R_OK | os.W_OK)

        results.append(
            SerialDeviceInfo(
                path=str(cand),
                real_path=real_path,
                by_id_path=by_id_map.get(real_path),
                suggested_name=_slugify_ups_name(raw_label, idx),
                vendor_id=meta["vendor_id"],
                product_id=meta["product_id"],
                manufacturer=meta["manufacturer"],
                product=meta["product"],
                serial=meta["serial"],
                accessible=accessible,
            )
        )

    return results


async def scan_raw_usb(timeout: float = 10.0) -> list[RawUsbDevice]:
    """Run `lsusb` safely without a shell and parse raw USB devices."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "lsusb",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("lsusb binary not found (install usbutils).") from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"lsusb timed out after {timeout:.0f}s") from exc

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 and not stdout.strip():
        raise RuntimeError(stderr or f"lsusb exited with code {proc.returncode}")
    return parse_lsusb_output(stdout)


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


async def scan_all_hardware() -> HardwareScanResponse:
    """Discover NUT USB UPSes, passed-through serial ports, and raw USB devices."""
    warnings: list[str] = []
    nut_usb: list[ScannedUsbDevice] = []
    raw_usb: list[RawUsbDevice] = []

    try:
        nut_usb = await scan_usb_ups()
    except RuntimeError as exc:
        warnings.append(f"nut-scanner: {exc}")

    serial_devices = discover_serial_devices()

    try:
        raw_usb = await scan_raw_usb()
    except RuntimeError as exc:
        warnings.append(f"lsusb: {exc}")

    return HardwareScanResponse(
        nut_usb_devices=nut_usb,
        serial_devices=serial_devices,
        raw_usb_devices=raw_usb,
        serial_drivers=list(COMMON_SERIAL_DRIVERS),
        warnings=warnings,
    )


def parse_snmp_scanner_output(stdout: str) -> list[ScannedSnmpDevice]:
    """Parse INI-style blocks emitted by `nut-scanner -S`."""
    devices: list[ScannedSnmpDevice] = []
    current: dict[str, str] | None = None

    def flush() -> None:
        if not current:
            return
        port = current.get("port", "").strip()
        if not port:
            return
        idx = len(devices) + 1
        desc = current.get("desc") or ""
        devices.append(
            ScannedSnmpDevice(
                suggested_name=_slugify_ups_name(f"snmp_{port.replace('.', '_')}", idx),
                driver=current.get("driver", "snmp-ups") or "snmp-ups",
                port=port,
                mibs=current.get("mibs") or "auto",
                community=current.get("community") or "public",
                snmp_version=current.get("snmp_version") or "v2c",
                desc=desc,
            )
        )

    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush()
            current = {}
            continue
        if current is not None and "=" in line:
            k, v = line.split("=", 1)
            current[k.strip().lower()] = v.strip().strip('"').strip("'")

    flush()
    return devices


async def scan_snmp_ups(
    start_ip: str,
    end_ip: str,
    community: str = "public",
    snmp_version: str = "v2c",
    timeout: float = 25.0,
) -> list[ScannedSnmpDevice]:
    """Run `nut-scanner -S` safely without a shell to discover SNMP UPS units."""
    args = [
        "nut-scanner",
        "-S",
        "-s",
        start_ip.strip(),
        "-e",
        end_ip.strip(),
        "-c",
        community.strip() or "public",
    ]
    if snmp_version in ("v1", "v2c", "v3"):
        args.extend(["-v", snmp_version.lstrip("v")])

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("nut-scanner binary not found.") from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"SNMP scan timed out after {timeout:.0f}s") from exc

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace").strip()
    devices = parse_snmp_scanner_output(stdout)
    if proc.returncode != 0 and not devices:
        raise RuntimeError(stderr or f"nut-scanner -S exited with code {proc.returncode}")
    return devices
