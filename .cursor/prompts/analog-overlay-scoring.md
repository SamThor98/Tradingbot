# Analog overlay — winner neighborhoods on the scan table

Paste this into **Agent mode** to implement the P0 analog-overlay research path: build a winner/loser library from existing backtest data, score live scan rows by neighborhood similarity, and show that as a **shadow** column on the scan results table. Do **not** change live sort, rank-v2 p76, or plugin modes.

Companion canvas: [Backtest analog overlay](C:\Users\Sam Thoresen\.cursor\projects\d-TradingBot\canvases\backtest-analog-overlay.canvas.tsx). Companion wiki: `wiki/signal-ranking.md`, `wiki/probabilistic-ranking-research-architecture.md`, `schwab_skill/SIGNAL_QUALITY_ROLLOUT.md` Stage 2h.

---

## 1. Goal (one sentence)

Make scoring **more valid** by teaching a shadow analog score from realized trade outcomes, then overlaying “what happened to historically similar setups” onto today’s scan rows — without adding linear weights, hard gates, or a new live sort key.

## 2. Priority and north star

| Item | Value |
|------|--------|
| Priority | **P0** base-signal edge (scoring / ranking / backtest feedback). Scan-table column is P1 display of that P0 score. |
| North star | Bare-signal PF mean ≥ **1.20**, worst-era PF ≥ **1.00**. Analog cannot skip these floors. |
| Live sort | `SCAN_LIVE_SORT_KEY=signal_score` stays. |
| Live filter | `RANK_FILTER_V2_MODE=live` at **p76** stays. |
| Analog / historical rank | **shadow only**. Do not set `HISTORICAL_RANK_MODE=live`, `ANALOG_OVERLAY_MODE=live`, or `SCAN_LIVE_SORT_KEY` to analog / historical_rank. |
| Dashboard | `python scripts/start_local_dashboard.py` → **https://127.0.0.1:8182/** (never port 8000) |
| Verdict language | `keep_shadow` until walk-forward analog top-5 PF mean ≥ 1.20 **and** worst-era ≥ 1.00 **and** it beats `current_live_p76_top5`. |

State at session start: priority P0, files you will touch, validation commands below. Then implement.

## 3. Why this exists (do not re-litigate)

Validity = the number on the row predicts realized P&L **out of sample**. Current live scores fail that:

| Layer | Evidence | Verdict |
|-------|----------|---------|
| `signal_score` (live sort) | 40d IC **−0.0268** on 16,423 `stage2_only_aug` trades | Heuristic; anti-correlated with later returns |
| `composite_score` | IC **−0.0403** | Offline challenger; worse IC |
| `rank_score_v2` | IC **−0.0120**; live p76 filter | Cuts the book; incumbent top-5 PF **1.028** / worst **0.937** |
| `historical_rank` (`hist_blend_83df956d8e`) | Walk-forward NNLS on 66,029 rows vs `ret_40d_fwd` | **Trend-only**. Volume IC −0.0005; 52w prox IC −0.040. Top-5 PF **1.160** / worst **1.014** — miss 1.20 |
| `pts_52w ≤ 37` | Bare PF **1.214** / worst **1.105** | Promoted. 52w proximity is harmful; cap stays |
| Hard vol 1.0–1.2× / confluence AND-gates | Phase 1 sweep | **Rejected** — emptied eras, PF 0.71 → 0.0 |
| LightGBM `expected_return_40d` | In-sample on 4/5 eras | **Excluded** until a fully purged model exists. Documented ~0.04 PF gap |

Linear outcome-fits already extracted the one stable univariate (`close_vs_sma200_pct`). Analog asks a different question: given this **combination** and today’s **regime**, what happened to similar setups? Interactions can exist when univariate IC is ~0. Hard AND-gates assumed every rule was independently necessary and killed N. Score similarity; do not veto.

Phase 2 edge audit: P&L is built by **21–40 day winners** and destroyed by **≤20 day stop-outs**. Label the library that way, not “high `signal_score`.”

## 4. Already done (do not redo or replace)

Inspect and **extend**. Mirror these patterns:

