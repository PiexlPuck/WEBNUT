"""Config flow for the WebNUT integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    WebNutApiClient,
    WebNutAuthError,
    WebNutConnectionError,
    WebNutDisabledError,
    WebNutError,
)
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_USE_SSL,
    CONF_VERIFY_SSL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _user_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_API_KEY, default=defaults.get(CONF_API_KEY, "")): str,
            vol.Optional(CONF_USE_SSL, default=defaults.get(CONF_USE_SSL, False)): bool,
            vol.Optional(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)): bool,
        }
    )


class WebNutConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for WebNUT."""

    VERSION = 1

    async def _async_validate(self, data: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        verify_ssl = bool(data.get(CONF_VERIFY_SSL, True))
        client = WebNutApiClient(
            async_get_clientsession(self.hass, verify_ssl=verify_ssl),
            host=str(data[CONF_HOST]),
            port=int(data[CONF_PORT]),
            api_key=str(data.get(CONF_API_KEY) or ""),
            use_ssl=bool(data.get(CONF_USE_SSL, False)),
            verify_ssl=verify_ssl,
        )
        try:
            info = await client.async_get_info()
        except WebNutAuthError:
            return None, "invalid_auth"
        except WebNutDisabledError:
            return None, "ha_disabled"
        except WebNutConnectionError:
            return None, "cannot_connect"
        except WebNutError:
            _LOGGER.exception("Unexpected WebNUT response")
            return None, "unknown"
        if not info.get("instance_id"):
            return None, "unknown"
        return info, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_HOST] = str(user_input[CONF_HOST]).strip()
            info, error = await self._async_validate(user_input)
            if error:
                errors["base"] = error
            else:
                assert info is not None
                await self.async_set_unique_id(str(info["instance_id"]))
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                        CONF_API_KEY: user_input.get(CONF_API_KEY, ""),
                    }
                )
                instance_name = str(info.get("name") or "WebNUT").strip() or "WebNUT"
                return self.async_create_entry(
                    title=f"{instance_name} ({user_input[CONF_HOST]})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_API_KEY: user_input.get(CONF_API_KEY, "")}
            info, error = await self._async_validate(data)
            if error:
                errors["base"] = error
            else:
                assert info is not None
                await self.async_set_unique_id(str(info["instance_id"]))
                self._abort_if_unique_id_mismatch(reason="wrong_instance")
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
            description_placeholders={"host": str(entry.data.get(CONF_HOST, ""))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return WebNutOptionsFlow()


class WebNutOptionsFlow(OptionsFlow):
    """Options for polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = int(self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                    )
                }
            ),
        )
