# Backtest Catalog

> Clean summary of Schwab-only multi-era backtests, counterfactuals, and
> promotion decisions. Numbers are taken from `validation_artifacts/` as of
> **2026-07-22**. Profit factor (PF) is net of costs unless noted.

---

## At a glance

| Question | Answer |
|---|---|
| Bare-signal verdict | **`proceed`** after `pts_52w≤37` — PF mean **1.214**, worst-era **1.105** (`stage2_pts52w_cap37`, 2026-07-18) |
| Pre-cap bare (historical) | `stage2_only_aug` — PF mean **1.162**, worst-era **1.032** (`iterate_with_caution`) |
| Promotion gates | PF mean ≥ **1.20**, worst-era PF ≥ **1.00** |
| Bare signal clears gates? | **Yes** with live `PTS_52W_CAP_MODE` (blocks lifted for bare gate; keep PROB_RANK shadow) |
| Promoted offline stack (pre-cap) | Exit grace 15/40 + 1% breakout buffer → PF mean **1.212**, worst **1.037** |
| Post-cap stack (pts_52w≤37) | Buffer book PF mean **1.283**, worst **1.058**; **p75 fails** worst-era — **p76 live** (PF mean **1.323**, worst **1.034**) |
| PF 1.50 peer (Track B) | **`pead_primary_aug_fixed_full_c40`** clears — PF mean **1.550**, worst **1.167**; pullback full **rejects** (worst 0.959) |
| PEAD × Stage2 buffer? | **No** — 1% breakout buffer fails worst-era (**0.922**); keep **exit-grace-only** on PEAD |
| Optional trim on stack (pre-cap) | Rank-v2 p75 → PF mean **1.249**, worst **1.120** (25% retention) |
| Canonical baseline run | `control_legacy_aug` (16,433 trades, 5 eras) |
| Canonical bare run (pre-cap) | `stage2_only_aug` (16,423 trades, 5 eras) |
| Canonical bare run (post-cap) | `stage2_pts52w_cap37` (15,994 trades, 5 eras) |

**North star:** raise bare-signal edge before promoting more plugins LIVE.
`pts_52w≤37` is now part of Stage A live. Overlays vs *pre-cap* bare were **neutral** (~0.001 PF).

---

## How to read these results

### Eras (Schwab-only, SP1500 universe)

| Era | Window | Role |
|---|---|---|
| `late_bull` | 2015-01-01 → 2017-12-31 | Strong bull regime |
| `volatility_chop` | 2018-01-01 → 2019-12-31 | Sideways / choppy |
| `crash_recovery` | 2020-01-01 → 2021-12-31 | Crash + rebound |
| `bear_rates` | 2022-01-01 → 2023-12-31 | Bear / rising rates |
| `recent_current` | 2024-01-01 → present | Live-adjacent regime |

### Metrics that matter

| Metric | Why |
|---|---|
| **PF mean** | Equal-weight mean of per-era profit factors (sizing-invariant) |
| **Worst-era PF** | Robustness floor — must stay ≥ 1.00 for promotion |
| **Retention %** | Share of baseline trades kept by a filter |
| Portfolio return / DD | Deployable equity-path metrics (capacity-capped book); not used for PF gates |

### Artifact locations

| Kind | Path pattern |
|---|---|
| Multi-era summaries | `validation_artifacts/multi_era_backtest_schwab_only_<run_id>.json` |
| Trade chunks | `validation_artifacts/multi_era_chunks/<run_id>/` |
| Phase 2 audit | `validation_artifacts/phase2_edge_audit*.json/.md` |
| Stack / sweeps | `validation_artifacts/signal_stack_counterfactual_*.json`, `sweep_cf_*.json` |
| Entry timing | `validation_artifacts/entry_timing_shadow_counterfactual_*.json` |

Runner: `scripts/run_multi_era_backtest_schwab_only.py`.
Audit: `scripts/phase2_edge_audit.py`.

---

## 1. Canonical baselines (five-era, full universe)

### Phase 2 edge audit (2026-07-17)

Source: `phase2_edge_audit_post_rank_live.json`

| Run | Role | Trades | PF mean | Worst-era PF | Verdict |
|---|---|---:|---:|---:|---|
| `stage2_only_aug` | Bare signal | 16,423 | **1.162** | **1.032** | `iterate_with_caution` |
| `control_legacy_aug` | Control (+ light overlays) | 16,433 | **1.164** | **1.032** | overlays neutral |

Aligned PF mean delta (control − bare): **+0.001** → overlays do not explain the edge gap to 1.20.

### Per-era PF — bare (`stage2_only_aug`)