- `core/scoring_historical.py` — shared feature extract + normalize for offline fit and live scan. Analog must use the same discipline (one module, no drift).
- `core/scoring_rank_v2.py` + `apply_rank_v2_risk_caps` — safety is **caps only**, never additive points.
- `core/scoring_composite.py` — `resolve_rank_volume_points`, trend-from-SMA helpers.
- `scripts/fit_historical_rank_blend.py` — walk-forward by era, cross-sectionally demeaned, drop unstable features.
- `scripts/analyze_historical_rank_counterfactual.py` — arms: `baseline`, `current_live_p76`, `current_live_p76_top5`, plus challenger. Reuse `research.counterfactual` and `_load_trade_frame`.
- `signal_scanner.py` `_attach_historical_rank` — shadow annotates; `live` would retarget `sort_score` (blocked). Analog attach must follow this shape.
- `backtest.py` `_score_fields_for_trade` — trade records already persist score components, `close_vs_sma200_pct`, advisory, rank fields.
- `research/registry.py` + `extract_registry_aligned_from_signal` — live/ops name aliases (`close_vs_sma200_pct` → `dist_sma200_pct`).
- `research/leakage.py` — never use `ret_*_fwd`, `net_return`, `y_up_*`, `drawdown_*` as analog inputs.
- `research/report.py` `calibration_deciles` — reuse for analog-score vs realized return.
- `scripts/phase1_trade_diagnostics.py` — era bounds, early-stop vs 21–40d cohorts, SPY-regime counterfactuals (condition neighborhoods; do not resurrect scan-wide SPY blocks).
- Scan table: `webapp/static/panels/scanTable.js`, `modules/signalScores.js`, `static/index.html` headers. Rank tooltip already dumps historical blend / composite / p(up) / caps.
- Artifact: `research_store/models/historical_rank_blend.json`
- Panel: `validation_artifacts/prob_rank_control_confirm/scored_features_control_legacy_aug_labeled.parquet` (66,029 rows) when present
- Trade frame: `control_legacy_aug` via `_load_trade_frame` / multi-era chunks
- Config pattern: `get_historical_rank_mode` / `HISTORICAL_RANK_MODE=off|shadow|live`

Do **not** re-fit historical rank, re-enable hard volume/confluence gates, promote `PROB_RANK_MODE` or `HISTORICAL_RANK_MODE` to live, or invent a second feature extract that diverges from live rows.

## 5. Deliverable — implement all of A–F

### A. Shared analog module (required)

Add `schwab_skill/core/scoring_analog.py` (`from __future__ import annotations`).

Responsibilities:

1. **Feature vector** (entry-time only; same names as live signal rows). Start small — curse of dimensionality is the failure mode:

   | Keep | Why |
   |------|-----|
   | `close_vs_sma200_pct` | Only positive OOS IC feature |
   | `pct_from_52w_high` / `pts_52w` | Harmful univariate; still a loser attractor |
   | `volume_ratio` and/or `avg_vcp_volume_ratio` / volume points | Univariate IC ~0; may interact with trend |
   | `pts_mirofish` | On scan + trade telemetry |
   | `p_up_calibrated` / advisory `p_up_10d` | On both sides when present |
   | Regime keys: `era` and/or SPY SMA state, `sector_rel_21d` if coverage is real | **Condition** the neighborhood; do not put 20 sparse columns in the distance |

   **Forbidden inputs:** `ret_*_fwd`, `net_return`, `hold_days`, `exit_reason`, `expected_return_40d` / unpurged `ml_expected_return_norm`, ticker identity (NVDA matching NVDA), post-entry fields.

2. `raw_features_from_signal_row(signal_row)` — reuse helpers from `scoring_historical` / `scoring_composite` where they already exist. Reconstruct `score_components` from flat trade-frame columns the same way `analyze_historical_rank_counterfactual.py` does.

3. Normalize with **fitted** p1/p99 bounds stored in the analog artifact (not `TREND_PCT_CAP` 0.20 — that saturates half the book).

4. `score_analog(signal_row, library, *, k, min_n, regime) -> dict | None`:
   - Restrict library to the matching regime slice (era and/or SPY state; fall back to all-eras if slice `< min_n`).
   - k-NN in normalized space (Euclidean or cosine; pick one, document it, test it).
   - `analog_p_win` = winner density / (winner + loser density) among the k neighbors (or kernel density if you prefer; keep it deterministic).
   - `analog_neighborhood_pf` from neighbor `net_return` (or `ret_40d_fwd` on the labeled panel). Use the same `_safe_pf` convention as research counterfactuals.
   - `analog_n` = neighbor count after filters.
   - `analog_regime` = slice used.
   - `analog_reasons` = short human strings for the tooltip (trend band, 52w band, “thin neighborhood”).
   - `analog_neighbors` = up to 3 `{ticker, entry_date, net_return, hold_days, exit_reason}` for display only (neighbors may include ticker; the **distance must not**).
   - Return `None` (not 0) when unscored, so thin/missing ≠ weak.

5. Shrinkage: if `analog_n < min_n` (default 20), shrink `analog_p_win` and neighborhood PF toward the **era prior** (era-level win rate / PF). Surface `analog_thin=true`. Do not color a thin neighborhood as a strong win.

