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

## Stage 2b-ii: pts_52w Cap — LIVE (P0 bare-signal, promoted 2026-07-18)

Weak-era diagnosis + multi-era bare re-run `stage2_pts52w_cap37`:

| Metric | Result |
|---|---:|
| PF mean | **1.214** |
| Worst-era PF | **1.105** (`bear_rates`) |
| Trades | 15,994 |

Clears bare-signal floors (PF mean ≥ 1.20, worst ≥ 1.00). Cap is now part of the
promoted Stage A / enforced stack. **Keep `PROB_RANK_MODE=shadow`.**

```env
PTS_52W_CAP_MODE=live
PTS_52W_CAP_MAX=37
```

Apply with stack:

```bash
python scripts/apply_signal_stack_enforced_env.py
python scripts/validate_signal_stack_enforced_env.py
```

## Stage 2c: Enforced Stack (live entry + live exit grace)

Offline stack `exit_grace_breakout_buffer_0.010` clears PF promotion gates
(PF mean ≥ 1.20, worst-era ≥ 1.00). Exit management completed its shadow run
and was explicitly operator-promoted; keep collecting live-enforced evidence
for the Phase 2 re-audit.

Set (or run `python scripts/apply_signal_stack_enforced_env.py`):

```env
ENTRY_TIMING_SHADOW_MODE=live
ENTRY_SHADOW_DISABLE_SMA50_FILTERS=true
ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT=0.01
EXIT_MANAGER_MODE=live
EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL=15
EXIT_MAX_HOLD_DAYS=40
HOLD_DAYS=40
BACKTEST_HOLD_DAYS=40
BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL=15
BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS=true
COUNTERFACTUAL_LOGGING_ENABLED=true
PTS_52W_CAP_MODE=live
PTS_52W_CAP_MAX=37
# PROB_RANK_MODE=shadow  # keep shadow; do not promote live here
```

Preflight:

```bash
python scripts/validate_signal_stack_enforced_env.py
python scripts/validate_entry_timing_live_active.py
```

Or start the dashboard with the stack applied:

```bash
python scripts/start_local_dashboard.py --signal-stack-enforced
```

Go/No-Go:

- Go if live Stage A filter rate stays ~40–60%, profile is
  `breakout_buffer_only_0.010`, and exit-manager diagnostics show the expected
  15/40-day live grace behavior.
- Re-run phase2 / signal-stack counterfactual on live-enforced trades before
  considering any additional plugin promotion.
- Do **not** re-enable hard breakout-volume or confluence Stage A gates.

## Stage 2d: Rank-v2 p75 Trim (shadow first)

The rank-v2 filter failed on unfiltered trades but improved the promoted
stack counterfactual. The 2026-07-13 percentile sweep (p60-p80, artifacts
`validation_artifacts/sweep_cf_rank*.json`) found a stable plateau at p73-p76;
p75 is the plateau max: PF mean 1.2118 -> 1.2491 and worst-era PF 1.0368 ->
1.1203 at 25.1% retention (p70 gave 1.2312 / 1.1431 at 30%; p80 fails gates).
Keep it isolated from the failed composite/signal rank filters.

```env
RANK_FILTER_V2_MODE=live
RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2=75
SCAN_LIVE_SORT_KEY=signal_score
```

Promoted 2026-07-16 (ledger seq 15) after p75 shadow evidence (session2
retention 25.9% dq=ok; session3 retention 28.0% dq=stale, operator-accepted)
and offline stack PF mean 1.2491 / worst-era 1.1203. Keep sort key on
`signal_score` until a separate justification.

Live diagnostics:

- `rank_filter_v2_evaluated`
- `rank_filter_v2_threshold`
- `rank_filter_v2_would_drop` / dropped counts
- per-signal `rank_filter_v2`

Go/No-Go (post-promote monitoring):

- Retention should stay ~25–35% on RTH scans with `data_quality=ok`.
- Only then consider `SCAN_LIVE_SORT_KEY=rank_score_v2`.
- Roll back to shadow if retention exits band for ≥2 distinct ok sessions or
  rank IC turns negative on refreshed metrics.

### Live monitoring cohort (post seq 15)

| Label | Day (UTC) | Rank mode | Entry WF% | Rank eval / drop / ret% | Signals | DQ | Provider notes |
|---|---|---|---|---|---|---|---|
| post_rank_live_rth1 | 2026-07-16 | live p75 | 72.3 (pass) | 26 / 19 / **26.9** | 7 | ok | primary 1505, fallback 1 (DNOW) |
| post_rank_live_rth2 | 2026-07-17 | live p75 | 52.7 (pass) | 27 / 20 / **25.9** | 7 | ok | heavy Schwab 401 → yfinance fallback during scan |

Qualifying RTH/`ok` sessions toward Phase 1 gate (need 2): **2 / 2** — Phase 1 live-fidelity gate **PASS** (retention in 25–35% both days; no rollback).

### Phase 3 re-audit (2026-07-17)

Artifact: `validation_artifacts/phase2_edge_audit_post_rank_live.json` (+ synced `phase2_edge_audit.json`).

| Check | Result |
|---|---|
| Bare verdict | `iterate_with_caution` (PF mean **1.162**, worst-era **1.032**) |
| Stack offline | PASS (buffer+exit PF mean 1.2118 / worst 1.0368; p75 1.2491 / 1.1203) |
| Readiness | `stack_and_bare_aligned` — plugin **shadow** ok after live week; **LIVE** plugins still blocked until bare PF mean ≥ 1.20 |

### Phase 4 hold (post-plan)

- Keep stack as-is: live entry 1% / exit 15/40 / rank-v2 p75 / QG shadow / `SCAN_LIVE_SORT_KEY=signal_score`
- Do **not** promote `REGIME_V2` or `CORRELATION_GUARD` to LIVE without fresh bare/stack evidence (pts_52w cap cleared bare 1.20; collect live week)
- Do **not** change sort key or enable Stage 3 QG hard this cycle
- Re-auth Schwab before next full-universe scan (rth2 saw primary 471 / fallback 528 on HTTP 401)
- Do **not** add new hard filters to chase PF. Prob-rank research path (Phases B–D) is implemented with `PROB_RANK_MODE=off` by default. To shadow-score without changing fills: train a model, then set `PROB_RANK_MODE=shadow` (+ optional `PROB_RANK_MODEL_DIR`). Rank-v2 p75 remains the live control until a separate promotion.

## Stage 2e: PF 1.50 dual-track (research, 2026-07-18)

Strict target remains five-era PF mean ≥ 1.50 / worst-era ≥ 1.00 (see `docs/BACKTEST_CATALOG.md` §11).

| Track | Status | Action |
|---|---|---|
| A early-stop gate | Offline CF: `pts_52w_cap_35` → PF mean 1.314 / worst 1.065 | Keep `EARLY_STOP_GATE_MODE=shadow` (default). Do **not** set live. |
| B pullback peer | Smoke 40-ticker PF mean **1.492** / worst **1.062**; full-universe `pullback_only_aug_full` in progress (recent_current PF 0.959 on 4086 trades — smoke≠full) | Finish full five-era + stack transfer |
| B PEAD-primary peer | Earnings history fixed: Finnhub merge + yfinance `limit=100` backfill; cache beats in all 5 eras (e.g. late_bull 311 on 40 names) | Re-run smoke/full after pullback finishes (avoid dual Schwab runs) |

Diagnostics: `early_stop_gate_mode`, `early_stop_gate_would_filter`, `early_stop_gate_blocked`.

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
