"""Small async Oura API v2 client and health summary helpers."""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from dataclasses import dataclass
from typing import Any

from .const import API_BASE, DEFAULT_MAX_PAGES

DATE_ENDPOINTS = {
    "daily_sleep": "/v2/usercollection/daily_sleep",
    "daily_readiness": "/v2/usercollection/daily_readiness",
    "daily_activity": "/v2/usercollection/daily_activity",
    "daily_spo2": "/v2/usercollection/daily_spo2",
    "daily_stress": "/v2/usercollection/daily_stress",
    "daily_resilience": "/v2/usercollection/daily_resilience",
    "sleep_time": "/v2/usercollection/sleep_time",
    "sleep": "/v2/usercollection/sleep",
    "workout": "/v2/usercollection/workout",
    "session": "/v2/usercollection/session",
}

TIMESERIES_ENDPOINTS = {
    "ring_battery_level": "/v2/usercollection/ring_battery_level",
}


class OuraApiError(RuntimeError):
    """Base Oura API error."""


class OuraAuthError(OuraApiError):
    """Oura rejected the configured token."""


class OuraRateLimitError(OuraApiError):
    """Oura rate-limited the request."""


class OuraTokenFileError(OuraApiError):
    """Configured token file could not be read."""


@dataclass(frozen=True)
class OuraBundle:
    """Fetched Oura documents and derived metrics."""

    raw: dict[str, Any]
    metrics: dict[str, Any]
    latest_days: dict[str, str]
    report: str
    synced_at: str


async def async_read_token_file(path: str) -> str:
    """Read an Oura token from a local file."""

    try:
        token = await asyncio.to_thread(_read_token_file, path)
    except OSError as exc:
        raise OuraTokenFileError(str(exc)) from exc
    if not token:
        raise OuraTokenFileError("token file is empty")
    return token


