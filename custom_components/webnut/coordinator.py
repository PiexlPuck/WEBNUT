"""DataUpdateCoordinator for WebNUT."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WebNutApiClient, WebNutAuthError, WebNutError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


@dataclass
class WebNutData:
    """Parsed WebNUT state indexed for fast entity lookups."""

    raw: dict[str, Any]
    ups: dict[str, dict[str, Any]]
    hosts: dict[int, dict[str, Any]]


type WebNutConfigEntry = ConfigEntry[WebNutCoordinator]


class WebNutCoordinator(DataUpdateCoordinator[WebNutData]):
    """Polls /api/integrations/homeassistant/state."""

    config_entry: WebNutConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: WebNutConfigEntry,
        client: WebNutApiClient,
        instance_id: str,
    ) -> None:
        interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.instance_id = instance_id
        self.instance_name: str = "WebNUT"
        self.server_version: str | None = None
        self._last_auto_title: str | None = None
        self.applied_data: dict[str, Any] = dict(entry.data)
        self.applied_options: dict[str, Any] = dict(entry.options)

    def _sync_instance_identity(self) -> None:
        """Keep HA's server device name and auto config entry title aligned with WebNUT."""
        try:
            dev_reg = dr.async_get(self.hass)
            device = dev_reg.async_get_device(identifiers={(DOMAIN, self.instance_id)})
            if device is not None and (
                device.name != self.instance_name or device.sw_version != self.server_version
            ):
                dev_reg.async_update_device(
                    device.id,
                    name=self.instance_name,
                    sw_version=self.server_version,
                )

            host = str(self.config_entry.data.get(CONF_HOST, "")).strip()
            desired_title = f"{self.instance_name} ({host})" if host else self.instance_name
            current_title = self.config_entry.title
            auto_candidates = {"WebNUT", f"WebNUT ({host})" if host else "WebNUT"}
            if self._last_auto_title:
                auto_candidates.add(self._last_auto_title)

            if current_title == desired_title:
                self._last_auto_title = desired_title
            elif current_title in auto_candidates or (
                host and current_title.endswith(f" ({host})")
            ):
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    title=desired_title,
                )
                self._last_auto_title = desired_title
        except Exception as err:  # pragma: no cover - defensive guard in HA runtime
            _LOGGER.debug("Skipping HA instance identity sync: %s", err)

    async def _async_update_data(self) -> WebNutData:
        try:
            raw = await self.client.async_get_state()
        except WebNutAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WebNutError as err:
            raise UpdateFailed(str(err)) from err

        self.server_version = raw.get("version")
        self.instance_name = str(raw.get("instance_name") or "").strip() or "WebNUT"
        self._sync_instance_identity()
        return WebNutData(
            raw=raw,
            ups={str(u["name"]): u for u in raw.get("ups", []) if u.get("name")},
            hosts={int(h["id"]): h for h in raw.get("hosts", [])},
        )