6. Winner / loser labels on library rows:
   - Primary: top vs bottom quintile of `net_return` (trades) or `ret_40d_fwd` (panel).
   - Diagnostic tags: `early_stopout` (hold ≤ 20d and loss) vs `mid_winner` (hold 21–40d and win). Phase 2 cohorts.

Risk caps: after the analog score exists, you may apply `apply_rank_v2_risk_caps` to a 0–100 display score. Caps never add points.

### B. Library builder (required)

Add `schwab_skill/scripts/build_analog_library.py`.

- Default trade run-id: `control_legacy_aug` via existing `_load_trade_frame` / chunk readers. Also accept the labeled parquet when present (`scored_features_control_legacy_aug_labeled.parquet`).
- Write `research_store/models/analog_library_<short_id>.json` (or parquet + a small JSON sidecar with bounds, k, min_n, feature list, `model_id`, generated_at, source paths, row counts). Prefer a size that the scanner can load quickly; if the full frame is huge, store normalized features + labels + regime + neighbor display fields only.
- Do **not** run a new multi-era backtest. Re-analyze existing artifacts.
- Idempotent: same inputs → same `model_id` (hash features + bounds + label rule + sources).
- Log row counts per era and winner/loser split. Fail closed if an era has too few labeled rows to be useful (document the floor; do not silently mix eras to hide it).

### C. Walk-forward counterfactual (required)

Add `schwab_skill/scripts/analyze_analog_overlay_counterfactual.py` modeled on `analyze_historical_rank_counterfactual.py`.

For each held-out era: build the library from the **other** eras only, score the holdout trades through `core.scoring_analog`, then report:

| Arm | Meaning |
|-----|---------|
| `baseline` | All trades |
| `current_live_p76` | Rank-v2 p76 |
| `current_live_p76_top5` | Incumbent: p76 then top-5/day by `signal_score` |
| `historical_top5` | Existing shadow bar (PF 1.160 / worst 1.014 on the 2026-09-03 artifact) |
| `analog_top5` | Top-5/day by analog score (or analog PF), **no new hard filter** |
| `analog_p76` | Optional diagnostic percentile cut — **not** a promotion candidate unless it also keeps retention honest |

Metrics per arm: n, retention, PF mean, worst-era PF, plus analog **IC** (cross-sectional Spearman vs `net_return` / `ret_40d_fwd`) and calibration deciles (mean analog score vs mean realized return / PF).

Write `validation_artifacts/analog_overlay/counterfactual_<run_id>.json` (+ a short `.md` summary).

Verdict rules (print them, do not promote):

- If `analog_top5` PF mean < `historical_top5` (1.160 on the frozen artifact — **recompute**, do not hardcode if the frame changes): analog is **education-only**. Shadow column may still ship.
- If analog beats historical_top5 but PF mean < 1.20 or worst-era < 1.00: `keep_shadow`.
- LIVE sort/filter remains blocked regardless of a pretty UI.

Print the same “HISTORICAL_RANK_MODE stays shadow” style line for `ANALOG_OVERLAY_MODE`.

### D. Scanner attach — shadow only (required)

Mirror `_attach_historical_rank`:

- Config: `ANALOG_OVERLAY_MODE=off|shadow|live` (default **`shadow`** once the artifact exists, else behave as `off`). `ANALOG_OVERLAY_ARTIFACT=` optional path. `ANALOG_OVERLAY_K`, `ANALOG_OVERLAY_MIN_N` tunables via `config.py` getters (process env overrides `.env`).
- **Never implement `live` behavior** in this wave beyond a stub that refuses (`diagnostics["analog_overlay_skipped"]="live_not_approved"`). `live` must not change `sort_score` or drop rows.
- `shadow`: attach `analog_p_win`, `analog_neighborhood_pf`, `analog_n`, `analog_thin`, `analog_regime`, `analog_reasons`, `analog_neighbors` on each scored signal.
- Diagnostics: `analog_overlay_mode`, `analog_overlay_model_id`, `analog_overlay_scored`, `analog_overlay_unscored`, `analog_overlay_thin`.
- Attach **after** the score stack / historical rank / prob-rank attach, same neighborhood as historical rank. Do not change Stage A filters.
- Fail closed on missing artifact: skip + diagnostic, do not crash the scan.

### E. Scan table overlay (required, display only)

Today’s columns: Ticker, Gate, Strategy, Vol, Price, Rank, Stop, Actions.

Add one **Analog** column (qualified + near-miss). Do **not** cram analog into the Rank tooltip as a fifth competing score.

