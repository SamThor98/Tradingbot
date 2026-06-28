# Signal Quality Rollout Runbook

## Goal

Raise signal precision safely with phased rollout and clear go/no-go checkpoints.

## Stage 1: Metrics-Only (1 week)

Set:

- `QUALITY_GATES_ENABLED=false`
- `QUALITY_WATCHLIST_PREFILTER_ENABLED=false`

Actions:

- Run normal scanner flow.
- Confirm weekly digest includes `Signal Quality (7d)` field.
- Capture baseline:
  - scans per week
  - signals per week
  - avg signal score
  - avg conviction
  - weak breakout volume count
  - weak MiroFish alignment count

Go/No-Go:

- Go if quality metrics are stable and no scanner errors/regressions are observed.

## Stage 2: Shadow Gate Logging (1 week)

Set:

- `QUALITY_GATES_ENABLED=false`
- Configure thresholds:
  - `QUALITY_MIN_SIGNAL_SCORE`
  - `QUALITY_MIN_CONTINUATION_PROB`
  - `QUALITY_MAX_BULL_TRAP_PROB`
  - optional `QUALITY_REQUIRE_BREAKOUT_VOLUME=true`

Actions:

- Monitor `quality_gates_would_filter` and per-reason counters in diagnostics.
- Do not block live alerts yet.

Go/No-Go:

- Go if would-filter behavior is sensible (not overly aggressive, not zero when expected).

## Stage 2b: Entry-Timing Shadow Experiment (1 week, P0)

Offline replay on `control_legacy_aug` recommends **breakout buffer only** at 1.0%
(~50% trade retention, overlap PF +0.32, early stops −3.9pp). Do **not** enforce live.

Set:

```env
ENTRY_TIMING_SHADOW_MODE=shadow
ENTRY_SHADOW_DISABLE_SMA50_FILTERS=true
ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT=0.01
```

Preflight:

```bash
python scripts/apply_entry_timing_experiment_env.py
python scripts/validate_entry_timing_experiment_env.py
```

Or start the local dashboard with experiment vars applied:

```bash
python scripts/start_local_dashboard.py --entry-timing-experiment
```

Headless refresh when ``last_scan`` is stale (requires Schwab market data auth):

```bash
python scripts/run_entry_timing_experiment_scan.py --smoke
python scripts/compare_live_entry_shadow_to_offline.py --write-artifact
```

After each scan (local dashboard auto-writes artifact on completion):

```bash
python scripts/compare_live_entry_shadow_to_offline.py --write-artifact
```

Go/No-Go:

- Go if live would-filter rate on Stage A is **~40–60%** and profile is
  `breakout_buffer_only_0.010` for 1–2 scans; compare verdict `pass`.
- No-Go if rate is near 0% (experiment env not loaded) or outside band with env ready.
- Do **not** enable rank filter or SMA50 extension cap (12% cap hurt overlap PF offline).

## Stage 3: Narrow Enforcement (1 week)

Set:

- `QUALITY_GATES_ENABLED=true`
- Restrict universe:
  - `SIGNAL_WATCHLIST` to a small canary list, or
  - `QUALITY_WATCHLIST_PREFILTER_ENABLED=true` with low max

Actions:

- Track `quality_gates_filtered` and resulting signal count.
- Verify confirm bot / manual execution flow remains unchanged.

Go/No-Go:

- Go if filtered signals show improved quality and no operational regressions.

## Stage 4: Expand

Actions:

- Gradually widen watchlist and/or relax prefilter max.
- Keep quality thresholds fixed while sample size grows.

Guardrails:

- Roll back immediately if signal throughput drops below acceptable floor.
- Roll back if weekly signal quality metrics degrade for two consecutive weeks.

## Pre-Deploy Validation

Run:

`python scripts/validate_signal_quality.py`

Expected:

- Prints `PASS: signal quality validation checks succeeded`
