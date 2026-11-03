---
source: Brain/Strategies/Quality Gates.md
created: 2026-04-13
updated: 2026-10-16
tags: [strategy, quality]
---

# Quality Gates

> Signal quality filtering that removes weak setups before reaching the final signal list.

## Modes

| Mode | Behavior |
|------|----------|
| `off` | Disabled |
| `shadow` | Track would-filter counts only (default) |
| `soft` | Filter when 2+ weak reasons exist |
| `hard` | Filter on any single weak reason |

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `QUALITY_GATES_MODE` | shadow | Gate mode |
| `QUALITY_SOFT_MIN_REASONS` | 2 | Min weak reasons for soft mode |
| `QUALITY_MIN_SIGNAL_SCORE` | 50 | Minimum score threshold |
| `QUALITY_MIN_CONTINUATION_PROB` | 0.55 | Min continuation probability |
| `QUALITY_MAX_BULL_TRAP_PROB` | 0.45 | Max bull-trap probability |

`weak_breakout_volume` is one soft reason. Soft mode drops it only together with another reason. Hard mode drops it alone. `expansion_volume_ratio` is the entry bar divided by the prior 50-day average. It is stored on the signal and the backtest trade. It is not a gate. The live floor remains `QUALITY_BREAKOUT_VOLUME_MIN_RATIO` 0.90, applied to entry volume over the average that includes the entry bar.

## Related Pages

- [[signal-scanner]] — quality gates applied after Stage B
- [[signal-quality-rollout]] — rollout plan
- [[scanner-tunables]] — env var reference
- [[self-study]] — sibling conviction floor (shadow/live ladder)

---

*Last compiled: 2026-08-26*
