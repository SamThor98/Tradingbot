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

## Stage 2d: Rank-v2 Trim (live; p76 post-cap retune)

Pre-cap (2026-07-13) sweep found plateau p73–p76; **p75** was promoted live
(ledger seq 15) on the uncapped stack. After live `PTS_52W_CAP_MODE=live`
(max 37), the same p75 arm **fails** worst-era under the capped book
(`late_bull` PF **0.955**). Sweep
`sweep_cf_rank_under_pts52w_cap37_control_legacy_aug.json` picks **p76** as
first clear: PF mean **1.323** / worst **1.034** / retention **24.1%**.

```env
RANK_FILTER_V2_MODE=live
RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2=76
SCAN_LIVE_SORT_KEY=signal_score
```

Retuned 2026-07-22 (ledger target `RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2=76`).
Keep sort key on `signal_score` until a separate justification.

Live diagnostics:

- `rank_filter_v2_evaluated`
- `rank_filter_v2_threshold`
- `rank_filter_v2_would_drop` / dropped counts
- per-signal `rank_filter_v2`

Go/No-Go (post-retune monitoring):

- Retention should stay ~22–35% on RTH scans with `data_quality=ok`.
- Only then consider `SCAN_LIVE_SORT_KEY=rank_score_v2`.
- Roll back to p75 or buffer-only if retention exits band for ≥2 distinct ok
  sessions or rank IC turns negative on refreshed metrics.

### Live monitoring cohort (post seq 15, pre-cap era)

| Label | Day (UTC) | Rank mode | Entry WF% | Rank eval / drop / ret% | Signals | DQ | Provider notes |
|---|---|---|---|---|---|---|---|
| post_rank_live_rth1 | 2026-07-16 | live p75 | 72.3 (pass) | 26 / 19 / **26.9** | 7 | ok | primary 1505, fallback 1 (DNOW) |
| post_rank_live_rth2 | 2026-07-17 | live p75 | 52.7 (pass) | 27 / 20 / **25.9** | 7 | ok | heavy Schwab 401 → yfinance fallback during scan |

Qualifying RTH/`ok` sessions toward Phase 1 gate (need 2): **2 / 2** — Phase 1 live-fidelity gate **PASS** (retention in 25–35% both days; no rollback). Re-collect 1–2 RTH/`ok` sessions after p76 retune.

### Live monitoring cohort (post p76 retune, seq 16)

| Label | Day (UTC) | Rank mode | Rank eval / drop / ret% | Signals | DQ | Notes |
|---|---|---|---|---|---|---|
| post_p76_from_pead_full | 2026-07-22 | live p76 | 25 / 19 / **24.0** | 6 | ok | Same scan as PEAD full shadow; qualifies |
| post_p76_postclose_20260727 | 2026-07-28 | live p76 | 24 / 18 / **25.0** | 6 | stale | Post-close full PEAD dual-admit scan; retention in band but dq=stale → does **not** qualify |

### Phase 3 re-audit (2026-07-17)

Artifact: `validation_artifacts/phase2_edge_audit_post_rank_live.json` (+ synced `phase2_edge_audit.json`).

| Check | Result |
|---|---|
| Bare verdict | `iterate_with_caution` (PF mean **1.162**, worst-era **1.032**) |
| Stack offline | PASS (buffer+exit PF mean 1.2118 / worst 1.0368; p75 1.2491 / 1.1203) |
| Readiness | `stack_and_bare_aligned` — plugin **shadow** ok after live week; **LIVE** plugins still blocked until bare PF mean ≥ 1.20 |

### Phase 4 hold (post-plan)