- Cell: `1.41 PF · n=47` (or `thin` when `analog_thin`). Optional short regime tag (`bear-like`).
- Tooltip / inline (honor existing rank-explain tooltip-vs-inline pattern if cheap; otherwise tooltip-only is fine): neighborhood PF, n, shrinkage, 2–3 analog neighbors, 1–2 `analog_reasons`.
- Calibration one-liner when you have deciles: “this analog decile historically realized PF X.”
- Accessors in `webapp/static/modules/signalScores.js` (`getAnalogNeighborhoodPf`, `getAnalogN`, …).
- Sortable `data-sort-key` for analog PF is OK as **operator sort**, not backend `sort_score`.
- Mirror any SaaS scan-row serialization that strips unknown keys so the column is not empty on `/api/scan-results`.
- Follow `wiki/frontend-route-contract` and `webapp-frontend.mdc`. No new visual system. No gradients/emoji chrome.

Do **not** change Brief/execution eligibility based on analog.

### F. Tests, rollout note, wiki (required)

- Unit tests: feature extract parity (flat trade row vs nested `score_components`), leakage (forward returns rejected), `None` vs 0 when unscored, shrinkage when n < min_n, ticker identity not in distance, walk-forward does not score holdout with holdout rows in the library, scanner attach does not mutate `sort_score` in shadow.
- JS tests if the repo already tests scan table cells (`tests/js/`).
- Append **Stage 2i: Analog overlay (KEEP SHADOW)** to `SIGNAL_QUALITY_ROLLOUT.md` with env, commands, and the same go/no-go as §5.C.
- Update `wiki/signal-ranking.md` + `wiki/index.md` if a new page is cleaner (`wiki/analog-overlay.md`, atomic, frontmatter, `[[wikilinks]]` both ways). Do not edit `/raw`.
- Keep the companion canvas in sync if numbers change.

## 6. Explicitly out of scope

- New multi-era backtest runs
- Re-fitting `historical_rank` or promoting it
- `PROB_RANK_MODE=live` or unpurged LightGBM as an analog feature (purged retrain is a **parallel** track, not this prompt)
- Hard Stage A gates (breakout volume 1.0–1.2×, confluence both/either)
- `QUALITY_GATES` hard enforce, `REGIME_V2` / `CORRELATION_GUARD` LIVE
- Changing `SCAN_LIVE_SORT_KEY` or rank-v2 percentile
- Live orders
- SaaS-only polish (`main_saas.py`) unless required to keep scan-row fields from being stripped
- Port 8000 dashboard verification

## 7. Implementation rules

- Smallest coherent diff. Match `project-conventions.mdc`: type hints, `LOG.info("… %s", var)` (no f-strings in logs), no bare `except:`.
- All tunables through `config.py` / `.env`; process env wins.
- `ApiResponse(ok, data, error)` if you add a route (you should not need a new route if the scan payload already carries the fields).
- Diagnostics `dict` travels with signals.
- Never fabricate prices, trades, or PF numbers. If the parquet or chunks are missing, say so and work from whatever `_load_trade_frame` can load; do not invent a demo library of fake winners.
- Surface `data_quality` and guardrail errors verbatim if a scan is used for UI verify.

## 8. Validation (from `schwab_skill/`)

1. `python -m ruff check core/scoring_analog.py scripts/build_analog_library.py scripts/analyze_analog_overlay_counterfactual.py`
2. `python -m pytest -q` on new tests + historical-rank / scan-table / scoring tests you touched
3. `python scripts/build_analog_library.py`
4. `python scripts/analyze_analog_overlay_counterfactual.py`
5. If you touch the scanner or scan table: `python scripts/validate_all.py --profile local --strict --skip-backtest`
6. UI: **https://127.0.0.1:8182/** — Analog column on qualified + near-miss, tooltip, sort key unchanged in the payload (`sort_score` / live rank). If the dashboard is not running, say so; do not start it on 8000.

## 9. Session summary (required when you finish)

- Files changed
- Library row counts per era / winner-loser split
- Counterfactual table: baseline vs live p76 top5 vs historical_top5 vs analog_top5 (PF mean, worst-era, retention, IC)
- Verdict: `education_only` | `keep_shadow` | (do not claim promote)
- Command outcomes
- Risks (thin eras, missing parquet, leakage)
- Next step (purged prob-rank retrain is the named parallel path for the remaining ~0.04 PF)

---

Paste ends here. Implement A–F in one session if possible; if blocked on missing trade artifacts, finish A + tests + attach/UI against a fixture library and leave C as a script that fails closed with a clear message.