| Era | Trades | PF | Win rate | Expectancy |
|---|---:|---:|---:|---:|
| `crash_recovery` | 2,771 | **1.433** | 55.0% | +1.20% |
| `late_bull` | 4,608 | **1.251** | 52.1% | +0.54% |
| `volatility_chop` | 2,549 | 1.050 | 52.6% | +0.12% |
| `recent_current` | 3,870 | 1.047 | 47.1% | +0.16% |
| `bear_rates` | 2,625 | **1.032** | 48.4% | +0.10% |

Weakest era: **`bear_rates`**. Strongest: **`crash_recovery`**.

### Other five-era baselines

| Run ID | Trades | PF mean | Worst-era | Notes |
|---|---:|---:|---:|---|
| `control_legacy` | 16,484 | 1.156 | 1.015 | Pre-augmentation control |
| `control_prod_default` | 1,811 | 1.126 | 0.963 | Smaller / prod-default universe — not the promotion baseline |
| `control_prod_default_aug` | 840 | 0.802 | 0.000 | Thin / incomplete — ignore for gates |

---

## 2. Promoted signal stack (counterfactual)

Source: `signal_stack_counterfactual_control_legacy_aug.json` (2026-07-16)

Offline replay on `control_legacy_aug` with exit profile `exit_grace_t15_h40`.

| Scenario | Trades | Retention | PF mean | Worst-era | Gates |
|---|---:|---:|---:|---:|---|
| `legacy_baseline` | 16,402 | 100% | 1.169 | 1.050 | Fail PF mean |
| `exit_grace_all` | 16,402 | 100% | 1.169 | 1.030 | Fail PF mean |
| **`exit_grace_breakout_buffer_0.010`** | **6,869** | **41.9%** | **1.212** | **1.037** | **Pass** |
| `exit_grace_breakout_buffer_rank_v2_p75` | 1,724 | 25.1%* | **1.249** | **1.120** | **Pass** |

\*Retention for rank-v2 row is vs the buffer-filtered set (not vs full baseline).

### Stack per-era PF (`exit_grace_breakout_buffer_0.010`)

| Era | PF |
|---|---:|
| `crash_recovery` | 1.479 |
| `late_bull` | 1.262 |
| `bear_rates` | 1.161 |
| `volatility_chop` | 1.120 |
| `recent_current` | 1.037 |

### Live operating stack (Stages 2c–2d)

| Knob | Setting |
|---|---|
| Entry timing | `ENTRY_TIMING_SHADOW_MODE=live`, 1% breakout buffer, SMA50 filters disabled |
| Exit manager | `EXIT_MANAGER_MODE=live`, min hold 15d, max hold 40d |
| Rank-v2 | p76 trim (live; retuned 2026-07-22 under pts_52w≤37) |
| Apply helper | `python scripts/apply_signal_stack_enforced_env.py` |

Bare-signal PF (unfiltered Stage 2) still sits below 1.20 — stack filters improve the *selected* book, they do not rewrite the bare-signal audit.

---

## 3. Parameter sweeps (2026-07-13)

All sweeps start from the exit-grace + buffer stack on `control_legacy_aug`.

### Breakout buffer width

| Buffer | Retention | PF mean | Worst-era | Pass? |
|---|---:|---:|---:|---|
| **1.0%** (promoted) | 41.9% | **1.212** | **1.037** | Yes |
| 1.2% | 35.1% | 1.196 | 0.994 | No |
| 1.5% | 27.5% | 1.213 | 0.984 | No |
| 2.0% | 18.8% | 1.256 | 0.961 | No |

Tighter buffers raise PF mean but punch through the worst-era floor. **1.0% is the robust pick.**

### Exit grace profiles

| Profile | Stack PF mean | Worst-era | Pass? |
|---|---:|---:|---|
| **t15 / h40** (promoted) | 1.212 | 1.037 | Yes |
| t15 / h30 | 1.212 | 1.037 | Yes* |
| t10 / h40 | 1.109 | 0.941 | No |

\*t15/h30 matched the buffer stack in the sweep artifact; live/policy choice remains **15-day grace / 40-day hold**.

### Rank-v2 percentile on the promoted stack

| Percentile | Retention† | PF mean | Worst-era | Pass? |
|---|---:|---:|---:|---|
| p60 | 40.0% | 1.211 | 1.103 | Yes |
| p70 | 30.0% | 1.231 | 1.143 | Yes |
| p72 | 28.1% | 1.226 | 1.090 | Yes |
| p73 | 27.0% | 1.230 | 1.090 | Yes |
| p74 | 26.0% | 1.236 | 1.109 | Yes |
| **p75** | **25.1%** | **1.249** | **1.120** | **Yes (plateau max)** |
| p76 | 24.1% | 1.238 | 1.138 | Yes |
| p78 | 22.0% | 1.208 | 1.156 | Yes |
| p80 | 20.0% | 1.157 | 1.046 | No |

