"""Constants for Oura OpenClaw."""

from __future__ import annotations

DOMAIN = "oura_openclaw"
NAME = "Oura OpenClaw"

API_BASE = "https://api.ouraring.com"

CONF_API_TOKEN = "api_token"  # nosec B105
CONF_TOKEN_FILE = "token_file"  # nosec B105
CONF_DAYS = "days"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_DAYS = 7
DEFAULT_SCAN_INTERVAL = 60 * 60
DEFAULT_MAX_PAGES = 20

ATTR_REPORT = "report"
ATTR_SOURCE_DAY = "source_day"
ATTR_LATEST_DAYS = "latest_days"
ATTR_SYNCED_AT = "synced_at"
ATTR_RANGE = "range"
