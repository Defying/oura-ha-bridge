# Oura Intelligence Plan

Goal: make Oura data useful without requiring someone to open the app.

This project behaves like a quiet local health analyst: sync Oura data, learn personal baselines, flag what is genuinely interesting, and stay silent when confidence is low or nothing changed.

## Guardrails

- Raw Oura API documents stay local under `data/` and are gitignored.
- GitHub gets code, docs, tests, and synthetic fixtures only — never tokens or raw health rows.
- Reports are pattern spotting, not medical advice.
- Prefer confidence labels over false certainty. If sleep/readiness are stale, say that before interpreting them.
- Avoid nagging. Daily reports should be short; event alerts need thresholds, cooldowns, and obvious value.

## Local learning model

“Training” means local personal-baseline modeling, not cloud fine-tuning:

1. Sync official Oura API v2 docs into local SQLite.
2. Normalize daily docs by endpoint/day and preserve raw JSON locally for reprocessing.
3. Compute rolling baselines over 7/14/30/60 day windows.
4. Score freshness, missingness, and confidence.
5. Compare latest values against the user's own distributions: medians, percentiles, deltas, EWMA shifts.
6. Generate adaptive observations:
   - unusually good/bad sleep relative to personal baseline
   - recovery/readiness drift
   - stress/recovery imbalance
   - low battery / stale sync / ring not worn
   - activity load vs recovery readiness
7. Later: correlate derived features with optional context like calendar density, weather, workouts, caffeine/alcohol/manual tags, or home/environment events.

## Reporting cadence

### Daily morning analysis

Run after the ring likely synced.

Include:
- data confidence/freshness
- battery state
- sleep/readiness if fresh
- one or two standout trends
- one next move

Stay silent or short if data is stale.

### Weekly review

Summarize patterns, not individual days:
- best/worst sleep and readiness days
- average sleep duration/efficiency/HRV/RHR
- stress vs recovery ratio
- activity consistency
- one experiment for next week

### Event alerts

Only interrupt for:
- battery critically low before night
- sleep/readiness data stale for several days
- unusually high stress load plus low recovery
- temperature/RHR/HRV anomaly with confidence caveat
- genuinely positive outliers worth reinforcing

## First implementation slice

Implemented:

- `oura-health sync`: syncs Oura API docs into local SQLite at `data/oura.sqlite3`.
- `oura-health analyze`: syncs then prints adaptive baseline analysis.
- Includes data freshness/confidence, battery, readiness, sleep, activity, stress, resilience, SpO₂/BDI, and next-move text.

## Next slices

1. Add more unit tests with synthetic fixtures and golden report snapshots.
2. Split the monolithic script into modules: API, store, metrics, reports, CLI.
3. Add weekly report command and weekly cron examples.
4. Add learned alert cooldown state so event alerts do not nag.
5. Add optional local context tags and correlation reports.
6. Add compact static HTML/Markdown trend report for deeper review.
