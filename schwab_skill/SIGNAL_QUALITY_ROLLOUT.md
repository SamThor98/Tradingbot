# Signal Quality Rollout Runbook

## Active cycle (2026-10-09 through 2026-11-07)

Follow `wiki/thirty-day-signal-cut-plan.md`. The live stack stays buffer 1% / exit grace 15/40 / rank-v2 p76 / `pts_52w` ≤ 37 / `SCAN_LIVE_SORT_KEY=signal_score` / `SIGNAL_TOP_N` 5. The only plugin signature in this window is correlation guard, on or after 2026-10-14. Stages below that say `keep_shadow` or `do not promote` stay closed. Do not start a new ranker.

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
python scripts/apply_pead_canary_paper_env.py
python scripts/run_pead_canary_paper_session.py
# or the pieces:
python scripts/run_pead_primary_shadow_scan.py --max-tickers 0 --label full_sp1500_rth_<YYYYMMDD>
python scripts/compare_pead_primary_shadow_to_offline.py --write-artifact
python scripts/score_hypothesis_outcomes.py
```

Go/No-Go:

- Go if `pead_primary_evaluated` > 0, `executable_stage2_only=True` (no PEAD-only in signals), and compare verdict `pass`.
- Paper sleeve stays **capacity top-5** (`top5_by_edge_score`) with Stage2 exit-grace 15/40 only (do not transfer the 1% buffer onto PEAD).
- R7 scores those canary names as `sleeve_id=S1` / `source=pead_canary`. Hold **all executable talk** until sleeve-local T+5 N ≥ 40 and expectancy > 0.
- Do **not** set `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=true` or promote PEAD-only to executable.

Capacity-aware shadow research (2026-07-22): run `python scripts/analyze_pead_primary_capacity_counterfactual.py`. Preferred PEAD-native arm for continued shadow ranking/sizing notes: **`top5_by_edge_score`** + exit `t15/h40`. Runtime shadow lists sort by Stage-A `edge_score` proxy and annotate capacity top-N (`PEAD_PRIMARY_SHADOW_RANK_TOP_N=5`); executable shortlist remains Stage2 `stage_a_score`. Reject Stage2 1% buffer on PEAD.

Dual-admit evidence (post edge top-5 wire): two compare-pass sessions (`…205237Z` dq=ok; `…043035Z` dq=stale). **Go for non-executable canary sleeve design discussion only** — do **not** set `STRATEGY_PEAD_PRIMARY_ALLOW_LIVE=true`. Prefer ≥1 more RTH-hours `dq=ok` session before any enablement.

Design draft: [`docs/PEAD_CANARY_SLEEVE_DESIGN.md`](PEAD_CANARY_SLEEVE_DESIGN.md) (paper/diagnostics sleeve = capacity top-5; Stage2-only executable; no `ALLOW_LIVE`).

## Stage 2g: MiroFish Grok shadow (LLM challenger, not a live rank change)

Point crowd-conviction at xAI Grok while persona weighting stays **shadow**
(`applied=false`). Live rank still uses the existing `pts_mirofish` blend;
Grok vs `gpt-4o-mini` is compared from `.mirofish_model_log.jsonl`, not from
LIVE `MIROFISH_WEIGHTING_MODE`.

```env
MIROFISH_WEIGHTING_MODE=shadow
LLM_BASE_URL=https://api.x.ai/v1
LLM_MODEL_NAME=grok-4.6
# XAI_API_KEY=...   # required; do not send OPENAI_API_KEY to xAI
```

```bash
python scripts/apply_mirofish_grok_shadow_env.py
python scripts/validate_mirofish_grok_shadow_env.py
python scripts/compare_mirofish_models.py --write-artifact
python scripts/analyze_rank_v2_mirofish_ablation.py --write-artifact
python scripts/compare_live_mirofish_to_backtest.py --write-artifact
```

Go/No-Go:

- Go to keep collecting if scans stamp `diagnostics.mirofish_llm.provider=xai`,
  cache misses when the model changes, and weighting `applied` is false.
- No-Go / rollback to `gpt-4o-mini` if LLM circuit opens, scan latency blows
  the dashboard budget, or conviction is `None` for the whole shortlist.
- Do **not** set `MIROFISH_WEIGHTING_MODE=live` from this stage.

## Stage 2h: Historical rank blend shadow (KEEP SHADOW, 2026-09-03)

A walk-forward blend fitted on `ret_40d_fwd` over the labeled control panel
(`validation_artifacts/prob_rank_control_confirm/scored_features_control_legacy_aug_labeled.parquet`,
66,029 rows, five catalog eras). Live selection is unchanged: rank-v2 p76 still
filters and `signal_score` still sorts the book.

```env
HISTORICAL_RANK_MODE=shadow      # off | shadow | live; live is NOT approved
# HISTORICAL_RANK_ARTIFACT=      # optional override path
```

```bash
python scripts/fit_historical_rank_blend.py --exclude ml_expected_return_norm
python scripts/analyze_historical_rank_counterfactual.py
```

Fitted blend `hist_blend_83df956d8e` is **trend-only** (`trend_norm` weight 1.0,
OOS IC mean 0.044, worst era 0.019). Two features were screened out and one was
excluded for leakage:

| Feature | Standalone IC | Outcome |
|---|---|---|
| `trend_norm` | +0.043 | kept — positive in all five eras |
| `volume_norm` | −0.0005 | dropped — no cross-sectional signal |
| `prox_52w_norm` | −0.040 | dropped — negative, consistent with `RANK_V2_EXCLUDE_52W=true` |
| `ml_expected_return_norm` | +0.15 … +0.30 | **excluded** — in-sample; the canonical prob-rank model was trained on this panel holding out only `recent_current`, so unpurged weights hand it ~100% of the blend |

Five-era counterfactual (`validation_artifacts/historical_rank/counterfactual_control_legacy_aug.json`),
gates PF mean ≥ 1.20 and worst-era PF ≥ 1.00:

| Arm | n | Retention | PF mean | Worst-era PF |
|---|---|---|---|---|
| baseline (all trades) | 16,433 | 1.000 | 1.164 | 1.032 |
| `current_live_p76` | 3,952 | 0.240 | 1.063 | 0.912 |
| `current_live_p76_top5` (incumbent) | 3,482 | 0.212 | 1.028 | 0.937 |
| `historical_p76` | 3,944 | 0.240 | 1.105 | 0.865 |
| `historical_top5` | 8,354 | 0.508 | 1.160 | 1.014 |

**Verdict: `keep_shadow`.** `historical_top5` beats the incumbent on both
metrics (PF mean +0.132, worst era +0.077) and clears the worst-era gate, but
its PF mean 1.160 misses the 1.20 absolute gate. `historical_p76` fails
worst-era. Neither arm beats the bare `baseline` PF mean either, which is the
same "selection is not adding edge" pattern the Phase 2 edge audit flagged.

Go/No-Go:

- Go to keep collecting if scans stamp `diagnostics.historical_rank_mode=shadow`
  with `historical_rank_scored` > 0 and `sort_score` unchanged.
- Do **not** set `HISTORICAL_RANK_MODE=live` or `SCAN_LIVE_SORT_KEY=historical_rank`
  from this stage — the PF mean gate is unmet.
- Re-run the fit without `--exclude ml_expected_return_norm` only after a fully
  purged prob-rank model exists; the ML feature is the most likely source of the
  missing 0.04 PF.

## Stage 2i: Analog overlay (KEEP SHADOW)

Winner-neighborhood k-NN (Euclidean, fitted p1/p99 bounds) scored from
realized `control_legacy_aug` trades. Live selection is unchanged: rank-v2 p76
still filters and `SCAN_LIVE_SORT_KEY=signal_score` still sorts the book.
`ANALOG_OVERLAY_MODE=live` is a refused stub (`analog_overlay_skipped=live_not_approved`)
and does not retarget `sort_score`.

```env
ANALOG_OVERLAY_MODE=shadow      # off | shadow | live; live is NOT approved
# ANALOG_OVERLAY_ARTIFACT=      # optional override path
# ANALOG_OVERLAY_K=40
# ANALOG_OVERLAY_MIN_N=20
```

```bash
python scripts/build_analog_library.py
python scripts/analyze_analog_overlay_counterfactual.py
```

Walk-forward: each held-out era is scored against a library built from the
**other** eras only. Arms: `baseline`, `current_live_p76`,
`current_live_p76_top5`, `historical_top5` (recomputed), `analog_top5`,
`analog_p76` (diagnostic only).

Library `analog_927a3f9f68` from 16,433 `control_legacy_aug` trades (plus the
labeled parquet for extra features). Winner/loser = top/bottom quintile of
`net_return` (3,287 / 3,287). Distance is Euclidean on fitted p1/p99 bounds.

Five-era walk-forward (`validation_artifacts/analog_overlay/counterfactual_control_legacy_aug.json`):

| Arm | n | Retention | PF mean | Worst-era PF |
|---|---|---|---|---|
| baseline | 16,433 | 1.000 | 1.164 | 1.032 |
| `current_live_p76` | 3,952 | 0.240 | 1.063 | 0.912 |
| `current_live_p76_top5` (incumbent) | 3,482 | 0.212 | 1.028 | 0.937 |
| `historical_top5` (recomputed) | 8,354 | 0.508 | 1.160 | 1.014 |
| `analog_top5` | 8,356 | 0.508 | 1.162 | 1.007 |
| `analog_p76` (diagnostic) | 4,306 | 0.262 | 1.177 | 0.991 |

Analog IC (cross-sectional Spearman): vs `net_return` **−0.0024**, vs
`ret_40d_fwd` **+0.0006**. Neighborhood score is not anti-correlated the way
`signal_score` is (−0.027), but it is not a ranking signal either.

**Verdict: `keep_shadow`.** `analog_top5` beats the live p76+top5 stack
(dPF +0.134, dWorst +0.070) and edges `historical_top5` on PF mean (+0.002)
while losing worst-era (−0.007). PF mean 1.162 misses the 1.20 gate. Worst-era
clears 1.00. `analog_p76` is not a promotion candidate (worst-era 0.991).

Promotion floors are unchanged: analog top-5 PF mean ≥ 1.20 **and** worst-era
≥ 1.00 **and** it beats `current_live_p76_top5`. Missing the 1.20 floor keeps
shadow.

Go/No-Go:

- Go to keep collecting if scans stamp `diagnostics.analog_overlay_mode=shadow`
  with `analog_overlay_scored` > 0 and `sort_score` unchanged.
- If `analog_top5` PF mean < recomputed `historical_top5`: **education_only**
  (shadow column may still ship).
- If analog beats historical_top5 but PF mean < 1.20 or worst-era < 1.00:
  `keep_shadow`.
- Do **not** set `ANALOG_OVERLAY_MODE=live`, `HISTORICAL_RANK_MODE=live`, or
  `SCAN_LIVE_SORT_KEY` to analog / historical_rank from this stage.
- Purged prob-rank retrain remains the named parallel path for the remaining
  ~0.04 PF.

See [[analog-overlay]] / `wiki/analog-overlay.md` and
`validation_artifacts/analog_overlay/counterfactual_control_legacy_aug.json`.

## Stage 2j: Setup confidence shadow (KEEP SHADOW)

A survival readout rather than another quality score: does a setup look like
the 21-40 day winners or the <=20 day stop-outs? Scored in Stage B from the
same PIT registry features prob-rank already computes. Live selection is
unchanged: rank-v2 p76 still filters and `SCAN_LIVE_SORT_KEY=signal_score`
still sorts. `CONFIDENCE_RANK_MODE=live` is a refused stub
(`confidence_rank_skipped=live_not_approved`).

```env
CONFIDENCE_RANK_MODE=shadow      # off | shadow | live; live is NOT approved
# CONFIDENCE_RANK_ARTIFACT=      # optional override path
```

```bash
python scripts/build_pit_fundamental_panel.py   # one-time PIT fundamentals + Form 4
python scripts/analyze_scan_confidence.py
```

115 legal features screened on 16,433 `control_legacy_aug` trades, walk-forward
leave-one-era-out with **feature selection inside each training fold**. Labels
(`ret_*_fwd`, `net_return`, hold/exit) are rejected by name including renamed
merge copies; unpurged LightGBM outputs are excluded as in-sample on 4/5 eras.

Five-era arms (`validation_artifacts/scan_confidence/counterfactual_control_legacy_aug.json`),
gates PF mean >= 1.20 and worst-era PF >= 1.00:

| Arm | n | Retention | PF mean | Worst-era PF |
|---|---|---|---|---|
| baseline (all trades) | 16,433 | 1.000 | 1.164 | 1.032 |
| `current_live_p76_top5` (incumbent) | 3,482 | 0.212 | 1.028 | 0.937 |
| `historical_top5` | 8,354 | 0.508 | 1.160 | 1.014 |
| `analog_top5` (Stage 2i WF) | 8,356 | 0.508 | 1.162 | 1.007 |
| `pts52w_cap37` | 8,791 | 0.535 | 1.258 | 1.053 |
| `confidence_top5` | 8,356 | 0.508 | 1.172 | 1.014 |
| `cap37_then_confidence_top5` | 5,964 | 0.363 | 1.226 | 0.992 |
| **`confidence_p76`** | 3,944 | 0.240 | **1.430** | **1.121** |
| `inv_pts52w_p76` (control) | 3,945 | 0.240 | 1.421 | 1.088 |
| `inv_signal_score_p76` (control) | 3,947 | 0.240 | 1.390 | 1.093 |
| `confidence_fundamental_p76` | 3,398 | 0.207 | 1.369 | 1.063 |

Confidence IC vs `net_return` **+0.0633**, AUC on the survival cohorts 0.5722 —
the first score in the stack pointing the right way (`signal_score` is −0.0262).

**Verdict: `keep_shadow_single_variable_explains_it`.** `confidence_p76` clears
both floors and beats the incumbent by +0.402 PF mean / +0.184 worst era, with
non-overlapping bootstrap intervals (1.315-1.556 vs 0.944-1.138). But it beats
the single-variable control `inv_pts52w_p76` by only **+0.0084** PF against the
+0.03 incremental bar, and their intervals overlap. The blend is largely a
re-derivation of the already-promoted `pts_52w <= 37` rule.

Two findings worth acting on separately:

- Every chart/trend variable is **negatively** correlated with realized return
  in all five eras, and extension drives early stop-outs monotonically
  (16.9% -> 28.6% across `close_vs_sma200_pct` quintiles).
- `close_vs_sma200_pct` is +0.044 IC against raw `ret_40d_fwd` on the full
  candidate panel (Stage 2h) but -0.047 against realized `net_return` on
  entered trades. Conditioning on entry plus the stop/trail policy flips the
  sign. Ranking studies fitted on raw forward returns do not transfer.
- PIT fundamentals (Sharadar, 56,205 filings) and insider Form 4 (291,280
  ticker-days) were tested properly and carry no signal. Only the free cash
  flow family holds a consistent sign, and weakly.

Go/No-Go:

- Go to keep collecting if scans stamp `diagnostics.confidence_rank_mode=shadow`
  with `confidence_rank_scored` > 0 and `sort_score` unchanged.
- Do **not** set `CONFIDENCE_RANK_MODE=live` or `SCAN_LIVE_SORT_KEY=setup_confidence`.
- The open question this raises is whether `signal_score` should remain the
  live sort key at all, given its own negative outperformed it on this book.

See `wiki/scan-confidence.md` and `validation_artifacts/scan_confidence/report.md`.

## Stage 2k: Next-factor tests (research only, 2026-09-21)

Three tests on frozen books. No live sort change. Artifact:
`validation_artifacts/scan_confidence/next_factor_tests.md`.

| Test | Result | Decision |
|---|---|---|
| Grace or a wider stop on the extended quintile, and removing the 0.9 tighten when 20-day return > 6% | Chunk paths end on the original exit (median 16 bars). Longer holds and wider stops have no post-exit bars. | Not interpretable. Do not change exits. |
| `distribution_score` p76 vs invert `pts_52w` | PF 1.3225 / worst 0.9571 vs 1.4212 / 1.0881. Lift −0.0987. IC +0.0377 (5/5). | Misses both the +0.03 bar and the worst-era floor. Do not attach. |
| PEAD walk-forward drop of the most extended quintile | Kept n=21,781 at 1.5989 / 1.2046 vs baseline 1.5502 / 1.1672. Dropped tail 1.3615 / 0.8970. Surprise coverage 11.9%. | PEAD-only shadow. Surprise not scored. Do not paste onto Stage 2. |

## Stage 2l: VCP-exit replay and purged net-return rank (research only, 2026-09-26)

Two measurements. Neither changes the live exit, `SCAN_LIVE_SORT_KEY`, or `PROB_RANK_MODE`.

| Track | Result | Decision |
|---|---|---|
| Skip VCP invalidation under grace 15/40, with 50 pre-entry bars | Grace 1.3250 / 1.1283 (n=16,241). Skip 1.3624 / 1.1133. Mean lift +0.037. Worst era fell. 10,151 grace exits were VCP; the skip arm exits on the trail, SMA50, or the 40-day clock. | `shadow_ready_exit_not_promoted`. Do **not** add a live skip. The mean clears +0.03 and both floors; the worst era moved the wrong way. |
| Purged ridge on `net_return` (embargo 40d). Forward returns and `expected_return_40d` excluded. | Walk-forward p76: model 1.2348 / **0.9589** vs inverted `pts_52w` 1.3862 / 1.0298 (n=16,433 scored). | `do_not_promote_purged_rank`. The purged model misses the worst-era floor and loses to the single variable. |

```bash
python scripts/analyze_vcp_invalidation_exit.py
python scripts/fit_purged_net_rank.py
```

Artifacts: `validation_artifacts/vcp_invalidation_exit.json`, `validation_artifacts/purged_net_rank/counterfactual.json`.

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