†Vs buffer-filtered book. Plateau is p73–p76; **p75** chosen as the peak.

Artifacts: `validation_artifacts/sweep_cf_*.json`.

---

## 4. Entry-timing offline study

Source: `entry_timing_shadow_counterfactual_control_legacy_aug.json`

| Finding | Detail |
|---|---|
| Best simple rule | Breakout buffer only @ 1.0% |
| Retention | 41.9% |
| Overlap PF mean | 1.224 (Δ +0.047 vs baseline overlap) |
| Early-stop delta | +1.3 pp (not improved) |
| Composite SMA50 caps | Hurt or failed retention gates |
| Offline action | Keep timing logic; do **not** promote on overlap criteria alone |
| Why stack still shipped | Combined with exit grace, PF gates clear (section 2) |

---

## 5. Phase 1 gate experiments (hard Stage A filters)

Full-universe multi-era treatments vs `control_legacy`. Guardrails require no thin eras and no large era regressions.

| Config | Trades | PF mean | Worst-era | Δ vs control | Guardrails | Decision |
|---|---:|---:|---:|---:|---|---|
| `breakout_vol_120` | 14,730 | 1.174 | 1.051 | +small | Mixed | Soft / shadow only |
| `breakout_vol_150` | 12,586 | 1.128 | 0.988 | − | Fail worst-era | **Reject** |
| `breakout_vol_120_buffer_010` | 13,226 | 1.133 | 1.034 | −0.023 | Fail (crash Δ) | **Reject as hard stack** |
| `vcp_pre_breakout` | 16,491 | 1.154 | 1.007 | ~flat | Marginal | Keep measurement only |
| `signal_gate_combo` | 40 | 1.344 | 0.111 | — | Fail thin | **Reject** |
| `confluence_either` | 597 | 1.747 | 0.300 | +0.59 | Fail thin/regress | **Shadow only** |
| `confluence_both` | 1 | ~0 | 0.0 | −1.19 | Fail | **Hard block** |
| `breakout_2bar` | 1,123 | 0.834 | 0.632 | − | Fail | **Reject** |

### Confluence + PEAD memo (2026-07-09)

- `confluence_both` → OFF  
- `confluence_either` → shadow only  
- PEAD → score enrichment, **not** a hard gate  
- Prefer exit-grace + 1% buffer path (section 2)

---

## 6. Exit-path diagnostics

| Study | Result |
|---|---|
| Exit grace smoke (3-era sample) | Plumbing OK; full five-era replay preferred over thin smoke multi-era files |
| Direct multi-era files `exit_grace_t*` | **0 trades** in summary JSONs — incomplete chunk runs; use counterfactual replay instead |
| Hold 21–40d cohort | Very high PF (~3.4–4.3) on kept winners — supports longer hold / grace |
| Early stopouts (≤20d) | ~22% of baseline book; primary drag on PF |

---

## 7. Portfolio sizing audit (Phase 0)

Source: `phase0_sizing_audit_20260418T030038Z.*`

Legacy aggregator chained per-trade % returns and produced fictional **−94% to −99%** drawdowns. Portfolio simulator (starting equity $100k, max 10 positions, 0.75% risk/trade) restores realistic equity paths. **PF is unchanged** (sizing-invariant).

| Era (sample) | Legacy max DD | Portfolio max DD | Portfolio return |
|---|---:|---:|---:|
| `recent_current` | −99.8% | −23.8% | +13.7% |
| `bear_rates` | −94.8% | −12.1% | −0.1% |
| `crash_recovery` | −99.3% | −17.1% | +11.8% |

Use `portfolio_summary` / portfolio returns for deployability; use **PF** for promotion gates.

---

## 8. Decision log (compressed)

