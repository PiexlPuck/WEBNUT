"""Tests for nut-scanner output parsing and NUT TCP protocol helpers."""

from __future__ import annotations

from app.nut.client import build_ups_telemetry, parse_nut_list_line
from app.nut.scanner import parse_nut_scanner_output


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
