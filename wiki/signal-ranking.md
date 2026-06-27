---
source: Brain/Strategies/Signal Ranking.md
created: 2026-04-13
updated: 2026-06-25
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
| **Quality rank** | **`composite_score`** | **Live sort key** |
| Diagnostic | `rank_score_v2` | Shadow comparison |

## Top-N Selection

`SIGNAL_TOP_N` (default 5, 0 = unlimited). Sorted by **`composite_score` descending**.

Offline validation: `scripts/run_scoring_ic_pipeline.py` (build → tune → strict validate) or stepwise via `build_scoring_audit_dataset.py`, `tune_composite_weights.py`, `validate_scoring_metrics.py --strict`.

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

## Related Pages

- [[signal-scanner]] — ranking at end of Stage B
- [[advisory-model]] — probability overlay
- [[scanner-tunables]], [[feature-flags]]

---

*Last compiled: 2026-06-25*