- Keep stack as-is: live entry 1% / exit 15/40 / rank-v2 **p76** / QG shadow / `SCAN_LIVE_SORT_KEY=signal_score`
- Do **not** promote `REGIME_V2` or `CORRELATION_GUARD` to LIVE without fresh bare/stack evidence (pts_52w cap cleared bare 1.20; collect live week)
- Do **not** change sort key or enable Stage 3 QG hard this cycle
- Re-auth Schwab before next full-universe scan (rth2 saw primary 471 / fallback 528 on HTTP 401)
- Do **not** add new hard filters to chase PF. Prob-rank research path (Phases B–D) is implemented with `PROB_RANK_MODE=off` by default. To shadow-score without changing fills: train a model, then set `PROB_RANK_MODE=shadow` (+ optional `PROB_RANK_MODEL_DIR`). Rank-v2 p76 remains the live control until a separate promotion.

## Stage 2e: PF 1.50 dual-track (research, 2026-07-18)

Strict target remains five-era PF mean ≥ 1.50 / worst-era ≥ 1.00 (see `docs/BACKTEST_CATALOG.md` §11).

| Track | Status | Action |
|---|---|---|
| A early-stop gate | Offline CF: `pts_52w_cap_35` → PF mean 1.314 / worst 1.065 | Keep `EARLY_STOP_GATE_MODE=shadow` (default). Do **not** set live. |
| B pullback peer | Full-universe `pullback_only_aug_full` PF mean 1.211 / worst **0.959** | **Reject** as 1.50 peer |
| B PEAD-primary peer | Full `pead_primary_aug_fixed_full_c40` PF mean **1.550** / worst **1.167** | **Clears 1.50**; keep exit-grace-only (Stage2 1% buffer does not transfer) |
| B PEAD capacity CF | `pead_primary_capacity_cf_*`: **`top5_by_edge_score`** PF **1.511** / worst **1.291** | Shadow rank/sizing candidate; bare book capacity-saturated; **no live** |

Diagnostics: `early_stop_gate_mode`, `early_stop_gate_would_filter`, `early_stop_gate_blocked`.

## Stage 2f: PEAD-primary shadow dual-admit (non-executable)

Wire PEAD-primary beside Stage-2 in one scan without changing what can place orders.

```env
STRATEGY_PEAD_PRIMARY_MODE=shadow
STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=false
PEAD_PRIMARY_SHADOW_MAX_NAMES=50
PEAD_PRIMARY_SHADOW_RANK_TOP_N=5
# PEAD_PRIMARY_LOOKBACK_DAYS=10   # optional; defaults to PEAD_LOOKBACK_DAYS
```

```bash
python scripts/run_pead_primary_shadow_scan.py --max-tickers 0 --label full_sp1500_rth
python scripts/compare_pead_primary_shadow_to_offline.py --write-artifact
```

Go/No-Go:

- Go if `pead_primary_evaluated` > 0, `executable_stage2_only=True` (no PEAD-only in signals), and compare verdict `pass`.
- Do **not** set `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=true` or promote PEAD-only to executable.

Capacity-aware shadow research (2026-07-22): run `python scripts/analyze_pead_primary_capacity_counterfactual.py`. Preferred PEAD-native arm for continued shadow ranking/sizing notes: **`top5_by_edge_score`** + exit `t15/h40`. Runtime shadow lists sort by Stage-A `edge_score` proxy and annotate capacity top-N (`PEAD_PRIMARY_SHADOW_RANK_TOP_N=5`); executable shortlist remains Stage2 `stage_a_score`. Reject Stage2 1% buffer on PEAD.

Dual-admit evidence (post edge top-5 wire): two compare-pass sessions (`…205237Z` dq=ok; `…043035Z` dq=stale). **Go for non-executable canary sleeve design discussion only** — do **not** set `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=true`. Prefer ≥1 more RTH-hours `dq=ok` session before any enablement.

Design draft: [`docs/PEAD_CANARY_SLEEVE_DESIGN.md`](PEAD_CANARY_SLEEVE_DESIGN.md) (paper/diagnostics sleeve = capacity top-5; Stage2-only executable; no `ALLOW_LIVE`).

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