| Date | Decision | Evidence |
|---|---|---|
| 2026-04-18 | Adopt portfolio equity simulator | Phase 0 sizing audit |
| 2026-06-27 | Exit grace plumbing validated | `exit_grace_smoke_compare` |
| 2026-06-28 | Reject 2-bar breakout confirm | `breakout_2bar` PF 0.83 |
| 2026-06-30 | Lock augmented baselines | `stage2_only_aug`, `control_legacy_aug` complete |
| 2026-07-07 | Entry timing: buffer-only preferred offline | Entry-timing CF |
| 2026-07-08 | Reject hard vol×buffer Stage A stack | `breakout_vol_120_buffer_010` |
| 2026-07-09 | Confluence hard gates fail; PEAD soft only | Confluence memo |
| 2026-07-13 | Buffer 1.0% + rank p75 plateau | `sweep_cf_*` |
| 2026-07-16 | Stack clears PF gates; rank-v2 p75 promoted | Signal-stack CF |
| 2026-07-17 | Re-audit still `iterate_with_caution` | Phase 2 edge audit |
| 2026-07-18 | Promote `PTS_52W_CAP_MODE=live` (max 37); bare clears gates | `stage2_pts52w_cap37` PF mean 1.214 / worst 1.105 |
| 2026-07-18 | Start PF 1.50 dual-track (A early-stop + B peer generators) | See §11 |
| 2026-07-18 | Stack CF under pts_52w≤37: buffer book still passes; p75 fails worst-era | `signal_stack_counterfactual_control_legacy_aug_pts52w_cap37` |
| 2026-07-19 | Full-universe pullback bare completes; fails worst-era | `pullback_only_aug_full` PF mean 1.211 / worst **0.959** |
| 2026-07-20 | Rank-v2 under pts_52w≤37: **p76** first clear (re-live candidate) | `sweep_cf_rank_under_pts52w_cap37_control_legacy_aug` |
| 2026-07-21 | Full-universe PEAD primary clears strict PF 1.50 | `pead_primary_aug_fixed_full_c40` PF mean **1.550** / worst **1.167** |
| 2026-07-22 | PEAD exit-grace transfer keeps 1.50; pullback pts_52w CF no lift | peer stack-transfer + `pts52w_cap_cf_pullback_*` |
| 2026-07-22 | Built PEAD entry-timing cache (100%); Stage2 1% buffer **rejects** on PEAD | `entry_timing_replay_cache_pead_primary_aug_fixed_full_c40` + full stack CF |
| 2026-07-22 | Retune live rank-v2 **p75 → p76** under pts_52w≤37 | `sweep_cf_rank_under_pts52w_cap37_*` → `SIGNAL_STACK_ENFORCED_ENV` |
| 2026-07-22 | Restored PEAD dual-admit runtime; full-scan compare **pass** | `pead_primary_shadow_compare_20260722T103711Z` (eval 1492 / admit 1) |
| 2026-07-22 | Full SP1500 PEAD shadow post-restore **pass** (admit 8) | `pead_primary_shadow_scan_*_full_sp1500_post_restore` + compare `…111606Z` |
| 2026-07-22 | First post-p76 live retention session **qualifies** (24.0%) | `rank_filter_v2_live_session_*_post_p76_from_pead_full` |
| 2026-07-22 | PEAD capacity-aware CF: prefer `top5_by_edge_score` shadow; buffer still reject | `pead_primary_capacity_cf_pead_primary_aug_fixed_full_c40` |
| 2026-07-22 | Wired shadow-only PEAD rank to Stage-A `edge_score` + capacity top-5; dual-admit **pass** | `pead_primary_shadow_scan_*_full_sp1500_rth_edge_top5_20260722` + compare `…205237Z` |
| 2026-07-23 | Second post-wire dual-admit compare **pass** (capacity top-5); dq=stale | `pead_primary_shadow_scan_*_full_sp1500_post_p76_20260723` + compare `…043035Z` |
| 2026-07-23 | Drafted PEAD non-executable canary sleeve design (no enablement) | `docs/PEAD_CANARY_SLEEVE_DESIGN.md` |
| 2026-07-23 | Wired canary sleeve block into compare + shadow ledger + summarize script | `canary_sleeve` on compare; `summarize_pead_dual_admit_sessions.py` |
| 2026-07-23 | last_scan dual-admit compare **pass** (dq=ok; partial eval) | `pead_primary_shadow_compare_20260724T005632Z` |
| 2026-07-27 | Fresh bare edge audit: verdict unchanged `iterate_with_caution` (PF mean 1.162 / worst 1.032; overlays neutral) | `phase2_edge_audit.json` (2026-07-28T00:09Z) |
| 2026-07-27 | Two dual-admit compare **pass** sessions (last_scan dq=ok eval 608; full post-close dq=stale eval 1392, admit 33, 0 leak) | compares `…001634Z` + `…004926Z` |
| 2026-07-27 | Post-close p76 retention 25.0% in band but dq=stale — not a qualifying session | `rank_filter_v2_live_session_20260728T004926Z_post_p76_postclose_20260727` |

---

## 11. PF 1.50 dual-track (2026-07-18 → 2026-07-23)

