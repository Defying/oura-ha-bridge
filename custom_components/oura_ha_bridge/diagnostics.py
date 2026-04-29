"""Diagnostics for Oura HA Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_TOKEN, CONF_TOKEN_FILE, DOMAIN

TO_REDACT = {CONF_API_TOKEN, CONF_TOKEN_FILE}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry without exposing raw health data."""

    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = getattr(coordinator, "data", None)
    return {
        "entry": async_redact_data(entry.data, TO_REDACT),
        "options": dict(entry.options),
        "latest_days": data.latest_days if data else {},
        "metrics_present": sorted(
            key
            for key, value in (data.metrics.items() if data else [])
            if value is not None
        ),
        "synced_at": data.synced_at if data else None,
    }
