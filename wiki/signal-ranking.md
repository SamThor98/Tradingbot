---
source: Brain/Strategies/Signal Ranking.md
created: 2026-04-13
updated: 2026-10-08
tags: [strategy, ranking]
---

# Signal Ranking

> How the scanner scores and ranks signals to surface top opportunities.

## Score Stack (v3 — IC-tuned composite)

| Layer | Field | Purpose |
|-------|-------|---------|
| Direct core | volume / signal−52w / mirofish weights | Predictive blend (matches rank v2 evidence) |
| Edge (blend) | `edge_score` | Optional 20% stack blend into composite |
| Safety | reliability / execution | **Caps only** — not blended into rank |
| Quality rank | `composite_score` | Offline challenger |
| Diagnostic | `rank_score_v2` | Shadow comparison; live p76 filter |
| Diagnostic | `historical_rank` | Shadow historical blend (operator Rank display) |
| Diagnostic | analog overlay | Shadow neighborhood PF / p(win); own Analog column |
| Diagnostic | `setup_confidence` | Shadow survival readout inside the Rank cell |

## Top-N Selection

`SIGNAL_TOP_N` (default 5, 0 = unlimited). `SCAN_LIVE_SORT_KEY` defaults to
**`signal_score`** until a challenger demonstrates positive realized-return
ranking evidence.

Offline validation: `scripts/run_scoring_ic_pipeline.py` (build → tune → strict validate) or stepwise via `build_scoring_audit_dataset.py`, `tune_composite_weights.py`, `validate_scoring_metrics.py --strict`.

## Latest Full-Era Evidence

The 2026-07-13 `stage2_only_aug` trade audit covered 16,423 trades across all
five eras. Current score IC remained negative (`signal_score` -0.0268,
`composite_score` -0.0403, `rank_score_v2` -0.0120). A constrained weight
search improved IC versus the baseline in 4/5 eras but still produced negative
absolute IC, so `promote_recommended_defaults=false`. No ranking challenger was
promoted as the live sort key; hard breakout-volume and confluence gates remain
excluded.

Conditioning on the promoted 1% breakout-buffer + exit-grace stack changes the
filter result: a rank-v2 p70 trim raises the offline PF mean from 1.2118 to
1.2312 and worst-era PF from 1.0368 to 1.1431 at roughly 30% retention.
`RANK_FILTER_V2_MODE=shadow` observes this isolated trim in live scans while
`SCAN_LIVE_SORT_KEY` remains `signal_score`. Enforcement and rank-v2 sorting
require separate promotion decisions.

## Scoring Components

| Factor | Source |
|--------|--------|
| Stage 2 proximity to 52W high | [[stage-2-analysis]] (excluded from edge when harmful) |
| VCP volume contraction quality | [[vcp-detection]] |
| Sector relative strength | [[sector-strength]] |
| PEAD earnings surprise | [[pead]] |
| Forensic flags | [[forensic-accounting]] |
| Advisory P(up) | [[advisory-model]] |

## Strategy Attribution

Each signal carries `strategy_attribution.top_live` — the dominant strategy label.

## Strategy Ensemble

When `STRATEGY_ENSEMBLE_MODE` is shadow/live, breakout and pullback strategies are weighted separately per regime via regime router.

## Probabilistic ranker (research)

`PROB_RANK_MODE` attaches LightGBM `prob_rank` scores after Stage B. In
`shadow`, selection is unchanged (rank-v2 control still applies). In `live`,
top `PROB_RANK_TOP_N` by `expected_return_40d` are kept. **Go/no-go locked
2026-07-18: KEEP SHADOW** (not LIVE) — see
[[probabilistic-ranking-research-architecture]].

## Historical blend ranker (shadow)

`HISTORICAL_RANK_MODE` (default `shadow`) attaches `historical_rank`,
`historical_rank_reasons`, and `rank_percentile` after the score stack and
prob-rank attach. `core/scoring_historical.py` scores a non-negative blend of
0–100 normalized features, then reuses the rank-v2 **caps only** (low
reliability, low execution, SEC/Beneish/Altman risk). Feature bounds are the
p1/p99 quantiles fitted from the training panel and stored in the artifact, so
scores keep resolution instead of saturating at a fixed cap.

Fitted with `scripts/fit_historical_rank_blend.py` (walk-forward,
cross-sectionally demeaned NNLS on `ret_40d_fwd`); artifact lives at
`research_store/models/historical_rank_blend.json`. Current model
`hist_blend_83df956d8e` is **trend-only** — `volume_norm` and `prox_52w_norm`
screened out on non-positive OOS IC, and the LightGBM `expected_return_40d`
feature is excluded because it is in-sample on four of five eras.

**Go/no-go 2026-09-03: KEEP SHADOW.** The `historical_top5` arm beats today's
p76 + `signal_score` stack on PF mean (1.160 vs 1.028) and worst-era PF (1.014
vs 0.937), but misses the 1.20 PF mean gate. Live sort/filter unchanged. See
`SIGNAL_QUALITY_ROLLOUT.md` Stage 2h and
`validation_artifacts/historical_rank/counterfactual_control_legacy_aug.json`.

Operator display: the Rank column leads with `trust_score` (0–100, higher is
less extended) when the extension shadow stamp is present. Cohort percentile
over `historical_rank` remains the fallback for older payloads. See
[[scan-confidence]].

## Analog overlay (shadow)

`ANALOG_OVERLAY_MODE` (default `shadow`) attaches a winner-neighborhood score
after historical rank. k-NN is Euclidean on the same entry-time names the
scanner already carries. Live sort/filter stay `signal_score` + rank-v2 p76.
See [[analog-overlay]] and `SIGNAL_QUALITY_ROLLOUT.md` Stage 2i.

## Setup confidence (shadow)

`CONFIDENCE_RANK_MODE` (default `shadow`) attaches `setup_confidence`, a 0–100
survival readout scored in Stage B from the same PIT registry features
prob-rank uses. It asks whether a setup resembles the 21–40 day winners or the
≤20 day stop-outs rather than how strong the setup looks.

The screen behind it found every chart/trend variable **negatively** correlated
with realized return in all five eras, and that a p76 cut on the score returns
PF 1.4296 / worst 1.1212 against the live stack's 1.0276 / 0.9368 — but only
ties inverting `pts_52w` alone. Fundamentals (real PIT Sharadar) and insider
Form 4 data were tested and carry no signal here.

**Go/no-go 2026-09-21: KEEP SHADOW** (`keep_shadow_single_variable_explains_it`).
Live sort/filter unchanged. See [[scan-confidence]] and
`SIGNAL_QUALITY_ROLLOUT.md` Stage 2j.

## Related Pages

- [[signal-scanner]] — ranking at end of Stage B
- [[advisory-model]] — probability overlay
- [[analog-overlay]] — winner-neighborhood shadow score
- [[scan-confidence]] — survival readout and the inverted-ranking evidence
- [[probabilistic-ranking-research-architecture]] — ML ranking research path
- [[pit-research-platform]] — factor analytics that reports the negative-IC fact
- [[scanner-tunables]], [[feature-flags]]
- [[thirty-day-signal-cut-plan]] — top-N stays 5 through 2026-11-07; the capped top-5 cut removes 88 names

---

*Last compiled: 2026-09-21*