Strict target: five-era equal-weight net PF mean ≥ **1.50**, worst-era ≥ **1.00**.

### Track A — early-stop avoidance (`control_legacy_aug`)

| Artifact | Result |
|---|---|
| `early_stopout_cohorts_control_legacy_aug.json` | Early stops **21.91%**; oracle drop-all-early-stops PF mean **3.64** / worst **3.07** (ceiling clears 1.50) |
| `early_stop_preentry_cf_control_legacy_aug.json` | Best incremental rule **`pts_52w_cap_35`**: PF mean **1.314** / worst **1.065** / retention **40.3%** / lift **+0.150** → shadow candidate; **does not** clear 1.50 |

`EARLY_STOP_GATE_MODE` defaults to **shadow** (`EARLY_STOP_GATE_PTS_52W_MAX=35`, buffer min 1%). Do **not** set live until a rule clears strict 1.50 or an agreed incremental promotion bar with ledger.

### Track B — peer generators

| Profile | Env overrides | Status |
|---|---|---|
| `pullback_only_aug` (smoke) | `research/env_overrides/pullback_only_aug.json` | 40 tickers: PF mean **1.492** / worst **1.062** — optimistic vs full universe |
| `pullback_only_aug_full` | same | **Complete** (2026-07-19): PF mean **1.211** / worst **0.959** / 17,183 trades — clears PF mean, **fails worst-era** (`recent_current` 0.959). Reject as 1.50 peer. |
| `pead_primary_aug_fixed_full_c40` | `research/env_overrides/pead_primary_aug.json` | **Complete** (2026-07-21): PF mean **1.550** / worst **1.167** / 27,422 trades / universe 1505 — **clears strict 1.50**. Portfolio equity path is capacity-saturated (ignore portfolio return for PF gates). |

#### Pullback full per-era (`pullback_only_aug_full`)

| Era | Trades | PF |
|---|---:|---:|
| `crash_recovery` | 2,818 | **1.502** |
| `late_bull` | 4,832 | **1.344** |
| `volatility_chop` | 2,699 | 1.151 |
| `bear_rates` | 2,748 | 1.099 |
| `recent_current` | 4,086 | **0.959** |

#### PEAD full per-era (`pead_primary_aug_fixed_full_c40`)

| Era | Trades | PF |
|---|---:|---:|
| `crash_recovery` | 4,397 | **2.004** |
| `late_bull` | 6,266 | **1.833** |
| `bear_rates` | 4,341 | **1.384** |
| `recent_current` | 8,397 | **1.362** |
| `volatility_chop` | 4,021 | **1.167** |

#### Stack transfer

| Artifact | Bare PF mean | Exit-grace t15/h40 | Buffer 1.0% | Action |
|---|---:|---:|---:|---|
| `peer_generator_stack_transfer_pullback_only_aug.json` (smoke) | 1.492 | 1.455 | — | `pass_pf_120_…_ready_for_full_universe` (stale vs full) |
| `peer_generator_stack_transfer_pullback_only_aug_full.json` | 1.274\* | 1.260 | — | Passes 1.20 on **4 eras** (missing `recent_current` chunks on disk) |
| `peer_generator_stack_transfer_pead_primary_aug_fixed_full_c40.json` | **1.550** | **1.549** / worst **1.191** | **1.361** / worst **0.922** | **`reject_breakout_buffer_keep_exit_grace_only`** |

\*Chunk-only baseline omits `recent_current` (0 chunks under `multi_era_chunks/pullback_only_aug_full/recent_current` even though the summary JSON includes that era). Prefer the five-era summary for gate calls.

**PEAD full stack (2026-07-22):** cache `entry_timing_replay_cache_pead_primary_aug_fixed_full_c40` (27,422 / 27,422). Source: `signal_stack_counterfactual_pead_primary_aug_fixed_full_c40.json`, buffer sweep `pead_buffer_sweep_under_exit_grace_pead_primary_aug_fixed_full_c40.json`.

| Scenario | Trades | Retention | PF mean | Worst-era | Strict 1.50 |
|---|---:|---:|---:|---:|---|
| bare / exit-grace | 27,422 | 100% | **1.549–1.550** | **1.167–1.191** | **Pass** |
| + breakout buffer ≥1.0% | 5,570 | 20.3% | 1.361 | **0.922** (`volatility_chop`) | **Fail** |
| + buffer + rank-v2 p76 | 3,096 | 55.6%† | 1.395 | **0.815** | **Fail** |

†Retention vs buffer-filtered book.

