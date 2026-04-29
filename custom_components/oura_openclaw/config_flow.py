"""Config flow for Oura OpenClaw."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    OuraApiClient,
    OuraApiError,
    OuraAuthError,
    async_read_token_file,
    resolve_token_file_path,
)
from .const import (
    CONF_API_TOKEN,
    CONF_DAYS,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN_FILE,
    DEFAULT_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NAME,
)


def user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the config flow schema."""

    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(CONF_API_TOKEN, default=defaults.get(CONF_API_TOKEN, "")): str,
            vol.Optional(
                CONF_TOKEN_FILE, default=defaults.get(CONF_TOKEN_FILE, "")
            ): str,
            vol.Required(
                CONF_DAYS, default=defaults.get(CONF_DAYS, DEFAULT_DAYS)
            ): vol.All(int, vol.Range(min=1, max=120)),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(int, vol.Range(min=300, max=86400)),
        }
    )


async def async_validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate token configuration with Oura."""

    token = str(data.get(CONF_API_TOKEN) or "").strip()
    token_file = str(data.get(CONF_TOKEN_FILE) or "").strip()
    if token and token_file:
        raise ValueError("choose either api token or token file, not both")
    if not token and not token_file:
        raise ValueError("missing token source")
    if token_file:
        token = await async_read_token_file(
            resolve_token_file_path(hass.config.path(), token_file)
        )

    client = OuraApiClient(async_get_clientsession(hass), token)
    await client.async_list_documents(
        "/v2/usercollection/ring_battery_level", {"latest": "true"}
    )


class OuraOpenClawConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an Oura OpenClaw config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""

        return OuraOpenClawOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle the initial config flow step."""

        errors: dict[str, str] = {}

        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            data = dict(user_input)
            data[CONF_API_TOKEN] = str(data.get(CONF_API_TOKEN) or "").strip()
            data[CONF_TOKEN_FILE] = str(data.get(CONF_TOKEN_FILE) or "").strip()
            if not data[CONF_API_TOKEN]:
                data.pop(CONF_API_TOKEN, None)
            if not data[CONF_TOKEN_FILE]:
                data.pop(CONF_TOKEN_FILE, None)
            try:
                await async_validate_input(self.hass, data)
            except ValueError:
                errors["base"] = "invalid_token_source"
            except OuraAuthError:
                errors["base"] = "auth"
            except OuraApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=NAME, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=user_schema(user_input),
            errors=errors,
        )


class OuraOpenClawOptionsFlow(config_entries.OptionsFlow):
    """Handle Oura OpenClaw options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Manage integration options."""

        if user_input is not None:
            return self.async_create_entry(data=user_input)

        merged = {
            CONF_DAYS: self.config_entry.options.get(
                CONF_DAYS, self.config_entry.data.get(CONF_DAYS, DEFAULT_DAYS)
            ),
            CONF_SCAN_INTERVAL: self.config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DAYS, default=merged[CONF_DAYS]): vol.All(
                        int, vol.Range(min=1, max=120)
                    ),
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=merged[CONF_SCAN_INTERVAL]
                    ): vol.All(int, vol.Range(min=300, max=86400)),
                }
            ),
        )
