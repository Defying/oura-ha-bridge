"""Coordinator for Oura HA Bridge."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    OuraApiClient,
    OuraApiError,
    OuraAuthError,
    OuraBundle,
    async_read_token_file,
    build_metrics,
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
)

_LOGGER = logging.getLogger(__name__)


async def async_get_entry_token(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the configured token for an entry."""

    token = entry.data.get(CONF_API_TOKEN)
    if isinstance(token, str) and token.strip():
        return token.strip()

    token_file = entry.data.get(CONF_TOKEN_FILE)
    if isinstance(token_file, str) and token_file.strip():
        path = resolve_token_file_path(hass.config.path(), token_file.strip())
        return await async_read_token_file(path)

    raise ConfigEntryAuthFailed("No Oura token or token file is configured")


def entry_option(entry: ConfigEntry, key: str, default: Any) -> Any:
    """Return an option, falling back to config entry data and then default."""

    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


class OuraHABridgeCoordinator(DataUpdateCoordinator[OuraBundle]):
    """Fetch Oura data for Home Assistant entities."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""

        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=dt.timedelta(
                seconds=int(
                    entry_option(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                )
            ),
            always_update=False,
        )

    async def _async_update_data(self) -> OuraBundle:
        """Fetch the latest Oura bundle."""

        try:
            token = await async_get_entry_token(self.hass, self.entry)
            client = OuraApiClient(async_get_clientsession(self.hass), token)
            raw = await client.async_fetch_bundle(
                days=int(entry_option(self.entry, CONF_DAYS, DEFAULT_DAYS)),
                now=dt_util.now().date(),
            )
        except OuraAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except OuraApiError as exc:
            raise UpdateFailed(str(exc)) from exc
        return build_metrics(raw, dt_util.now().date())