PEAD entries are **not** Stage2 breakouts: median `breakout_buffer_pct` ≈ **-1.1%** (67% negative). Any buffer floor ≥0.1% drops retention to ~31% and breaks worst-era. **Do not** transfer the Stage2 1% buffer arm onto `pead_primary`.

#### PEAD capacity-aware CF (2026-07-22)

Artifact: `pead_primary_capacity_cf_pead_primary_aug_fixed_full_c40.json` (+ `.md`). Source book: `pead_primary_aug_fixed_full_c40` + entry-timing cache (27,422). Exit-grace caches: `pead_primary_exit_grace_replay_*_{t15_h40,t10_h40,t15_h30}.json`.

Gates: five-era equal-weight net PF mean ≥ **1.50**, worst-era ≥ **1.00**, no thin eras (<50). Portfolio metrics are **capacity notes only** (bare book is capacity-saturated).

| Arm | N | Ret% | PF mean | Worst | Cap.filt (max10) | Port.ret% | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `bare_full_book` | 27,422 | 100 | **1.550** | **1.167** | 25,390 | −99.99 | **Pass** (saturated) |
| `exit_grace_t15_h40_full_book` | 27,422 | 100 | **1.549** | **1.191** | 25,390 | −99.99 | **Pass** |
| `exit_grace_t15_h30_full_book` | 27,422 | 100 | **1.549** | **1.191** | 25,390 | −99.99 | **Pass** (≈ t15/h40 on this book) |
| `exit_grace_t10_h40_full_book` | 27,422 | 100 | 1.462 | 1.239 | 25,390 | −99.99 | **Fail** PF mean |
| `top5_by_edge_score` (**selected**) | 7,268 | 26.5 | **1.511** | **1.291** | 5,789 | −60.4 | **Pass** |
| `top8_by_edge_score` | 10,400 | 37.9 | **1.565** | **1.379** | 8,799 | −93.5 | **Pass** |
| `edge_score_p70` | 8,227 | 30.0 | **1.599** | **1.143** | 7,026 | −81.1 | **Pass** |
| `top10_by_composite_score` | 12,170 | 44.4 | **1.501** | **1.288** | 10,492 | −97.4 | **Pass** |
| `top3_by_edge_score` | 4,724 | 17.2 | 1.492 | 1.189 | 3,311 | **+36.8** | **Fail** PF mean (near-miss; best port.ret) |
| `top*_by_signal_score` / `signal_score_p*` | — | — | ≤1.47 | mixed | — | — | **Fail** |
| `transfer_breakout_buffer_ge_0.010` | 5,570 | 20.3 | 1.355 | **0.907** | 4,289 | −66.8 | **Reject** |
| `transfer_pts_52w_cap_37` | 25,403 | 92.6 | **1.582** | **1.171** | 23,400 | −99.99 | Clears gates but **not** PEAD-native default |
| `transfer_rank_v2_p76` | 6,590 | 24.0 | **1.622** | **1.065** | 5,538 | −60.4 | Clears gates but **not** PEAD-native default |

**Operating stack for continued shadow (not live):**

| Layer | Choice |
|---|---|
| Executable entries | Stage2 only |
| PEAD mode | `STRATEGY_PEAD_PRIMARY_MODE=shadow`, `ALLOW_LIVE=false` |
| PEAD entry | beat + liquidity (dual-admit shadow) |
| PEAD exit | `exit_grace_t15_h40` (t15/h30 tied; **reject** t10/h40) |
| PEAD rank / capacity | **`top5_by_edge_score`** (PEAD-native); runtime: `PEAD_PRIMARY_SHADOW_RANK_TOP_N=5`, sort `edge_score_desc,ticker_asc` (diagnostics only) |
| PEAD sizing note | max-positions + risk-per-trade with score-priority fill |
| Do **not** default | Stage2 1% breakout buffer; do not promote pts_52w / rank-v2 as PEAD policy even though they clear offline |

Dual-admit leak check on this run: **pass** (`pead_only_is_executable=false`, `ALLOW_LIVE=false`). Lookback sweeps (`PEAD_LOOKBACK_DAYS` ∈ {10,15,20,30}) **deferred** — need new full-universe entry books.

#### Dual-admit sessions after edge_score top-5 wire

