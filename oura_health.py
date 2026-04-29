#!/usr/bin/env python3
"""Small OpenClaw-friendly Oura API helper.

The digest command does not persist raw health data. Sync/analyze commands store
raw Oura documents in a local gitignored SQLite database. The API token is read from one of:
  1. OURA_TOKEN environment variable
  2. local token file `data/oura.token` or OURA_TOKEN_FILE
  3. macOS Keychain generic password service `openclaw-oura-api`

Use `oura-health setup-token-file` to avoid Keychain unlock prompts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
import sqlite3
import statistics
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

API_BASE = "https://api.ouraring.com"
KEYCHAIN_SERVICE = os.environ.get("OURA_KEYCHAIN_SERVICE", "openclaw-oura-api")
KEYCHAIN_ACCOUNT = os.environ.get(
    "OURA_KEYCHAIN_ACCOUNT", os.environ.get("USER", "oura")
)
DEFAULT_TZ = os.environ.get("OURA_TZ", "America/New_York")

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
    "heartrate": "/v2/usercollection/heartrate",
    "ring_battery_level": "/v2/usercollection/ring_battery_level",
}

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.environ.get(
    "OURA_DB", os.path.join(PROJECT_DIR, "data", "oura.sqlite3")
)
DEFAULT_TOKEN_FILE = os.environ.get(
    "OURA_TOKEN_FILE", os.path.join(PROJECT_DIR, "data", "oura.token")
)
SCHEMA_VERSION = 1


class OuraError(RuntimeError):
    pass


class MissingToken(OuraError):
    pass


@dataclass(frozen=True)
class KeychainLookup:
    token: str | None
    item_exists: bool
    read_error: str | None = None


@dataclass(frozen=True)
class TokenFileLookup:
    token: str | None
    path: str
    file_exists: bool
    read_error: str | None = None


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def run_security(
    args: list[str], *, input_text: str | None = None, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    cmd = ["/usr/bin/security", *args]
    try:
        # `cmd` starts with fixed /usr/bin/security and shell remains False.
        return subprocess.run(  # nosec B603
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return subprocess.CompletedProcess(
            cmd, 124, stdout, stderr or "security command timed out"
        )


def keychain_read_error(proc: subprocess.CompletedProcess[str]) -> str:
    detail = proc.stderr.strip()
    if detail:
        return detail
    return f"security exited {proc.returncode}; Keychain may be locked or requiring user interaction"


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)


def ensure_private_file(path: str) -> None:
    if os.name != "posix" or path == ":memory:":
        return
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        os.chmod(path, 0o600)
    else:
        os.close(fd)


def ensure_private_existing_file(path: str) -> None:
    if os.name != "posix" or path == ":memory:" or not os.path.exists(path):
        return
    os.chmod(path, 0o600)


def lookup_token_file(path: str | None = None) -> TokenFileLookup:
    path = path or DEFAULT_TOKEN_FILE
    if not path or not os.path.exists(path):
        return TokenFileLookup(None, path, file_exists=False)
    try:
        ensure_private_existing_file(path)
        with open(path, encoding="utf-8") as fh:
            token = fh.readline().strip()
    except OSError as exc:
        return TokenFileLookup(None, path, file_exists=True, read_error=str(exc))
    if not token:
        return TokenFileLookup(
            None, path, file_exists=True, read_error="token file is empty"
        )
    return TokenFileLookup(token, path, file_exists=True)


def lookup_keychain_token(
    service: str = KEYCHAIN_SERVICE, account: str = KEYCHAIN_ACCOUNT
) -> KeychainLookup:
    proc = run_security(["find-generic-password", "-s", service, "-a", account, "-w"])
    if proc.returncode == 0:
        token = proc.stdout.strip()
        return KeychainLookup(token or None, item_exists=bool(token))

    metadata = run_security(["find-generic-password", "-s", service, "-a", account])
    if metadata.returncode == 0:
        return KeychainLookup(
            None, item_exists=True, read_error=keychain_read_error(proc)
        )
    return KeychainLookup(None, item_exists=False, read_error=metadata.stderr.strip())


def get_keychain_token(
    service: str = KEYCHAIN_SERVICE, account: str = KEYCHAIN_ACCOUNT
) -> str | None:
    return lookup_keychain_token(service, account).token


def store_keychain_token(
    token: str, service: str, account: str
) -> subprocess.CompletedProcess[str]:
    # Keep the token out of argv/process listings. `security` prompts when -w is last.
    return run_security(
        [
            "add-generic-password",
            "-U",
            "-s",
            service,
            "-a",
            account,
            "-T",
            "/usr/bin/security",
            "-w",
        ],
        input_text=token + "\n",
    )


def get_token(required: bool = True) -> str | None:
    token = os.environ.get("OURA_TOKEN", "").strip()
    if token:
        return token
    token_file = lookup_token_file()
    if token_file.token:
        return token_file.token
    if required and token_file.file_exists:
        raise MissingToken(
            f"Oura token file exists at {token_file.path!r} but cannot be used: "
            f"{token_file.read_error or 'unknown token file error'}."
        )
    keychain = lookup_keychain_token()
    if keychain.token:
        return keychain.token
    if required:
        if keychain.item_exists:
            raise MissingToken(
                "Oura token exists in macOS Keychain but cannot be read from this session: "
                f"{keychain.read_error or 'unknown Keychain error'}. "
                "Unlock the login keychain or run `oura-health setup-token` from an interactive terminal."
            )
        raise MissingToken(
            "missing Oura API token. Run `oura-health setup-token-file` privately on this Mac, "
            "or set OURA_TOKEN in the environment."
        )
    return None


def prompt_for_token() -> str | None:
    token = getpass.getpass("Oura token: ").strip()
    if not token:
        eprint("no token entered")
        return None
    confirm = getpass.getpass("Paste it again: ").strip()
    if token != confirm:
        eprint("tokens did not match; nothing stored")
        return None
    return token


def setup_token_file(args: argparse.Namespace) -> int:
    print(
        "Paste your Oura personal access token. Input is hidden; it will be stored in a local chmod 600 file."
    )
    token = prompt_for_token()
    if not token:
        return 2
    ensure_parent_dir(args.path)
    try:
        fd = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
        ensure_private_existing_file(args.path)
    except OSError as exc:
        eprint(f"failed to write token file {args.path!r}: {exc}")
        return 1
    print(
        f"stored Oura token file at {args.path!r}; Keychain will not be used while this file exists"
    )
    return 0


def setup_token(args: argparse.Namespace) -> int:
    print(
        "Paste your Oura personal access token. Input is hidden; it will be stored in macOS Keychain."
    )
    token = prompt_for_token()
    if not token:
        return 2

    # Trust /usr/bin/security so the same CLI path can read it from OpenClaw cron.
    proc = store_keychain_token(token, args.service, args.account)
    if proc.returncode != 0:
        eprint(
            proc.stderr.strip()
            or proc.stdout.strip()
            or "failed to write token to Keychain"
        )
        return proc.returncode or 1
    print(
        f"stored Oura token in Keychain service={args.service!r} account={args.account!r}"
    )
    return 0


def token_status(args: argparse.Namespace) -> int:
    if os.environ.get("OURA_TOKEN", "").strip():
        print("token source: OURA_TOKEN environment variable")
        return 0
    token_file = lookup_token_file(args.token_file)
    if token_file.token:
        print(f"token source: local token file path={token_file.path!r}")
        return 0
    if token_file.file_exists:
        print(
            f"token file exists but is not usable path={token_file.path!r}: "
            f"{token_file.read_error or 'unknown token file error'}"
        )
        return 1
    keychain = lookup_keychain_token(args.service, args.account)
    if keychain.token:
        print(
            f"token source: macOS Keychain service={args.service!r} account={args.account!r}"
        )
        return 0
    if keychain.item_exists:
        print(
            "token source: macOS Keychain item exists but password is not readable "
            f"service={args.service!r} account={args.account!r}: "
            f"{keychain.read_error or 'unknown Keychain error'}"
        )
        return 1
    print(
        f"missing token: no OURA_TOKEN env var and no Keychain item service={args.service!r} account={args.account!r}"
    )
    return 1


@dataclass
class OuraClient:
    token: str
    timeout: int = 30
    max_pages: int = 20

    def __post_init__(self) -> None:
        if self.timeout < 1:
            raise ValueError("timeout must be >= 1")
        if self.max_pages < 1:
            raise ValueError("max_pages must be >= 1")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            # API_BASE is fixed HTTPS.
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:1000]
            if exc.code == 401:
                raise OuraError(
                    "Oura API rejected the token (401). Create/store a fresh token."
                ) from exc
            if exc.code == 403:
                raise OuraError(
                    "Oura API returned 403. Subscription/API access may be unavailable."
                ) from exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                raise OuraError(
                    f"Oura API rate limit exceeded (429). Retry-After={retry_after or 'unknown'}."
                ) from exc
            raise OuraError(f"Oura API HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise OuraError(f"Oura API network error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise OuraError("Oura API returned invalid JSON") from exc

    def list_documents(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        next_token = None
        for _page in range(self.max_pages):
            page_params = dict(params)
            if next_token:
                page_params["next_token"] = next_token
            payload = self.get(path, page_params)
            data = payload.get("data", [])
            if isinstance(data, list):
                out.extend(x for x in data if isinstance(x, dict))
            next_token = payload.get("next_token")
            if not next_token:
                break
            time.sleep(0.2)
        else:
            raise OuraError(
                f"Oura API pagination exceeded max_pages={self.max_pages}; refusing partial results"
            )
        return out


def today(tz_name: str = DEFAULT_TZ) -> dt.date:
    tz = ZoneInfo(tz_name) if ZoneInfo else None
    return dt.datetime.now(tz=tz).date() if tz else dt.date.today()


def iso_date(d: dt.date) -> str:
    return d.isoformat()


def date_range(days: int, tz_name: str = DEFAULT_TZ) -> tuple[str, str]:
    if days < 1:
        raise ValueError("days must be >= 1")
    end = today(tz_name) + dt.timedelta(days=1)
    start = end - dt.timedelta(days=days)
    return iso_date(start), iso_date(end)


def fetch_bundle(
    client: OuraClient, days: int, include_timeseries: bool = True
) -> dict[str, Any]:
    start_date, end_date = date_range(days)
    bundle: dict[str, Any] = {
        "range": {"start_date": start_date, "end_date": end_date},
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    for name, path in DATE_ENDPOINTS.items():
        bundle[name] = client.list_documents(
            path, {"start_date": start_date, "end_date": end_date}
        )

    if include_timeseries:
        # For digest purposes, latest battery is more useful than a giant time series.
        for name, path in TIMESERIES_ENDPOINTS.items():
            if name == "ring_battery_level":
                bundle[name] = client.list_documents(path, {"latest": "true"})
            else:
                bundle[name] = []
    return bundle


def by_day(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        day = item.get("day")
        if isinstance(day, str):
            out[day] = item
    return out


def newest(items: list[dict[str, Any]], key: str = "day") -> dict[str, Any] | None:
    filtered = [x for x in items if isinstance(x.get(key), str)]
    if not filtered:
        return None
    return sorted(filtered, key=lambda x: str(x.get(key)))[-1]


def previous_for_day(
    items: list[dict[str, Any]], day: str | None
) -> dict[str, Any] | None:
    if not day:
        return None
    older = [
        x for x in items if isinstance(x.get("day"), str) and str(x.get("day")) < day
    ]
    return sorted(older, key=lambda x: str(x.get("day")))[-1] if older else None


def main_sleep_for_day(
    sleeps: list[dict[str, Any]], day: str | None
) -> dict[str, Any] | None:
    candidates = [x for x in sleeps if x.get("day") == day]
    if not candidates:
        return None
    long = [x for x in candidates if x.get("type") in ("long_sleep", "long")]
    pool = long or candidates
    return sorted(
        pool,
        key=lambda x: int(x.get("time_in_bed") or x.get("total_sleep_duration") or 0),
    )[-1]


def fmt_int(n: Any) -> str | None:
    if n is None:
        return None
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return None


def fmt_score(n: Any, prev: Any = None) -> str | None:
    if n is None:
        return None
    try:
        base = int(round(float(n)))
    except Exception:
        return str(n)
    delta = ""
    if prev is not None:
        try:
            d = base - int(round(float(prev)))
            if d > 0:
                delta = f" (+{d})"
            elif d < 0:
                delta = f" ({d})"
        except (TypeError, ValueError):
            delta = ""
    return f"{base}{delta}"


def fmt_seconds(seconds: Any) -> str | None:
    if seconds is None:
        return None
    try:
        seconds = int(seconds)
    except Exception:
        return None
    if seconds < 0:
        return None
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def parse_isoish(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    s = value.replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def fmt_time(value: Any) -> str | None:
    parsed = parse_isoish(value)
    if not parsed:
        return None
    hour = parsed.hour % 12 or 12
    minute = parsed.minute
    suffix = "a" if parsed.hour < 12 else "p"
    return f"{hour}:{minute:02d}{suffix}"


def fmt_temp(n: Any) -> str | None:
    if n is None:
        return None
    try:
        v = float(n)
    except Exception:
        return None
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}°C"


def fmt_pct(n: Any) -> str | None:
    if n is None:
        return None
    try:
        return f"{float(n):.1f}%"
    except Exception:
        return None


def contributor_watch(
    contribs: Any, *, threshold: int = 75, limit: int = 3
) -> list[str]:
    if not isinstance(contribs, dict):
        return []
    pairs: list[tuple[float, str]] = []
    for key, value in contribs.items():
        if value is None:
            continue
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = threshold
        if score < threshold:
            pairs.append((score, key.replace("_", " ")))
    pairs.sort()
    return [f"{name} {int(round(score))}" for score, name in pairs[:limit]]


def compact_join(parts: list[str | None], sep: str = ", ") -> str:
    return sep.join([p for p in parts if p])


def build_digest(bundle: dict[str, Any], *, days: int) -> str:
    daily_sleep = bundle.get("daily_sleep") or []
    daily_readiness = bundle.get("daily_readiness") or []
    daily_activity = bundle.get("daily_activity") or []
    daily_spo2 = bundle.get("daily_spo2") or []
    daily_stress = bundle.get("daily_stress") or []
    daily_resilience = bundle.get("daily_resilience") or []
    sleeps = bundle.get("sleep") or []
    workouts = bundle.get("workout") or []
    sessions = bundle.get("session") or []
    battery = bundle.get("ring_battery_level") or []

    candidate_days = []
    for collection in (
        daily_sleep,
        daily_readiness,
        daily_activity,
        daily_stress,
        daily_resilience,
        daily_spo2,
    ):
        candidate_days.extend(
            [x.get("day") for x in collection if isinstance(x.get("day"), str)]
        )
    if not candidate_days:
        r = bundle.get("range", {})
        return f"oura health digest: no Oura daily data found for {r.get('start_date')} → {r.get('end_date')}. sync the ring/app and try again."
    day = sorted(candidate_days)[-1]

    ds = by_day(daily_sleep).get(day)
    dr = by_day(daily_readiness).get(day)
    da = by_day(daily_activity).get(day)
    spo2 = by_day(daily_spo2).get(day)
    stress = by_day(daily_stress).get(day)
    resilience = by_day(daily_resilience).get(day)
    sleep_detail = main_sleep_for_day(sleeps, day)

    prev_ds = previous_for_day(daily_sleep, day)
    prev_dr = previous_for_day(daily_readiness, day)
    prev_da = previous_for_day(daily_activity, day)

    pretty_day = day
    try:
        pretty_day = dt.date.fromisoformat(day).strftime("%a %b %-d")
    except ValueError:
        pretty_day = day

    lines = [f"oura health digest — {pretty_day}"]

    if dr:
        bits = [
            f"readiness {fmt_score(dr.get('score'), prev_dr.get('score') if prev_dr else None)}"
        ]
        temp = fmt_temp(dr.get("temperature_deviation"))
        if temp:
            bits.append(f"temp {temp}")
        watch = contributor_watch(dr.get("contributors"))
        if watch:
            bits.append("watch: " + "; ".join(watch))
        lines.append("- " + compact_join(bits, " — "))

    if ds or sleep_detail:
        score = (
            fmt_score(ds.get("score"), prev_ds.get("score") if prev_ds and ds else None)
            if ds
            else None
        )
        detail = sleep_detail or {}
        sleep_bits = [f"sleep {score}" if score else "sleep"]
        duration = fmt_seconds(detail.get("total_sleep_duration"))
        in_bed = fmt_seconds(detail.get("time_in_bed"))
        if duration and in_bed:
            sleep_bits.append(f"{duration} asleep / {in_bed} in bed")
        elif duration:
            sleep_bits.append(f"{duration} asleep")
        bed_start = fmt_time(detail.get("bedtime_start"))
        bed_end = fmt_time(detail.get("bedtime_end"))
        if bed_start and bed_end:
            sleep_bits.append(f"bed {bed_start}–{bed_end}")
        physiology = compact_join(
            [
                f"HRV {fmt_int(detail.get('average_hrv'))}ms"
                if fmt_int(detail.get("average_hrv"))
                else None,
                f"RHR {fmt_int(detail.get('lowest_heart_rate'))}"
                if fmt_int(detail.get("lowest_heart_rate"))
                else None,
                f"eff {fmt_int(detail.get('efficiency'))}%"
                if fmt_int(detail.get("efficiency"))
                else None,
            ]
        )
        if physiology:
            sleep_bits.append(physiology)
        stages = compact_join(
            [
                f"deep {fmt_seconds(detail.get('deep_sleep_duration'))}"
                if fmt_seconds(detail.get("deep_sleep_duration"))
                else None,
                f"REM {fmt_seconds(detail.get('rem_sleep_duration'))}"
                if fmt_seconds(detail.get("rem_sleep_duration"))
                else None,
            ]
        )
        if stages:
            sleep_bits.append(stages)
        watch = contributor_watch(ds.get("contributors") if ds else None)
        if watch:
            sleep_bits.append("watch: " + "; ".join(watch))
        lines.append("- " + compact_join(sleep_bits, " — "))

    if da:
        bits = [
            f"activity {fmt_score(da.get('score'), prev_da.get('score') if prev_da else None)}"
        ]
        steps = fmt_int(da.get("steps"))
        active_cal = fmt_int(da.get("active_calories"))
        alerts = da.get("inactivity_alerts")
        if steps:
            bits.append(f"{steps} steps")
        if active_cal:
            bits.append(f"{active_cal} active cal")
        if alerts is not None:
            bits.append(f"{alerts} inactivity alert" + ("s" if alerts != 1 else ""))
        watch = contributor_watch(da.get("contributors"))
        if watch:
            bits.append("watch: " + "; ".join(watch))
        lines.append("- " + compact_join(bits, " — "))

    if stress or resilience or spo2:
        bits = []
        if stress:
            summary = stress.get("day_summary")
            if summary:
                bits.append(f"stress {summary}")
            if stress.get("stress_high") is not None:
                bits.append(f"high stress {fmt_seconds(stress.get('stress_high'))}")
            if stress.get("recovery_high") is not None:
                bits.append(f"high recovery {fmt_seconds(stress.get('recovery_high'))}")
        if resilience:
            level = resilience.get("level")
            if level:
                bits.append(f"resilience {level}")
        if spo2:
            avg = None
            if isinstance(spo2.get("spo2_percentage"), dict):
                avg = spo2.get("spo2_percentage", {}).get("average")
            pct = fmt_pct(avg)
            if pct:
                bits.append(f"SpO₂ {pct}")
            bdi = spo2.get("breathing_disturbance_index")
            if bdi is not None:
                bits.append(f"BDI {bdi}")
        if bits:
            lines.append("- " + compact_join(bits, " — "))

    today_workouts = [x for x in workouts if x.get("day") == day]
    today_sessions = [x for x in sessions if x.get("day") == day]
    if today_workouts or today_sessions:
        bits = []
        if today_workouts:
            labels = []
            for w in today_workouts[:3]:
                label = str(w.get("activity") or w.get("type") or "workout").replace(
                    "_", " "
                )
                dur = fmt_seconds(w.get("duration"))
                labels.append(f"{label}" + (f" {dur}" if dur else ""))
            if len(today_workouts) > 3:
                labels.append(f"+{len(today_workouts) - 3} more")
            bits.append("workouts: " + "; ".join(labels))
        if today_sessions:
            bits.append(f"sessions: {len(today_sessions)}")
        lines.append("- " + compact_join(bits, " — "))

    battery_rows = [x for x in battery if isinstance(x, dict)]
    if battery_rows:

        def battery_sort_key(row: dict[str, Any]) -> float:
            try:
                return float(row.get("timestamp_unix") or 0)
            except Exception:
                return 0.0

        latest = sorted(battery_rows, key=battery_sort_key)[-1]
        level = latest.get("level")
        if level is not None:
            charging = " charging" if latest.get("charging") else ""
            lines.append(f"- ring battery {level}%{charging}")

    lines.append("_not medical advice; just pattern spotting from Oura data._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Local storage + adaptive analysis
# ---------------------------------------------------------------------------


def ensure_private_sqlite_files(path: str) -> None:
    ensure_private_file(path)
    for suffix in ("-journal", "-wal", "-shm"):
        ensure_private_existing_file(path + suffix)


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(obj: Any) -> str:
    return hashlib.sha256(stable_json(obj).encode("utf-8")).hexdigest()


def document_key(endpoint: str, doc: dict[str, Any]) -> str:
    explicit = doc.get("id") or doc.get("document_id")
    if explicit:
        return str(explicit)
    bits = [
        endpoint,
        str(doc.get("day") or ""),
        str(doc.get("timestamp") or doc.get("bedtime_start") or ""),
    ]
    return ":".join(bits + [content_hash(doc)[:16]])


def document_day(doc: dict[str, Any]) -> str | None:
    day = doc.get("day")
    if isinstance(day, str) and day:
        return day[:10]
    for key in ("timestamp", "bedtime_start", "bedtime_end"):
        parsed = parse_isoish(doc.get(key))
        if parsed:
            return parsed.date().isoformat()
    return None


class OuraStore:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        ensure_parent_dir(path)
        ensure_private_sqlite_files(path)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.migrate()
        ensure_private_sqlite_files(path)

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            create table if not exists meta (
              key text primary key,
              value text not null
            );
            create table if not exists documents (
              endpoint text not null,
              document_id text not null,
              day text,
              timestamp text,
              fetched_at text not null,
              content_hash text not null,
              json text not null,
              primary key (endpoint, document_id)
            );
            create index if not exists idx_documents_endpoint_day on documents(endpoint, day);
            create index if not exists idx_documents_endpoint_ts on documents(endpoint, timestamp);
            create table if not exists sync_runs (
              id integer primary key autoincrement,
              started_at text not null,
              finished_at text,
              days integer not null,
              endpoints text not null,
              inserted integer not null default 0,
              updated integer not null default 0,
              errors text
            );
            """
        )
        cur.execute(
            "insert into meta(key, value) values('schema_version', ?) on conflict(key) do update set value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def upsert_documents(
        self, endpoint: str, docs: list[dict[str, Any]], fetched_at: str
    ) -> tuple[int, int]:
        inserted = updated = 0
        for doc in docs:
            doc_id = document_key(endpoint, doc)
            day = document_day(doc)
            timestamp = (
                doc.get("timestamp")
                or doc.get("bedtime_start")
                or doc.get("bedtime_end")
            )
            if timestamp is not None:
                timestamp = str(timestamp)
            h = content_hash(doc)
            raw = stable_json(doc)
            existing = self.conn.execute(
                "select content_hash from documents where endpoint=? and document_id=?",
                (endpoint, doc_id),
            ).fetchone()
            if existing is None:
                inserted += 1
            elif existing["content_hash"] != h:
                updated += 1
            self.conn.execute(
                """
                insert into documents(endpoint, document_id, day, timestamp, fetched_at, content_hash, json)
                values(?, ?, ?, ?, ?, ?, ?)
                on conflict(endpoint, document_id) do update set
                  day=excluded.day,
                  timestamp=excluded.timestamp,
                  fetched_at=excluded.fetched_at,
                  content_hash=excluded.content_hash,
                  json=excluded.json
                """,
                (endpoint, doc_id, day, timestamp, fetched_at, h, raw),
            )
        return inserted, updated

    def sync(
        self, client: OuraClient, days: int, *, include_timeseries: bool = True
    ) -> dict[str, Any]:
        started = dt.datetime.now(dt.timezone.utc).isoformat()
        endpoints = list(DATE_ENDPOINTS.keys()) + (
            ["ring_battery_level"] if include_timeseries else []
        )
        cur = self.conn.cursor()
        cur.execute(
            "insert into sync_runs(started_at, days, endpoints) values(?, ?, ?)",
            (started, days, json.dumps(endpoints)),
        )
        run_id = int(cur.lastrowid)
        inserted = updated = 0
        errors: list[str] = []
        start_date, end_date = date_range(days)
        try:
            for endpoint, path in DATE_ENDPOINTS.items():
                docs = client.list_documents(
                    path, {"start_date": start_date, "end_date": end_date}
                )
                i, u = self.upsert_documents(endpoint, docs, started)
                inserted += i
                updated += u
            if include_timeseries:
                docs = client.list_documents(
                    TIMESERIES_ENDPOINTS["ring_battery_level"], {"latest": "true"}
                )
                i, u = self.upsert_documents("ring_battery_level", docs, started)
                inserted += i
                updated += u
        except Exception as exc:
            errors.append(str(exc))
            raise
        finally:
            finished = dt.datetime.now(dt.timezone.utc).isoformat()
            cur.execute(
                "update sync_runs set finished_at=?, inserted=?, updated=?, errors=? where id=?",
                (
                    finished,
                    inserted,
                    updated,
                    json.dumps(errors) if errors else None,
                    run_id,
                ),
            )
            self.conn.commit()
        return {
            "run_id": run_id,
            "inserted": inserted,
            "updated": updated,
            "errors": errors,
            "db": self.path,
        }

    def load_bundle(self, days: int) -> dict[str, Any]:
        start_date, end_date = date_range(days)
        bundle: dict[str, Any] = {
            "range": {"start_date": start_date, "end_date": end_date},
            "source": "sqlite",
            "db": self.path,
        }
        endpoints = sorted(set([*DATE_ENDPOINTS.keys(), *TIMESERIES_ENDPOINTS.keys()]))
        for endpoint in endpoints:
            if endpoint == "heartrate":
                bundle[endpoint] = []
                continue
            if endpoint == "ring_battery_level":
                rows = self.conn.execute(
                    "select json from documents where endpoint=? order by coalesce(timestamp, fetched_at)",
                    (endpoint,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "select json from documents where endpoint=? and day>=? and day<=? order by day, timestamp",
                    (endpoint, start_date, end_date),
                ).fetchall()
            bundle[endpoint] = [json.loads(row["json"]) for row in rows]
        return bundle

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "select endpoint, count(*) c from documents group by endpoint order by endpoint"
        ).fetchall()
        return {row["endpoint"]: int(row["c"]) for row in rows}


def numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except Exception:
        return None


def nums(values: list[Any]) -> list[float]:
    out = []
    for value in values:
        n = numeric(value)
        if n is not None:
            out.append(n)
    return out


def mean(values: list[Any]) -> float | None:
    xs = nums(values)
    return sum(xs) / len(xs) if xs else None


def median(values: list[Any]) -> float | None:
    xs = nums(values)
    return statistics.median(xs) if xs else None


def percentile_rank(values: list[Any], value: Any) -> int | None:
    xs = sorted(nums(values))
    v = numeric(value)
    if not xs or v is None:
        return None
    return round(100 * sum(1 for x in xs if x <= v) / len(xs))


def latest_by_day(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    dated = [r for r in rows if isinstance(r.get("day"), str)]
    return sorted(dated, key=lambda r: r["day"])[-1] if dated else None


def rows_last_n(rows: list[dict[str, Any]], n: int = 14) -> list[dict[str, Any]]:
    dated = [r for r in rows if isinstance(r.get("day"), str)]
    return sorted(dated, key=lambda r: r["day"])[-n:]


def days_old(day: str | None, *, tz_name: str = DEFAULT_TZ) -> int | None:
    if not day:
        return None
    try:
        return (today(tz_name) - dt.date.fromisoformat(day[:10])).days
    except Exception:
        return None


def fmt_num(value: Any, digits: int = 0) -> str:
    n = numeric(value)
    if n is None:
        return "n/a"
    if digits == 0:
        return f"{round(n):,}"
    return f"{n:.{digits}f}"


def fmt_delta(value: Any) -> str:
    n = numeric(value)
    if n is None:
        return ""
    sign = "+" if n > 0 else ""
    return f"{sign}{n:.0f}"


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fmt_percentile(rank: int | None) -> str:
    if rank is None:
        return "n/a"
    label = ordinal(rank)
    if rank >= 90:
        return f"top-ish ({label} percentile)"
    if rank <= 10:
        return f"bottom-ish ({label} percentile)"
    return f"{label} percentile"


def is_main_sleep(row: dict[str, Any]) -> bool:
    duration = numeric(row.get("total_sleep_duration")) or 0
    if duration <= 0:
        return False
    typ = str(row.get("type") or "").lower()
    return typ in ("long_sleep", "long", "") or duration >= 3 * 3600


def main_sleep_rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        r for r in bundle.get("sleep", []) if isinstance(r, dict) and is_main_sleep(r)
    ]
    return sorted(
        rows,
        key=lambda r: (
            str(r.get("day") or ""),
            numeric(r.get("total_sleep_duration")) or 0,
        ),
    )


