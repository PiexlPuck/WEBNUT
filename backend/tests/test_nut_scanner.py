"""Tests for nut-scanner, lsusb, serial device discovery, and NUT TCP protocol helpers."""

from __future__ import annotations

from pathlib import Path
import pytest

from app.nut.client import build_ups_telemetry, parse_nut_list_line
from app.nut.scanner import (
    discover_serial_devices,
    parse_lsusb_output,
    parse_nut_scanner_output,
    scan_all_hardware,
)


def test_parse_nut_scanner_output() -> None:
    sample = """
[nutdev1]
\tdriver = "usbhid-ups"
\tport = "auto"
\tvendorid = "051D"
\tproductid = "0002"
\tproduct = "Back-UPS RS 1500G"
\tserial = "3B1824X12345"
\tvendor = "American Power Conversion"
\tbus = "001"
\tdevice = "004"

[nutdev2]
\tdriver = "blazer_usb"
\tport = "auto"
\tvendorid = "0665"
\tproductid = "5161"
"""
    devices = parse_nut_scanner_output(sample)
    assert len(devices) == 2

    d1 = devices[0]
    assert d1.driver == "usbhid-ups"
    assert d1.port == "auto"
    assert d1.vendorid == "051D"
    assert d1.productid == "0002"
    assert d1.product == "Back-UPS RS 1500G"
    assert d1.serial == "3B1824X12345"
    assert d1.suggested_name == "back-ups_rs_1500g"

    d2 = devices[1]
    assert d2.driver == "blazer_usb"
    assert d2.suggested_name == "ups2"


def test_nut_tcp_line_and_telemetry_building() -> None:
    ups_parts = parse_nut_list_line('UPS apc1 "Main Rack UPS"', "UPS ")
    assert ups_parts == ["UPS", "apc1", "Main Rack UPS"]

    var_parts = parse_nut_list_line('VAR apc1 battery.charge "98"', "VAR ")
    assert var_parts == ["VAR", "apc1", "battery.charge", "98"]

    telemetry = build_ups_telemetry(
        "apc1",
        "Main Rack UPS",
        {
            "ups.status": "OL CHRG",
            "battery.charge": "98",
            "battery.runtime": "1800",
            "ups.load": "22",
            "input.voltage": "121.4",
            "output.voltage": "121.4",
            "ups.mfr": "APC",
            "ups.model": "Smart-UPS 1500",
        },
    )
    assert telemetry.status == "OL CHRG"
    assert telemetry.status_tokens == ["OL", "CHRG"]
    assert telemetry.battery_charge == 98.0
    assert telemetry.battery_runtime == 1800.0
    assert telemetry.ups_load == 22.0
    assert telemetry.input_voltage == 121.4
    assert telemetry.ups_mfr == "APC"


def test_parse_lsusb_output() -> None:
    sample = """
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 001 Device 004: ID 0403:6001 Future Technology Devices International, Ltd FT232 Serial (UART) IC
Bus 002 Device 002: ID 051d:0002 American Power Conversion Uninterruptible Power Supply
"""
    devices = parse_lsusb_output(sample)
    assert len(devices) == 3
    assert devices[1].bus == "001"
    assert devices[1].device == "004"
    assert devices[1].vendor_id == "0403"
    assert devices[1].product_id == "6001"
    assert "FT232" in devices[1].description


def test_discover_serial_devices_with_sysfs_and_dedup(tmp_path: Path) -> None:
    dev_root = tmp_path / "dev"
    sys_tty = tmp_path / "sys" / "class" / "tty"
    dev_root.mkdir(parents=True)
    (dev_root / "serial" / "by-id").mkdir(parents=True)

    # Create mock ttyUSB0 node and sysfs USB parent metadata
    tty0 = dev_root / "ttyUSB0"
    tty0.write_text("", encoding="utf-8")

    # Also create a stable webnut-ups-apc symlink pointing to ttyUSB0 (if symlinks supported)
    usb_device_dir = tmp_path / "sys" / "devices" / "pci0" / "usb1" / "1-1"
    tty_iface_dir = usb_device_dir / "iface0" / "ttyUSB0"
    tty_iface_dir.mkdir(parents=True)
    (usb_device_dir / "idVendor").write_text("0403\n", encoding="utf-8")
    (usb_device_dir / "idProduct").write_text("6001\n", encoding="utf-8")
    (usb_device_dir / "manufacturer").write_text("FTDI\n", encoding="utf-8")
    (usb_device_dir / "product").write_text("FT232R USB UART\n", encoding="utf-8")
    (usb_device_dir / "serial").write_text("A12345\n", encoding="utf-8")

    sys_tty_0 = sys_tty / "ttyUSB0"
    sys_tty_0.mkdir(parents=True)
    try:
        (sys_tty_0 / "device").symlink_to(tty_iface_dir)
        (dev_root / "webnut-ups-apc").symlink_to(tty0)
        (dev_root / "serial" / "by-id" / "usb-FTDI_FT232R-if00").symlink_to(tty0)
        symlinks_ok = True
    except OSError:
        symlinks_ok = False

    found = discover_serial_devices(dev_root=dev_root, sys_tty_root=sys_tty)
    assert len(found) == 1
    d = found[0]
    if symlinks_ok:
        # Must prefer stable webnut-ups-* symlink and deduplicate ttyUSB0 / by-id
        assert d.path.endswith("webnut-ups-apc")
        assert d.by_id_path is not None and "usb-FTDI_FT232R-if00" in d.by_id_path
        assert d.vendor_id == "0403"
        assert d.product_id == "6001"
        assert d.manufacturer == "FTDI"
        assert d.serial == "A12345"


@pytest.mark.asyncio
async def test_scan_all_hardware_returns_warnings_gracefully(monkeypatch) -> None:
    async def _fail_usb():
        raise RuntimeError("nut-scanner missing")

    async def _fail_lsusb():
        raise RuntimeError("lsusb missing")

    monkeypatch.setattr("app.nut.scanner.scan_usb_ups", _fail_usb)
    monkeypatch.setattr("app.nut.scanner.scan_raw_usb", _fail_lsusb)
    monkeypatch.setattr("app.nut.scanner.discover_serial_devices", lambda: [])

    res = await scan_all_hardware()
    assert res.nut_usb_devices == []
    assert res.serial_devices == []
    assert res.raw_usb_devices == []
    assert len(res.serial_drivers) >= 5
    assert len(res.warnings) == 2

