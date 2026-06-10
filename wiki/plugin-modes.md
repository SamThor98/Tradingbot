---
source: Brain/Strategies/Plugin Modes.md
created: 2026-04-13
updated: 2026-06-10
tags: [strategy, plugins, risk]
---

# Plugin Modes

> All new plugins follow OFF → SHADOW → LIVE rollout.

## Mode Definitions

| Mode | Behavior |
|------|----------|
| `off` | Legacy behavior, plugin disabled |
| `shadow` | Compute + diagnostics only, no behavior changes |
| `live` | Enforce gates, resize positions, block/allow actions |

## Active Plugins

1. **Execution Quality** (`EXEC_QUALITY_MODE`) — spread/slippage checks
2. **Exit Manager** (`EXIT_MANAGER_MODE`) — partial TP, breakeven, time stops
3. **Event Risk** (`EVENT_RISK_MODE`) — earnings/macro blackouts
4. **Regime v2** (`REGIME_V2_MODE`) — score-based sizing
5. **Correlation Guard** (`CORRELATION_GUARD_MODE`) — pairwise limits

## Recommended Rollout Sequence

1. `EXEC_QUALITY_MODE` shadow → live
2. `EVENT_RISK_MODE` shadow → live
3. `REGIME_V2_MODE` shadow → live
4. `EXIT_MANAGER_MODE` shadow → live
5. `CORRELATION_GUARD_MODE` after live-testing

**Rule**: Promote one at a time. Hold for at least one full market week.

## Current Status (2026-06-10)

| Plugin | Mode | Where set | Ledger seq |
|--------|------|-----------|------------|
| `EXEC_QUALITY_MODE` | live | config default (promoted 2026-04-18) | 1 |
| `EVENT_RISK_MODE` | live | config default (promoted 2026-04-18) | 2 |
| `REGIME_V2_MODE` | shadow | `.env` override (2026-06-10) | 3 |
| `EXIT_MANAGER_MODE` | shadow | `.env` override (2026-06-10) | 4 |
| `CORRELATION_GUARD_MODE` | shadow | `.env` override (2026-06-10) | 5 |

Steps 3–5 were moved to **shadow only** on 2026-06-10. Live promotion is
blocked by the Phase 2 edge audit verdict `halt_fix_signal_first` (bare-signal
PF mean 1.005, worst-era PF 0.801 across 5 eras; PROCEED requires PF mean
>= 1.20 and worst-era PF >= 1.00 — see
`schwab_skill/validation_artifacts/phase2_edge_audit_aug.md`). Re-run the
audit after base-signal improvements before promoting any of the three.
`POSITION_SIZE_MODE` vol_target/kelly and adaptive guardrails remain
backtest-only (not wired into `execution.py`) for the same reason.

## Related Pages

- [[execution-engine]] — plugins hook into execution flow
- [[canary-rollout]] — canary process
- [[plugin-modes-config]] — full env var reference

---

*Last compiled: 2026-04-13*
