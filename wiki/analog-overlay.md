---
source: .cursor/prompts/analog-overlay-scoring.md
created: 2026-09-21
updated: 2026-09-21
tags: [strategy, ranking, analog, shadow]
---

# Analog Overlay

> Shadow winner-neighborhood score: what happened to historically similar setups.

## Why

Linear outcome-fits already extracted the one stable univariate
(`close_vs_sma200_pct`). Analog asks a different question: given this
**combination** and today's **regime**, what happened to similar setups?
Interactions can exist when univariate IC is ~0. Score similarity; do not veto.

Distance is **Euclidean** on fitted p1/p99 normalized 0–100 features in
`core/scoring_analog.py`. Ticker identity is never a distance coordinate.
Forward returns, hold days, and exit reasons are labels / display only.

## Feature vector (entry-time only)

| Keep | Role |
|------|------|
| `close_vs_sma200_pct` | Only positive OOS IC feature |
| `pct_from_52w_high` / `pts_52w` | Harmful univariate; loser attractor |
| `volume_points` | Univariate IC ~0; may interact with trend |
| `pts_mirofish` | On scan + trade telemetry |
| `p_up_calibrated` | On both sides when present |
| `era` / SPY SMA state | **Condition** the neighborhood; not a distance column |

Forbidden inputs: `ret_*_fwd`, `net_return`, `hold_days`, `exit_reason`,
`expected_return_40d` / unpurged `ml_expected_return_norm`, ticker identity.

## Rollout

`ANALOG_OVERLAY_MODE` (default `shadow`) attaches `analog_p_win`,
`analog_neighborhood_pf`, `analog_n`, `analog_thin`, `analog_regime`,
`analog_reasons`, `analog_neighbors` after the score stack and
[[signal-ranking]] historical-rank attach. Missing artifact → skip + diagnostic
(fail closed, no crash). `live` is refused (`analog_overlay_skipped=live_not_approved`)
and must not change `sort_score`.

Thin neighborhoods (`analog_n` < `ANALOG_OVERLAY_MIN_N`, default 20) shrink
`analog_p_win` and neighborhood PF toward the era prior and set `analog_thin`.

Library builder: `scripts/build_analog_library.py` (default run-id
`control_legacy_aug`). Artifact: `research_store/models/analog_library.json`.
Era labeled-row floor: 40 winner+loser rows; thin eras fail closed rather than
silently mixing.

Walk-forward: `scripts/analyze_analog_overlay_counterfactual.py` builds the
library from **other** eras only, then scores the holdout through
`core.scoring_analog`.

## Verdict language

- `analog_top5` PF mean < recomputed `historical_top5` → `education_only`
  (shadow column may still ship)
- Analog beats historical but PF mean < 1.20 or worst-era < 1.00 → `keep_shadow`
- LIVE sort/filter remains blocked regardless of UI

See `SIGNAL_QUALITY_ROLLOUT.md` Stage 2i.

## Walk-forward (2026-09-21)

`analog_927a3f9f68` on 16,433 `control_legacy_aug` trades. Leave-one-era-out
library. `analog_top5` PF mean **1.162** / worst-era **1.007** vs
`historical_top5` 1.160 / 1.014 and live p76+top5 1.028 / 0.937.

**Verdict: `keep_shadow`.** Beats the live stack; misses the 1.20 PF mean gate.
Analog IC vs `net_return` is −0.0024 (near zero, not the −0.027 of `signal_score`).

## Operator display

Scan table Analog column (qualified + near-miss): `1.41 PF · n=47` or `thin`.
Operator `data-sort-key=analog_neighborhood_pf` only — not backend `sort_score`.
Does not change Brief / execution eligibility.

## Related Pages

- [[signal-ranking]] — live sort, rank-v2 p76, historical blend
- [[scan-confidence]] — sibling shadow survival readout on the Rank cell
- [[probabilistic-ranking-research-architecture]] — purged ML is a parallel track
- [[feature-flags]] — `ANALOG_OVERLAY_*` env
- [[frontend-route-contract]] — scan table lives on Today / `#scanSection`

---

*Last compiled: 2026-09-21*