| Artifact | eval | admit | overlap | leak | capacity_top_n | dq | verdict |
|---|---:|---:|---:|---:|---:|---|---|
| `pead_primary_shadow_scan_20260722T205228Z_full_sp1500_rth_edge_top5_20260722` + compare `…205237Z` | **1363** | **9** | **1** | **0** | **5** | ok | **pass** |
| `pead_primary_shadow_scan_20260723T043023Z_full_sp1500_post_p76_20260723` + compare `…043035Z` | **940** | **13** | **2** | **0** | **5** | **stale** | **pass** |
| Dashboard `last_scan` 2026-07-23T21:43Z + compare `…005632Z` | **349** | **8** | **2** | **0** | fallback top5† | **ok** | **pass** |
| Dashboard `last_scan` 2026-07-27T22:06Z + compare `…001634Z` | **608** | **19** | **4** | **0** | **5** | **ok** | **pass** |
| `pead_primary_shadow_scan_20260728T004857Z_full_sp1500_postclose_20260727` + compare `…004926Z` | **1392** | **33** | **11** | **0** | **5** | **stale** | **pass** |

†Partial PEAD eval (349/1505); shadow rows lacked `capacity_top_n` / edge sort (process likely pre-wire). Canary tickers via first-5 fallback: CME, GM, LMT, NEM, NOC. Still Stage2-only executable.

Both wired sessions + this last_scan: `executable_stage2_only=True`. Soft list cap remains `PEAD_PRIMARY_SHADOW_MAX_NAMES=50`.

**Canary sleeve:** design + compare `canary_sleeve` reporting wired (`docs/PEAD_CANARY_SLEEVE_DESIGN.md`). Still **no** PEAD live / `ALLOW_LIVE`. Prefer ≥1 more **RTH-hours** full dual-admit with **current** edge top-5 diagnostics (`capacity_rank_arm` set, eval coverage healthy) before enablement talk.

#### Pullback × pts_52w≤37 offline CF

`pts52w_cap_cf_pullback_only_aug_full_cap37.json`: retention **99.3%**, PF mean lift **+0.003** → `keep_shadow_only` (cap is nearly a no-op on the pullback book).

### Post-cap stack fidelity (control entry cache ∩ pts_52w≤37)

Sources: `signal_stack_counterfactual_control_legacy_aug_pts52w_cap37.json`, `sweep_cf_rank_under_pts52w_cap37_control_legacy_aug.json`.

| Scenario | Trades | Retention† | PF mean | Worst-era | Gates |
|---|---:|---:|---:|---:|---|
| `cap_only_bare` | 8,773 | 53.5% vs uncapped | 1.260 | 1.062 | Pass |
| `exit_grace_t15_h40` | 8,773 | 100% | 1.249 | 1.079 | Pass |
| **`exit_grace_breakout_buffer_0.010`** (selected) | **3,559** | **40.6%** | **1.283** | **1.058** | **Pass** |
| `…_rank_v2_p75` (pre-retune live) | 890 | 25.0% | 1.299 | **0.955** | **Fail worst-era** |
| `…_rank_v2_p76` (**live** post 2026-07-22) | 859 | 24.1% | **1.323** | **1.034** | **Pass** |

†Retention for buffer row is vs capped book; rank rows vs buffer-filtered capped book.

**Operator note:** live rank-v2 retuned **p75 → p76** so the post-cap stack clears worst-era (`late_bull`). Buffer-only remains a valid fallback (PF mean 1.283 / worst 1.058) if p76 retention drifts outside ~22–35%.

---

## 9. What is still open