def _read_token_file(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.readline().strip()


def resolve_token_file_path(config_path: str, path: str) -> str:
    """Resolve a token file path relative to the Home Assistant config dir."""

    if os.path.isabs(path):
        return path
    return os.path.join(config_path, path)


def date_range(days: int, now: dt.date) -> tuple[str, str]:
    """Return Oura API start/end dates with an exclusive tomorrow end date."""

    end = now + dt.timedelta(days=1)
    start = end - dt.timedelta(days=max(days, 1))
    return start.isoformat(), end.isoformat()


class OuraApiClient:
    """Async Oura API v2 client backed by Home Assistant's aiohttp session."""

    def __init__(
        self, session: Any, token: str, *, max_pages: int = DEFAULT_MAX_PAGES
    ) -> None:
        self._session = session
        self._token = token
        self._max_pages = max_pages

    async def async_request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Request a JSON payload from Oura."""

        clean_params = {
            key: value for key, value in (params or {}).items() if value is not None
        }
        headers = {"Authorization": f"Bearer {self._token}"}
        async with self._session.get(
            API_BASE + path, params=clean_params, headers=headers
        ) as response:
            if response.status == 401:
                raise OuraAuthError("Oura rejected the configured token")
            if response.status == 403:
                raise OuraAuthError(
                    "Oura API returned 403; subscription or API access may be unavailable"
                )
            if response.status == 429:
                retry_after = response.headers.get("Retry-After", "unknown")
                raise OuraRateLimitError(
                    f"Oura API rate limit exceeded; Retry-After={retry_after}"
                )
            if response.status >= 400:
                body = await response.text()
                raise OuraApiError(f"Oura API HTTP {response.status}: {body[:500]}")
            payload = await response.json(content_type=None)
            if not isinstance(payload, dict):
                raise OuraApiError("Oura API returned a non-object JSON payload")
            return payload

    async def async_list_documents(
        self, path: str, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Fetch a paginated Oura document collection."""

        out: list[dict[str, Any]] = []
        next_token = None
        for _page in range(self._max_pages):
            page_params = dict(params)
            if next_token:
                page_params["next_token"] = next_token
            payload = await self.async_request(path, page_params)
            data = payload.get("data", [])
            if isinstance(data, list):
                out.extend(item for item in data if isinstance(item, dict))
            next_token = payload.get("next_token")
            if not next_token:
                break
        else:
            raise OuraApiError(
                f"Oura API pagination exceeded max_pages={self._max_pages}"
            )
        return out

    async def async_fetch_bundle(self, *, days: int, now: dt.date) -> dict[str, Any]:
        """Fetch the Oura documents used by Home Assistant sensors."""

        start_date, end_date = date_range(days, now)
        bundle: dict[str, Any] = {
            "range": {"start_date": start_date, "end_date": end_date},
            "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        for endpoint, path in DATE_ENDPOINTS.items():
            bundle[endpoint] = await self.async_list_documents(
                path, {"start_date": start_date, "end_date": end_date}
            )
        bundle["ring_battery_level"] = await self.async_list_documents(
            TIMESERIES_ENDPOINTS["ring_battery_level"], {"latest": "true"}
        )
        return bundle


def numeric(value: Any) -> float | None:
    """Convert a value to float when safe."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_by_day(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the latest row with a day field."""

    dated = [row for row in rows if isinstance(row.get("day"), str)]
    return sorted(dated, key=lambda row: row["day"])[-1] if dated else None


def by_day(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index rows by Oura day."""

    return {row["day"]: row for row in rows if isinstance(row.get("day"), str)}


def main_sleep_for_day(
    rows: list[dict[str, Any]], day: str | None
) -> dict[str, Any] | None:
    """Return the main sleep row for a day."""

    candidates = [row for row in rows if row.get("day") == day]
    if not candidates:
        return None
    long_sleep = [
        row for row in candidates if row.get("type") in ("long_sleep", "long")
    ]
    pool = long_sleep or candidates
    return sorted(pool, key=lambda row: numeric(row.get("time_in_bed")) or 0)[-1]


def latest_battery(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the latest battery row."""

    battery_rows = [row for row in rows if isinstance(row, dict)]
    if not battery_rows:
        return None
    return sorted(
        battery_rows, key=lambda row: numeric(row.get("timestamp_unix")) or 0
    )[-1]


def days_old(day: str | None, now: dt.date) -> int | None:
    """Return whole days between a day string and now."""

    if not day:
        return None
    try:
        return (now - dt.date.fromisoformat(day[:10])).days
    except ValueError:
        return None


def confidence(
    metrics: dict[str, Any], latest_days: dict[str, str], now: dt.date
) -> tuple[str, list[str]]:
    """Estimate report confidence from freshness."""

    reasons: list[str] = []
    for endpoint, label in (
        ("daily_sleep", "sleep"),
        ("daily_readiness", "readiness"),
        ("daily_activity", "activity"),
        ("daily_stress", "stress"),
    ):
        age = days_old(latest_days.get(endpoint), now)
        if age is None:
            reasons.append(f"no {label} data")
        elif age > 2:
            reasons.append(f"{label} stale by {age}d")
    if not reasons:
        return "high", ["daily docs are fresh"]
    if len(reasons) <= 2:
        return "medium", reasons
    return "low", reasons


def build_metrics(bundle: dict[str, Any], now: dt.date) -> OuraBundle:
    """Derive Home Assistant sensor values from a fetched bundle."""

    latest_days: dict[str, str] = {}
    for endpoint, rows in bundle.items():
        if isinstance(rows, list):
            latest = latest_by_day([row for row in rows if isinstance(row, dict)])
            if latest and isinstance(latest.get("day"), str):
                latest_days[endpoint] = latest["day"]

    candidate_days = sorted(latest_days.values())
    latest_day = candidate_days[-1] if candidate_days else None

    daily_sleep = by_day(bundle.get("daily_sleep", []) or [])
    daily_readiness = by_day(bundle.get("daily_readiness", []) or [])
    daily_activity = by_day(bundle.get("daily_activity", []) or [])
    daily_stress = by_day(bundle.get("daily_stress", []) or [])
    daily_resilience = by_day(bundle.get("daily_resilience", []) or [])
    daily_spo2 = by_day(bundle.get("daily_spo2", []) or [])

    sleep_score_row = latest_by_day(bundle.get("daily_sleep", []) or [])
    readiness_row = latest_by_day(bundle.get("daily_readiness", []) or [])
    activity_row = latest_by_day(bundle.get("daily_activity", []) or [])
    stress_row = latest_by_day(bundle.get("daily_stress", []) or [])
    resilience_row = latest_by_day(bundle.get("daily_resilience", []) or [])
    spo2_row = latest_by_day(bundle.get("daily_spo2", []) or [])
    sleep_detail = main_sleep_for_day(
        bundle.get("sleep", []) or [], latest_days.get("sleep") or latest_day
    )
    battery_row = latest_battery(bundle.get("ring_battery_level", []) or [])

    if latest_day:
        sleep_score_row = daily_sleep.get(latest_day) or sleep_score_row
        readiness_row = daily_readiness.get(latest_day) or readiness_row
        activity_row = daily_activity.get(latest_day) or activity_row
        stress_row = daily_stress.get(latest_day) or stress_row
        resilience_row = daily_resilience.get(latest_day) or resilience_row
        spo2_row = daily_spo2.get(latest_day) or spo2_row

    spo2_average = None
    if spo2_row and isinstance(spo2_row.get("spo2_percentage"), dict):
        spo2_average = spo2_row["spo2_percentage"].get("average")

    metrics: dict[str, Any] = {
        "latest_day": latest_day,
        "readiness_score": maybe_int(
            readiness_row.get("score") if readiness_row else None
        ),
        "temperature_deviation": numeric(
            readiness_row.get("temperature_deviation") if readiness_row else None
        ),
        "sleep_score": maybe_int(
            sleep_score_row.get("score") if sleep_score_row else None
        ),
        "activity_score": maybe_int(
            activity_row.get("score") if activity_row else None
        ),
        "steps": maybe_int(activity_row.get("steps") if activity_row else None),
        "active_calories": maybe_int(
            activity_row.get("active_calories") if activity_row else None
        ),
        "inactivity_alerts": maybe_int(
            activity_row.get("inactivity_alerts") if activity_row else None
        ),
        "stress_summary": stress_row.get("day_summary") if stress_row else None,
        "stress_high": maybe_int(stress_row.get("stress_high") if stress_row else None),
        "recovery_high": maybe_int(
            stress_row.get("recovery_high") if stress_row else None
        ),
        "resilience_level": resilience_row.get("level") if resilience_row else None,
        "spo2_average": numeric(spo2_average),
        "breathing_disturbance_index": numeric(
            spo2_row.get("breathing_disturbance_index") if spo2_row else None
        ),
        "battery_level": maybe_int(battery_row.get("level") if battery_row else None),
        "battery_charging": bool(battery_row.get("charging")) if battery_row else None,
        "battery_timestamp": parse_datetime(
            battery_row.get("timestamp") if battery_row else None
        ),
        "sleep_duration": maybe_int(
            sleep_detail.get("total_sleep_duration") if sleep_detail else None
        ),
        "time_in_bed": maybe_int(
            sleep_detail.get("time_in_bed") if sleep_detail else None
        ),
        "sleep_efficiency": maybe_int(
            sleep_detail.get("efficiency") if sleep_detail else None
        ),
        "average_hrv": maybe_int(
            sleep_detail.get("average_hrv") if sleep_detail else None
        ),
        "lowest_heart_rate": maybe_int(
            sleep_detail.get("lowest_heart_rate") if sleep_detail else None
        ),
        "deep_sleep_duration": maybe_int(
            sleep_detail.get("deep_sleep_duration") if sleep_detail else None
        ),
        "rem_sleep_duration": maybe_int(
            sleep_detail.get("rem_sleep_duration") if sleep_detail else None
        ),
        "bedtime_start": parse_datetime(
            sleep_detail.get("bedtime_start") if sleep_detail else None
        ),
        "bedtime_end": parse_datetime(
            sleep_detail.get("bedtime_end") if sleep_detail else None
        ),
        "synced_at": parse_datetime(bundle.get("fetched_at")),
    }

    conf, reasons = confidence(metrics, latest_days, now)
    metrics["confidence"] = conf
    metrics["confidence_reasons"] = reasons
    report = build_report(metrics, latest_days)
    return OuraBundle(
        raw=bundle,
        metrics=metrics,
        latest_days=latest_days,
        report=report,
        synced_at=str(bundle.get("fetched_at") or ""),
    )


def maybe_int(value: Any) -> int | None:
    """Convert a numeric value to int, preserving None for non-numeric values."""

    number = numeric(value)
    if number is None:
        return None
    return int(round(number))


def parse_datetime(value: Any) -> dt.datetime | None:
    """Parse an Oura timestamp."""

    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def fmt_duration(seconds: Any) -> str:
    """Format seconds for a compact report."""

    value = maybe_int(seconds)
    if value is None:
        return "n/a"
    hours, rem = divmod(value, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def build_report(metrics: dict[str, Any], latest_days: dict[str, str]) -> str:
    """Build a concise text report for HA attributes."""

    lines = [
        f"confidence {metrics.get('confidence')}: {', '.join(metrics.get('confidence_reasons') or [])}",
        f"latest day {metrics.get('latest_day') or 'n/a'}",
    ]
    if metrics.get("readiness_score") is not None:
        lines.append(f"readiness {metrics['readiness_score']}")
    if metrics.get("sleep_score") is not None:
        lines.append(
            f"sleep {metrics['sleep_score']}; {fmt_duration(metrics.get('sleep_duration'))} asleep"
        )
    if metrics.get("activity_score") is not None:
        lines.append(
            f"activity {metrics['activity_score']}; steps {metrics.get('steps') or 'n/a'}"
        )
    if metrics.get("battery_level") is not None:
        lines.append(f"battery {metrics['battery_level']}%")
    if latest_days:
        lines.append(
            "latest docs "
            + ", ".join(
                f"{endpoint}:{day}" for endpoint, day in sorted(latest_days.items())
            )
        )
    return "\n".join(lines)