def latest_battery(bundle: dict[str, Any]) -> dict[str, Any] | None:
    rows = [r for r in bundle.get("ring_battery_level", []) if isinstance(r, dict)]
    if not rows:
        return None
    return sorted(rows, key=lambda r: numeric(r.get("timestamp_unix")) or 0)[-1]


def confidence_label(bundle: dict[str, Any]) -> tuple[str, list[str]]:
    reasons = []
    latest_sleep = latest_by_day(bundle.get("daily_sleep", []) or [])
    latest_ready = latest_by_day(bundle.get("daily_readiness", []) or [])
    latest_activity = latest_by_day(bundle.get("daily_activity", []) or [])
    latest_stress = latest_by_day(bundle.get("daily_stress", []) or [])
    for label, row, warn_after in (
        ("sleep", latest_sleep, 2),
        ("readiness", latest_ready, 2),
        ("activity", latest_activity, 2),
        ("stress", latest_stress, 2),
    ):
        age = days_old(row.get("day") if row else None)
        if age is None:
            reasons.append(f"no {label} data")
        elif age > warn_after:
            reasons.append(f"{label} stale by {age}d")
    if not reasons:
        return "high", ["daily docs are fresh"]
    if len(reasons) <= 2:
        return "medium", reasons
    return "low", reasons


