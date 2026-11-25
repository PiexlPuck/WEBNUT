"""Button platform for WebNUT (Wake-on-LAN per managed host)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import WebNutError
from .coordinator import WebNutConfigEntry, WebNutCoordinator
from .entity import WebNutHostEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebNutConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known_hosts: set[int] = set()

    @callback
    def _add_new() -> None:
        new = []
        for host_id in coordinator.data.hosts:
            if host_id not in known_hosts:
                known_hosts.add(host_id)
                new.append(WebNutWakeButton(coordinator, host_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class WebNutWakeButton(WebNutHostEntity, ButtonEntity):
    """Send a tracked Wake-on-LAN sequence via WebNUT."""

    _attr_translation_key = "wake"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: WebNutCoordinator, host_id: int) -> None:
        super().__init__(coordinator, host_id, "wake")

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.async_wake_host(self.host_id)
        except WebNutError as err:
            raise HomeAssistantError(f"WebNUT wake request failed: {err}") from err
        await self.coordinator.async_request_refresh()
