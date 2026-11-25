"""Constants for the WebNUT Home Assistant integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "webnut"
MANUFACTURER = "WebNUT"

CONF_USE_SSL = "use_ssl"
CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 8765
DEFAULT_SCAN_INTERVAL = 10
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 300

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

ORCHESTRATOR_STATES = ["normal", "on_battery", "stabilizing", "recovering"]
WAKE_STATES = [
    "idle",
    "waiting_stagger",
    "waiting_battery",
    "waking",
    "retrying",
    "wake_succeeded",
    "wake_failed",
]