def build_adaptive_analysis(bundle: dict[str, Any], *, days: int) -> str:
    generated = today().strftime("%a %b %-d")
    lines = [f"oura adaptive analysis — {generated}"]

    confidence, reasons = confidence_label(bundle)
    battery = latest_battery(bundle)
    battery_text = "battery n/a"
    if battery and battery.get("level") is not None:
        charging = " charging" if battery.get("charging") else ""
        battery_text = f"battery {battery.get('level')}%{charging}"
    lines.append(
        f"- confidence: {confidence} — {compact_join(reasons[:3], '; ')}; {battery_text}"
    )

    daily_sleep = bundle.get("daily_sleep") or []
    daily_readiness = bundle.get("daily_readiness") or []
    daily_activity = bundle.get("daily_activity") or []
    daily_stress = bundle.get("daily_stress") or []
    daily_resilience = bundle.get("daily_resilience") or []
    daily_spo2 = bundle.get("daily_spo2") or []
    sleeps = main_sleep_rows(bundle)

    # Recovery/readiness.
    recent_ready = rows_last_n(daily_readiness, 14)
    latest_ready = latest_by_day(daily_readiness)
    if recent_ready and latest_ready:
        vals = [r.get("score") for r in recent_ready]
        med = median(vals)
        pct = percentile_rank(vals, latest_ready.get("score"))
        age = days_old(latest_ready.get("day"))
        temp = fmt_temp(latest_ready.get("temperature_deviation"))
        watch = contributor_watch(
            latest_ready.get("contributors"), threshold=70, limit=2
        )
        stale = f"; {age}d stale" if age and age > 1 else ""
        bits = [
            f"readiness latest {fmt_num(latest_ready.get('score'))} on {latest_ready.get('day')} vs 14d median {fmt_num(med)} ({fmt_percentile(pct)}){stale}"
        ]
        if temp:
            bits.append(f"temp {temp}")
        if watch:
            bits.append("watch " + "; ".join(watch))
        lines.append("- recovery: " + " — ".join(bits))

    # Sleep.
    recent_sleeps = sleeps[-14:]
    latest_sleep_score = latest_by_day(daily_sleep)
    if recent_sleeps:
        avg_sleep = mean([r.get("total_sleep_duration") for r in recent_sleeps])
        avg_bed = mean([r.get("time_in_bed") for r in recent_sleeps])
        avg_eff = mean([r.get("efficiency") for r in recent_sleeps])
        avg_hrv = mean([r.get("average_hrv") for r in recent_sleeps])
        avg_rhr = mean([r.get("lowest_heart_rate") for r in recent_sleeps])
        sleep_bits = [
            f"14-night avg {fmt_seconds(avg_sleep)} asleep / {fmt_seconds(avg_bed)} in bed",
            f"eff {fmt_num(avg_eff)}%",
            f"HRV {fmt_num(avg_hrv)}ms",
            f"low HR {fmt_num(avg_rhr)}",
        ]
        if latest_sleep_score:
            vals = [r.get("score") for r in rows_last_n(daily_sleep, 14)]
            pct = percentile_rank(vals, latest_sleep_score.get("score"))
            age = days_old(latest_sleep_score.get("day"))
            sleep_bits.insert(
                0,
                f"score latest {fmt_num(latest_sleep_score.get('score'))} on {latest_sleep_score.get('day')} ({fmt_percentile(pct)})"
                + (f"; {age}d stale" if age and age > 1 else ""),
            )
        if (numeric(avg_sleep) or 0) < 6 * 3600:
            sleep_bits.append("signal: chronic short sleep vs normal 7–9h target")
        lines.append("- sleep: " + " — ".join(sleep_bits))

    # Activity/training load.
    recent_activity = rows_last_n(daily_activity, 14)
    latest_activity = latest_by_day(daily_activity)
    if recent_activity and latest_activity:
        vals = [r.get("score") for r in recent_activity]
        steps = mean([r.get("steps") for r in recent_activity])
        active_cal = mean([r.get("active_calories") for r in recent_activity])
        zero_step_days = sum(
            1 for r in recent_activity if (numeric(r.get("steps")) or 0) == 0
        )
        pct = percentile_rank(vals, latest_activity.get("score"))
        lines.append(
            "- activity: "
            + f"latest {fmt_num(latest_activity.get('score'))} on {latest_activity.get('day')} ({fmt_percentile(pct)}); "
            + f"14d avg {fmt_num(steps)} steps / {fmt_num(active_cal)} active cal; "
            + f"zero-step days {zero_step_days}/{len(recent_activity)}"
        )

    # Stress/resilience.
    recent_stress = rows_last_n(daily_stress, 14)
    if recent_stress:
        summaries: dict[str, int] = {}
        for row in recent_stress:
            key = str(row.get("day_summary") or "unclassified")
            summaries[key] = summaries.get(key, 0) + 1
        stress_high = mean([r.get("stress_high") for r in recent_stress])
        recovery_high = mean([r.get("recovery_high") for r in recent_stress])
        ratio = None
        if stress_high is not None and recovery_high is not None and recovery_high > 0:
            ratio = stress_high / recovery_high
        summary_text = ", ".join(f"{k}:{v}" for k, v in sorted(summaries.items()))
        ratio_text = (
            f"; stress/recovery ratio {ratio:.1f}:1" if ratio is not None else ""
        )
        lines.append(
            f"- stress: {summary_text}; avg high stress {fmt_seconds(stress_high)} / high recovery {fmt_seconds(recovery_high)}{ratio_text}"
        )

    recent_res = rows_last_n(daily_resilience, 14)
    if recent_res:
        levels: dict[str, int] = {}
        for row in recent_res:
            key = str(row.get("level") or "unknown")
            levels[key] = levels.get(key, 0) + 1
        lines.append(
            "- resilience: " + ", ".join(f"{k}:{v}" for k, v in sorted(levels.items()))
        )

    recent_spo2 = rows_last_n(daily_spo2, 14)
    if recent_spo2:
        sp_vals = []
        bdi_vals = []
        for row in recent_spo2:
            sp = row.get("spo2_percentage")
            if isinstance(sp, dict):
                sp_vals.append(sp.get("average"))
            bdi_vals.append(row.get("breathing_disturbance_index"))
        lines.append(
            f"- breathing: SpO₂ avg {fmt_num(mean(sp_vals), 1)}%; BDI avg {fmt_num(mean(bdi_vals), 1)}"
        )

    # Actionable next step based on current data quality.
    if confidence != "high":
        lines.append(
            "- next move: wear it tonight after the recharge; I’ll treat tomorrow’s sleep/readiness as the first clean post-charge checkpoint."
        )
    elif (
        recent_ready
        and latest_ready
        and numeric(latest_ready.get("score")) is not None
        and (numeric(latest_ready.get("score")) or 0)
        < (median([r.get("score") for r in recent_ready]) or 0) - 10
    ):
        lines.append(
            "- next move: recovery-biased day; keep the report focused on sleep debt, stress load, and easy activity."
        )
    else:
        lines.append(
            "- next move: keep collecting; strongest current target is stress/recovery balance plus sleep duration consistency."
        )

    lines.append("_local baseline analysis; not medical advice._")
    return "\n".join(lines)


