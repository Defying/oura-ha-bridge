# oura-openclaw

Local CLI helper for Oura API v2 health digests and adaptive personal-baseline analysis.

It syncs Oura data into a local SQLite database, learns rolling personal baselines, and renders concise reports for automation/chat systems.

Also included: a Home Assistant custom integration under `custom_components/oura_openclaw`.

## Privacy defaults

- Oura API tokens are read from `OURA_TOKEN`, local `data/oura.token`, or macOS Keychain.
- Raw Oura data is stored locally under gitignored `data/`.
- The public repo contains code, docs, and synthetic tests only — no raw health data.
- Analysis is pattern spotting, not medical advice.

## Install

```bash
git clone https://github.com/Defying/oura-openclaw.git
cd oura-openclaw
./bin/oura-health --help
```

Optional convenience symlink:

```bash
ln -sf "$PWD/bin/oura-health" ~/.local/bin/oura-health
```

## Token setup

Create a personal access token at <https://cloud.ouraring.com/personal-access-tokens>.

Local token file storage, best for scheduled jobs that should not wait on Keychain unlock:

```bash
oura-health setup-token-file
```

This writes `data/oura.token` with `0600` permissions. `data/` is gitignored.

macOS Keychain storage:

```bash
oura-health setup-token
```

Or for one-off use:

```bash
export OURA_TOKEN='...'
```

Check status:

```bash
oura-health token-status
```

## Usage

Adaptive local analysis, with sync first:

```bash
oura-health analyze
```

Sync Oura API documents into local SQLite:

```bash
oura-health sync --days 90
```

Analyze existing local SQLite without fetching:

```bash
oura-health analyze --no-sync
```

Simple daily digest:

```bash
oura-health digest
```

Quiet mode for scheduled jobs before the token exists:

```bash
oura-health analyze --quiet-if-missing-token
```

Fetch one endpoint for debugging:

```bash
oura-health raw daily_sleep --days 14
oura-health raw sleep --days 14
oura-health raw ring_battery_level --latest
```

## Home Assistant

Copy or symlink `custom_components/oura_openclaw` into your Home Assistant `/config/custom_components/` directory, restart Home Assistant, then add **Oura OpenClaw** from **Settings → Devices & services → Add integration**.

The integration supports either:

- direct Oura API token entry in the config flow, or
- a token file path readable by Home Assistant.

For a token file on Home Assistant OS, put the token in a file such as `/config/oura.token` with `0600` permissions, then enter `oura.token` in the setup form. Relative paths are resolved from the Home Assistant config directory.

Sensors include readiness, sleep, activity, stress, resilience, SpO2, battery, sleep-stage durations, HRV, heart rate, bedtime timestamps, and a compact summary sensor. The `oura_openclaw.refresh` service forces an immediate refresh.

## HACS

This repository is structured for HACS as a custom integration:

- one integration under `custom_components/oura_openclaw`
- root `hacs.json`
- integration `manifest.json` with `domain`, `documentation`, `issue_tracker`, `codeowners`, `name`, and `version`
- HACS and Hassfest GitHub Actions

To install before default HACS listing, add this repository as a HACS custom repository with category **Integration**.

## Local data

- Default DB: `data/oura.sqlite3`
- Raw API docs are stored there so derived metrics can be recomputed as the analyzer improves.
- `data/` is gitignored; do not commit raw Oura data.

## Intelligence plan

See [`docs/OURA_INTELLIGENCE_PLAN.md`](docs/OURA_INTELLIGENCE_PLAN.md).

## Notes

- Uses official Oura API v2 at `https://api.ouraring.com`.
- Oura data only updates after the ring syncs to the Oura app/cloud.
