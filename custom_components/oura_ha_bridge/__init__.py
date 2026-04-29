"""Home Assistant integration for Oura HA Bridge."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import OuraHABridgeCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

SERVICE_REFRESH = "refresh"
ATTR_ENTRY_ID = "entry_id"

REFRESH_SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration-level services."""

    hass.data.setdefault(DOMAIN, {})

    async def async_handle_refresh(call: ServiceCall) -> None:
        entry_id = call.data.get(ATTR_ENTRY_ID)
        coordinators = hass.data.get(DOMAIN, {})
        for current_entry_id, coordinator in list(coordinators.items()):
            if entry_id and entry_id != current_entry_id:
                continue
            await coordinator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH,
            async_handle_refresh,
            schema=REFRESH_SERVICE_SCHEMA,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Oura HA Bridge from a config entry."""

    coordinator = OuraHABridgeCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Oura HA Bridge config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload an Oura HA Bridge config entry."""

    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
