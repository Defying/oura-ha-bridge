# oura-openclaw

Local CLI helper for Oura API v2 health digests and adaptive personal-baseline analysis.

It syncs Oura data into a local SQLite database, learns rolling personal baselines, and renders concise reports for automation/chat systems.

## Privacy defaults

- Oura API tokens are read from `OURA_TOKEN` or macOS Keychain.
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

## Local data

- Default DB: `data/oura.sqlite3`
- Raw API docs are stored there so derived metrics can be recomputed as the analyzer improves.
- `data/` is gitignored; do not commit raw Oura data.

## Intelligence plan

See [`docs/OURA_INTELLIGENCE_PLAN.md`](docs/OURA_INTELLIGENCE_PLAN.md).

## Notes

- Uses official Oura API v2 at `https://api.ouraring.com`.
- Oura data only updates after the ring syncs to the Oura app/cloud.