def cmd_sync(args: argparse.Namespace) -> int:
    try:
        token = get_token(required=not args.quiet_if_missing_token)
    except MissingToken as exc:
        if args.quiet_if_missing_token:
            return 0
        eprint(str(exc))
        return 2
    if not token:
        return 0
    store = OuraStore(args.db)
    try:
        result = store.sync(
            OuraClient(token=token, timeout=args.timeout, max_pages=args.max_pages),
            args.days,
            include_timeseries=not args.no_timeseries,
        )
        if not args.quiet:
            counts = store.counts()
            print(
                json.dumps({"sync": result, "counts": counts}, indent=2, sort_keys=True)
            )
    finally:
        store.close()
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    token = None
    if args.sync:
        try:
            token = get_token(required=not args.quiet_if_missing_token)
        except MissingToken as exc:
            if args.quiet_if_missing_token:
                return 0
            eprint(str(exc))
            return 2
        if not token:
            return 0
    store = OuraStore(args.db)
    try:
        sync_result = None
        if args.sync:
            sync_result = store.sync(
                OuraClient(token=token, timeout=args.timeout, max_pages=args.max_pages),
                args.days,
                include_timeseries=not args.no_timeseries,
            )
        bundle = store.load_bundle(args.days)
        if args.json:
            print(
                json.dumps(
                    {
                        "sync": sync_result,
                        "analysis": build_adaptive_analysis(bundle, days=args.days),
                        "counts": store.counts(),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(build_adaptive_analysis(bundle, days=args.days))
    finally:
        store.close()
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    try:
        token = get_token(required=not args.quiet_if_missing_token)
    except MissingToken as exc:
        if args.quiet_if_missing_token:
            return 0
        eprint(str(exc))
        return 2
    if not token:
        return 0
    client = OuraClient(token=token, timeout=args.timeout, max_pages=args.max_pages)
    bundle = fetch_bundle(client, args.days, include_timeseries=not args.no_timeseries)
    if args.json:
        print(json.dumps(bundle, indent=2, sort_keys=True))
    else:
        print(build_digest(bundle, days=args.days))
    return 0


def cmd_raw(args: argparse.Namespace) -> int:
    token = get_token(required=True)
    if not token:
        eprint("missing Oura API token")
        return 2
    client = OuraClient(token=token, timeout=args.timeout, max_pages=args.max_pages)
    start, end = args.start_date, args.end_date
    if not start or not end:
        start, end = date_range(args.days)
    path = DATE_ENDPOINTS.get(args.endpoint) or TIMESERIES_ENDPOINTS.get(args.endpoint)
    if not path:
        valid = sorted([*DATE_ENDPOINTS.keys(), *TIMESERIES_ENDPOINTS.keys()])
        eprint("unknown endpoint. valid:", ", ".join(valid))
        return 2
    if args.endpoint in TIMESERIES_ENDPOINTS:
        params = {
            "start_datetime": args.start_datetime,
            "end_datetime": args.end_datetime,
            "latest": "true" if args.latest else None,
        }
    else:
        params = {"start_date": start, "end_date": end}
    data = client.list_documents(path, params)
    print(
        json.dumps({"endpoint": args.endpoint, "data": data}, indent=2, sort_keys=True)
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw helper for Oura API v2")
    sub = parser.add_subparsers(dest="cmd", required=True)

    setup = sub.add_parser(
        "setup-token", help="prompt for an Oura token and store it in macOS Keychain"
    )
    setup.add_argument("--service", default=KEYCHAIN_SERVICE)
    setup.add_argument("--account", default=KEYCHAIN_ACCOUNT)
    setup.set_defaults(func=setup_token)

    setup_file = sub.add_parser(
        "setup-token-file",
        help="prompt for an Oura token and store it in a local chmod 600 file",
    )
    setup_file.add_argument("--path", default=DEFAULT_TOKEN_FILE)
    setup_file.set_defaults(func=setup_token_file)

    status = sub.add_parser(
        "token-status", help="check whether a token source is configured"
    )
    status.add_argument("--service", default=KEYCHAIN_SERVICE)
    status.add_argument("--account", default=KEYCHAIN_ACCOUNT)
    status.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    status.set_defaults(func=token_status)

    digest = sub.add_parser("digest", help="print a concise daily health digest")
    digest.add_argument(
        "--days", type=positive_int, default=7, help="lookback window for daily docs"
    )
    digest.add_argument("--timeout", type=positive_int, default=30)
    digest.add_argument("--max-pages", type=positive_int, default=20)
    digest.add_argument(
        "--json",
        action="store_true",
        help="print raw fetched bundle JSON instead of digest text",
    )
    digest.add_argument(
        "--no-timeseries",
        action="store_true",
        help="skip latest battery/time-series calls",
    )
    digest.add_argument(
        "--quiet-if-missing-token",
        action="store_true",
        help="exit 0 with no output if no token is configured",
    )
    digest.set_defaults(func=cmd_digest)

    sync = sub.add_parser(
        "sync", help="sync Oura API data into the local gitignored SQLite database"
    )
    sync.add_argument(
        "--days", type=positive_int, default=90, help="lookback window to sync"
    )
    sync.add_argument("--db", default=DEFAULT_DB_PATH)
    sync.add_argument("--timeout", type=positive_int, default=45)
    sync.add_argument("--max-pages", type=positive_int, default=50)
    sync.add_argument(
        "--no-timeseries",
        action="store_true",
        help="skip latest battery/time-series calls",
    )
    sync.add_argument("--quiet", action="store_true", help="do not print sync summary")
    sync.add_argument(
        "--quiet-if-missing-token",
        action="store_true",
        help="exit 0 with no output if no token is configured",
    )
    sync.set_defaults(func=cmd_sync)

    analyze = sub.add_parser(
        "analyze", help="sync and print adaptive local-baseline analysis"
    )
    analyze.add_argument(
        "--days", type=positive_int, default=45, help="lookback window for analysis"
    )
    analyze.add_argument("--db", default=DEFAULT_DB_PATH)
    analyze.add_argument("--timeout", type=positive_int, default=45)
    analyze.add_argument("--max-pages", type=positive_int, default=50)
    analyze.add_argument(
        "--no-timeseries",
        action="store_true",
        help="skip latest battery/time-series calls during sync",
    )
    analyze.add_argument(
        "--no-sync",
        dest="sync",
        action="store_false",
        help="analyze existing local database without fetching",
    )
    analyze.add_argument(
        "--sync",
        dest="sync",
        action="store_true",
        default=True,
        help="fetch before analyzing (default)",
    )
    analyze.add_argument(
        "--json", action="store_true", help="print JSON wrapper with report/counts"
    )
    analyze.add_argument(
        "--quiet-if-missing-token",
        action="store_true",
        help="exit 0 with no output if no token is configured",
    )
    analyze.set_defaults(func=cmd_analyze)

    raw = sub.add_parser("raw", help="fetch one endpoint as JSON")
    raw.add_argument(
        "endpoint",
        help="endpoint nickname, e.g. daily_sleep, sleep, ring_battery_level",
    )
    raw.add_argument("--days", type=positive_int, default=7)
    raw.add_argument("--start-date")
    raw.add_argument("--end-date")
    raw.add_argument("--start-datetime")
    raw.add_argument("--end-datetime")
    raw.add_argument("--latest", action="store_true")
    raw.add_argument("--timeout", type=positive_int, default=30)
    raw.add_argument("--max-pages", type=positive_int, default=20)
    raw.set_defaults(func=cmd_raw)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except MissingToken as exc:
        eprint(str(exc))
        return 2
    except OuraError as exc:
        eprint(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
