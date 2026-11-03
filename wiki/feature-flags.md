---
source: Brain/Config/Feature Flags.md
created: 2026-04-13
updated: 2026-09-23
tags: [config, features, flags]
---

# Feature Flags

> Boolean toggles and mode switches that enable/disable system capabilities.

## Execution Control

| Env Var | Default | Description |
|---------|---------|-------------|
| `EXECUTION_SHADOW_MODE` | false | Compute but don't submit orders |
| `PAPER_TRADING_ENABLED` | false | Alias for shadow mode |
| `LIVE_TRADING_KILL_SWITCH` | false | Platform-wide trading halt |
| `USER_TRADING_HALTED` | false | Per-user trading pause (SaaS) |

## Data Quality

| Env Var | Default | Description |
|---------|---------|-------------|
| `DATA_QUALITY_EXEC_POLICY` | off | `off`, `warn`, or `block_risk_increasing` |
| `DATA_CROSSCHECK_ENABLED` | false | Compare Schwab vs yfinance quotes |

## SEC Enrichment

| Env Var | Default | Description |
|---------|---------|-------------|
| `SEC_ENRICHMENT_ENABLED` | true | Enable SEC enrichment |
| `SEC_SHADOW_MODE` | true | SEC score hints diagnostics-only |
| `SEC_FILING_ANALYSIS_ENABLED` | true | Enable filing analysis endpoints |

## Advisory Model

| Env Var | Default | Description |
|---------|---------|-------------|
| `ADVISORY_MODEL_ENABLED` | true | Enable advisory scoring |
| `ADVISORY_REQUIRE_MODEL` | false | Fail validation if model missing |

## Hypothesis Ledger

| Env Var | Default | Description |
|---------|---------|-------------|
| `HYPOTHESIS_LEDGER_ENABLED` | false | Enable hypothesis recording |
| `HYPOTHESIS_SELF_STUDY_MERGE` | false | Include in self-study output |
| `HYPOTHESIS_PROMOTION_GUARD_ENABLED` | false | Gate promotions on hit rate |
| `SELF_STUDY_MODE` | off | `off` / `shadow` / `live` conviction floor. Demo seeds and <30 round trips are refused. Legacy `SELF_STUDY_ENABLED=true` maps to live when MODE is unset |

See [[self-study]].

## Forensic & PEAD

| Env Var | Default | Description |
|---------|---------|-------------|
| `FORENSIC_ENABLED` | true | Enable forensic accounting |
| `FORENSIC_FILTER_MODE` | shadow | off/shadow/soft/hard |
| `PEAD_ENABLED` | true | Enable PEAD scoring |

## Probabilistic ranking (research)

| Env Var | Default | Description |
|---------|---------|-------------|
| `PROB_RANK_MODE` | off | `off` / `shadow` / `live` — ML ranker; **KEEP SHADOW** locked 2026-07-18 (do not set live) |
| `PROB_RANK_MODEL_DIR` | (empty) | Path to model dir; else newest under `research_store/models/` |
| `PROB_RANK_TOP_N` | 5 | Keep count when mode=`live` |
| `PROB_RANK_INCLUDE_SHAP` | false | Attach local SHAP contributors (slower) |
| `PROB_RANK_SIZING_MODE` | equal | `equal` or `edge_vol` (edge×confidence/vol) |
| `PROB_RANK_MAX_POSITION` | 0.25 | Max day-book weight per name (edge_vol) |
| `PROB_RANK_MAX_SECTOR` | 0.40 | Max day-book weight per sector |
| `PROB_RANK_KELLY_CAP` | 0.25 | Kelly-style size multiplier cap |

See [[probabilistic-ranking-research-architecture]].

## Analog overlay (research)

| Env Var | Default | Description |
|---------|---------|-------------|
| `ANALOG_OVERLAY_MODE` | shadow | `off` / `shadow` / `live` — winner-neighborhood k-NN; **live is refused** |
| `ANALOG_OVERLAY_ARTIFACT` | (empty) | Optional path; else `research_store/models/analog_library.json` |
| `ANALOG_OVERLAY_K` | 40 | Neighbor count (Euclidean in normalized space) |
| `ANALOG_OVERLAY_MIN_N` | 20 | Below this, shrink toward the era prior and mark `analog_thin` |

See [[analog-overlay]].

## Ranking readouts (research)

| Env Var | Default | Description |
|---------|---------|-------------|
| `CONFIDENCE_RANK_MODE` | shadow | `off` / `shadow` / `live` — survival readout in the Rank cell; **live is refused** |
| `CONFIDENCE_RANK_ARTIFACT` | (empty) | Optional path; else `research_store/models/scan_confidence.json` |
| `EXTENSION_SORT_MODE` | shadow | `off` / `shadow` / `live` — inverted `pts_52w` stamp; **live is refused**; does not change `SCAN_LIVE_SORT_KEY` |
| `PEAD_EXTENSION_DROP_MODE` | shadow | `off` / `shadow` / `live` — PEAD-only mark when `close_vs_sma200_pct` ≥ 0.2028; **live is refused**; Stage 2 and `both` are not stamped; the row stays on the scan |

See [[scan-confidence]].

## Related Pages

- [[scanner-tunables]] — scanner-specific tunables
- [[plugin-modes-config]] — plugin env vars
- [[guardrails]] — kill switches
- [[signal-scanner]] — where flags take effect
- [[self-study]] — conviction-floor ladder
- [[probabilistic-ranking-research-architecture]] — research platform
- [[analog-overlay]] — winner-neighborhood shadow score
- [[scan-confidence]] — survival readout; live ranking is inverted on this book

---

*Last compiled: 2026-09-23*
