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
| Post-cap stack (pts_52w≤37) | Buffer book PF mean **1.283**, worst **1.058**; **p75 fails** worst-era — **p76** is `re_live_candidate` |
| PF 1.50 peer (Track B) | **`pead_primary_aug_fixed_full_c40`** clears — PF mean **1.550**, worst **1.167**; pullback full **rejects** (worst 0.959) |
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
| Rank-v2 | p75 trim (live after 2026-07-16 promotion) |
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

---

## 11. PF 1.50 dual-track (2026-07-18 → 2026-07-22)

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

#### Stack transfer (exit-grace-only; no entry-timing cache)

| Artifact | Bare PF mean | Exit-grace t15/h40 | Action |
|---|---:|---:|---|
| `peer_generator_stack_transfer_pullback_only_aug.json` (smoke) | 1.492 | 1.455 | `pass_pf_120_…_ready_for_full_universe` (stale vs full) |
| `peer_generator_stack_transfer_pullback_only_aug_full.json` | 1.274\* | 1.260 | Passes 1.20 on **4 eras** (missing `recent_current` chunks on disk) |
| `peer_generator_stack_transfer_pead_primary_aug_fixed_full_c40.json` | **1.550** | **1.549** | **`pass_strict_pf_150_exit_grace`** |

\*Chunk-only baseline omits `recent_current` (0 chunks under `multi_era_chunks/pullback_only_aug_full/recent_current` even though the summary JSON includes that era). Prefer the five-era summary for gate calls.

#### Pullback × pts_52w≤37 offline CF

`pts52w_cap_cf_pullback_only_aug_full_cap37.json`: retention **99.3%**, PF mean lift **+0.003** → `keep_shadow_only` (cap is nearly a no-op on the pullback book).

### Post-cap stack fidelity (control entry cache ∩ pts_52w≤37)

Sources: `signal_stack_counterfactual_control_legacy_aug_pts52w_cap37.json`, `sweep_cf_rank_under_pts52w_cap37_control_legacy_aug.json`.

| Scenario | Trades | Retention† | PF mean | Worst-era | Gates |
|---|---:|---:|---:|---:|---|
| `cap_only_bare` | 8,773 | 53.5% vs uncapped | 1.260 | 1.062 | Pass |
| `exit_grace_t15_h40` | 8,773 | 100% | 1.249 | 1.079 | Pass |
| **`exit_grace_breakout_buffer_0.010`** (selected) | **3,559** | **40.6%** | **1.283** | **1.058** | **Pass** |
| `…_rank_v2_p75` (current live trim) | 890 | 25.0% | 1.299 | **0.955** | **Fail worst-era** |
| `…_rank_v2_p76` (sweep pick) | 859 | 24.1% | **1.323** | **1.034** | **Pass** (`re_live_candidate`) |

†Retention for buffer row is vs capped book; rank rows vs buffer-filtered capped book.

**Operator note:** live rank-v2 p75 is **misaligned** with the post-cap stack — under pts_52w≤37, p75 breaks `late_bull`. Next stack decision is p76 re-live (or drop rank trim and keep buffer-only).

---

## 9. What is still open

1. ~~Bare-signal PF mean ≥ 1.20~~ — cleared via live `pts_52w≤37` (`stage2_pts52w_cap37`).  
2. ~~Stack CF with pts_52w cap~~ — done; buffer book passes; **decide p76 re-live vs buffer-only**.  
3. ~~Track B full-universe bare runs~~ — pullback **reject** (worst 0.959); PEAD **clears 1.50**.  
4. Collect **live-enforced** evidence for Stage 2c–2d stack + pts_52w cap (filter diagnostics / one trading week).  
5. Build `entry_timing_replay_cache_pead_primary_aug_fixed_full_c40` so PEAD can take the 1% buffer (± rank) stack arms (currently exit-grace-only).  
6. Repair `pullback_only_aug_full` `recent_current` chunks if any further pullback CFs are needed (summary has era; chunk dir empty).  
7. Do **not** re-enable hard breakout-volume or confluence Stage A gates without a fresh five-era pass.  
8. **PROB_RANK** stays **KEEP SHADOW** — do not set `PROB_RANK_MODE=live`.  
9. Regime v2 / Correlation Guard LIVE still require operator promotion + shadow evidence.  
10. Track A: keep `EARLY_STOP_GATE_MODE=shadow`.

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
python scripts/analyze_signal_stack_counterfactual.py  # see script --help
python scripts/analyze_peer_generator_stack_transfer.py --run-id pead_primary_aug_fixed_full_c40
python scripts/sweep_rank_v2_under_pts52w_cap.py --run-id control_legacy_aug --cap 37

# Apply live operating stack
python scripts/apply_signal_stack_enforced_env.py
python scripts/validate_signal_stack_enforced_env.py
```

---

## Related docs

- `PROBABILISTIC_RANKING_RESEARCH_ARCHITECTURE.md` — design for continuous features + probabilistic ranking (no code until approved)  
- `../SIGNAL_QUALITY_ROLLOUT.md` — staged live rollout for the stack  
- `../README.md` — Recommended Rollout Sequence  
- `STRATEGY_PROMOTION_OPERATOR_CHECKLIST.md` — promotion checklist  
- `PARAM_ABLATION_WORKFLOW.md` — ablation machinery  
- Wiki: `wiki/backtest.md`, `wiki/probabilistic-ranking-research-architecture.md`, `wiki/promotion-playbook.md`

---

*Catalog compiled 2026-07-22 from `validation_artifacts/`. Re-generate after major multi-era or audit runs.*