1. ~~Bare-signal PF mean ≥ 1.20~~ — cleared via live `pts_52w≤37` (`stage2_pts52w_cap37`).  
2. ~~Stack CF with pts_52w cap~~ — done; buffer book passes; **p76 live** (p75 retired under cap).  
3. ~~Track B full-universe bare runs~~ — pullback **reject** (worst 0.959); PEAD **clears 1.50**.  
4. Collect **live-enforced** evidence for Stage 2c–2d stack + pts_52w cap + **p76** (filter diagnostics / 1–2 RTH weeks).  
5. ~~PEAD entry-timing cache + full stack CF~~ — done; **reject Stage2 buffer on PEAD**; keep exit-grace-only for the 1.50 peer.  
6. Repair `pullback_only_aug_full` `recent_current` chunks if any further pullback CFs are needed (summary has era; chunk dir empty).  
7. Do **not** re-enable hard breakout-volume or confluence Stage A gates without a fresh five-era pass.  
8. **PROB_RANK** stays **KEEP SHADOW** — do not set `PROB_RANK_MODE=live`.  
9. Regime v2 / Correlation Guard LIVE still require operator promotion + shadow evidence.  
10. Track A: keep `EARLY_STOP_GATE_MODE=shadow`.  
11. ~~Stage2 post-cap p76 vs buffer-only~~ — **p76 selected** (higher PF mean than buffer-only; clears gates).  
12. ~~PEAD dual-admit runtime~~ — restored 2026-07-22; full SP1500 post-restore shadow **pass** (eval **1493** / admit **8** / overlap **1** / 0 leak). Collect more RTH sessions; do **not** make PEAD-only executable.  
13. Stage2 **p76** live retention: first post-retune qualifying session **pass** (eval **25** / ret **24.0%** / dq=ok) via `record_rank_v2_live_session.py`. Need ≥1 more distinct RTH/`ok` session.  
14. ~~PEAD capacity-aware CF~~ — done 2026-07-22 (`pead_primary_capacity_cf_*`): prefer **`top5_by_edge_score`** + exit-grace t15/h40 in shadow; **reject** Stage2 buffer; pts_52w/rank-v2 clear offline but stay non-default. Still **no** PEAD live / canary sleeve.  
15. Optional: new full-universe PEAD books for `PEAD_LOOKBACK_DAYS` soft sweep {10,15,20,30}.  
16. ~~Dual-admit evidence after edge top-5 wire (≥2 sessions)~~ — **2/2 compare-pass** (`…205237Z` dq=ok; `…043035Z` dq=stale). **Go for canary sleeve design discussion only**; still no enablement / `ALLOW_LIVE`. Prefer ≥1 more RTH-hours `dq=ok` before any enablement talk.  
17. ~~PEAD non-executable canary sleeve design~~ — drafted `docs/PEAD_CANARY_SLEEVE_DESIGN.md`; compare/ledger now emit `canary_sleeve` (still **no enablement**).  
18. ~~Optional: dashboard surface of `canary_sleeve` names~~ — Today scan lane `#peadCanarySection` (2026-07-27).  
19. Next evidence: ≥1 **RTH-hours** full dual-admit via `run_pead_primary_shadow_scan.py` on **current** code (`capacity_rank_arm=top5_by_edge_score`, healthy `pead_primary_evaluated`) with `dq=ok` + compare pass — then operator review before any enablement talk.

---

## 10. Quick command reference

```bash
# Multi-era Schwab-only run
python scripts/run_multi_era_backtest_schwab_only.py --run-tag <id>

# Peer generator bare (Track B)
python scripts/run_multi_era_backtest_schwab_only.py \
  --env-overrides research/env_overrides/pullback_only_aug.json \
  --run-tag pullback_only_aug_full --no-resume
python scripts/run_multi_era_backtest_schwab_only.py \
  --env-overrides research/env_overrides/pead_primary_aug.json \
  --run-tag pead_primary_aug_fixed_full_c40 --chunk-size 40 --max-workers 1 \
  --warm-earnings-cache

# Early-stop oracle + pre-entry CF (Track A)
python scripts/analyze_early_stopout_cohorts.py --run-id control_legacy_aug
python scripts/analyze_early_stop_preentry_counterfactual.py --run-id control_legacy_aug

# Phase 2 bare vs control audit
python scripts/phase2_edge_audit.py

# Stack / peer transfer
python scripts/build_entry_timing_replay_cache.py --run-id pead_primary_aug_fixed_full_c40
python scripts/analyze_signal_stack_counterfactual.py  # see script --help
python scripts/analyze_peer_generator_stack_transfer.py --run-id pead_primary_aug_fixed_full_c40
python scripts/analyze_pead_primary_capacity_counterfactual.py  # PEAD-native capacity CF
python scripts/summarize_pead_dual_admit_sessions.py            # canary dual-admit session table
python scripts/sweep_rank_v2_under_pts52w_cap.py --run-id control_legacy_aug --cap 37

# Apply live operating stack
python scripts/apply_signal_stack_enforced_env.py
python scripts/validate_signal_stack_enforced_env.py
```

---

## Related docs

- `PEAD_CANARY_SLEEVE_DESIGN.md` — non-executable PEAD canary sleeve design draft (no enablement)  
- `PROBABILISTIC_RANKING_RESEARCH_ARCHITECTURE.md` — design for continuous features + probabilistic ranking (no code until approved)  
- `../SIGNAL_QUALITY_ROLLOUT.md` — staged live rollout for the stack  
- `../README.md` — Recommended Rollout Sequence  
- `STRATEGY_PROMOTION_OPERATOR_CHECKLIST.md` — promotion checklist  
- `PARAM_ABLATION_WORKFLOW.md` — ablation machinery  
- Wiki: `wiki/backtest.md`, `wiki/probabilistic-ranking-research-architecture.md`, `wiki/promotion-playbook.md`

---

*Catalog compiled 2026-07-23 (PEAD canary design draft) from `validation_artifacts/`. Re-generate after major multi-era or audit runs.*
