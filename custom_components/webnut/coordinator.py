"""DataUpdateCoordinator for WebNUT."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
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
        self.server_version: str | None = None

    async def _async_update_data(self) -> WebNutData:
        try:
            raw = await self.client.async_get_state()
        except WebNutAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WebNutError as err:
            raise UpdateFailed(str(err)) from err

        self.server_version = raw.get("version")
        return WebNutData(
            raw=raw,
            ups={str(u["name"]): u for u in raw.get("ups", []) if u.get("name")},
            hosts={int(h["id"]): h for h in raw.get("hosts", [])},
        )
