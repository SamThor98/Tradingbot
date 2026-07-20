---
source: Brain/Strategies/Signal Ranking.md
created: 2026-04-13
updated: 2026-07-17
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
| Diagnostic | `rank_score_v2` | Shadow comparison |

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

## Related Pages

- [[signal-scanner]] — ranking at end of Stage B
- [[advisory-model]] — probability overlay
- [[probabilistic-ranking-research-architecture]] — ML ranking research path
- [[scanner-tunables]], [[feature-flags]]

---

*Last compiled: 2026-07-17*
