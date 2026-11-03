---
source: Brain/Config/Feature Flags.md
created: 2026-04-13
updated: 2026-11-03
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

## Decision packets

| Env Var | Default | Description |
|---------|---------|-------------|
| `DECISION_PACKET_SCAN_RECORDING_ENABLED` | true | Record one staged packet per kept scan signal per UTC day |
| `DECISION_PACKET_HORIZON_DAYS` | 10 | Trading days until a pending packet is labeled win/loss/scratch |

## Today tab agent

| Env Var | Default | Description |
|---------|---------|-------------|
| `TAB_AGENT_TODAY_ENABLED` | false | Serve Today persona prompts from the version registry |
| `TAB_AGENT_SHADOW_ENABLED` | false | Run a shadow prompt beside live without changing conviction |
| `TAB_AGENT_MIN_SAMPLES` | 30 | Minimum labeled outcomes before a prompt comparison is readable |
| `TAB_AGENT_DEBATE_ENABLED` | false | Log debate-committee calls after scans. Does not place orders |
| `TAB_AGENT_DEBATE_SHADOW_ENABLED` | false | Extra shadow committee call. Off means zero extra LLM calls |
| `TAB_AGENT_DEBATE_MAX_NAMES` | 5 | Kept names reviewed per scan |

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

## Related Pages

- [[scanner-tunables]] — scanner-specific tunables
- [[plugin-modes-config]] — plugin env vars
- [[guardrails]] — kill switches
- [[signal-scanner]] — where flags take effect
- [[decision-packet-outcomes]] — packet recording and outcome horizon
- [[today-tab-agent]] — opt-in prompt agent for the Today tab
- [[debate-tab-agent]] — opt-in debate committee agent
- [[probabilistic-ranking-research-architecture]] — research platform

---

*Last compiled: 2026-07-17*
