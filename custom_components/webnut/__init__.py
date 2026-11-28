"""The WebNUT integration: UPS telemetry, power recovery orchestration, and WoL hosts."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WebNutApiClient
from .const import CONF_USE_SSL, CONF_VERIFY_SSL, PLATFORMS
from .coordinator import WebNutConfigEntry, WebNutCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: WebNutConfigEntry) -> bool:
    """Set up WebNUT from a config entry."""
    verify_ssl = bool(entry.data.get(CONF_VERIFY_SSL, True))
    client = WebNutApiClient(
        async_get_clientsession(hass, verify_ssl=verify_ssl),
        host=entry.data[CONF_HOST],
        port=int(entry.data[CONF_PORT]),
        api_key=entry.data.get(CONF_API_KEY),
        use_ssl=bool(entry.data.get(CONF_USE_SSL, False)),
        verify_ssl=verify_ssl,
    )
    coordinator = WebNutCoordinator(hass, entry, client, instance_id=str(entry.unique_id))
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WebNutConfigEntry) -> bool:
    """Unload a WebNUT config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_options(hass: HomeAssistant, entry: WebNutConfigEntry) -> None:
    coordinator = getattr(entry, "runtime_data", None)
    if (
        coordinator is not None
        and dict(entry.options) == coordinator.applied_options
        and dict(entry.data) == coordinator.applied_data
    ):
        return
    await hass.config_entries.async_reload(entry.entry_id)
