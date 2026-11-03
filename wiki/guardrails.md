---
source: Brain/Architecture/Guardrails.md
created: 2026-04-13
updated: 2026-08-20
tags: [architecture, risk, guardrails]
---

# Guardrails

> Risk limits, circuit breaker, and position sizing constraints that wrap execution.

## Position Sizing

- Adaptive stops set per-trade risk based on ATR (see [[adaptive-stops]])
- Regime v2 multipliers scale size by market conditions
- `MAX_SECTOR_ACCOUNT_FRACTION` caps single-sector exposure

## Circuit Breaker

`schwab_circuit` (in `circuit_breaker.py`) is a process-wide latch for **network instability** (DNS / connect / read timeout), not HTTP 401s.

- Trips only after **consecutive** unstable errors (default 3; `SCHWAB_CIRCUIT_FAILURE_THRESHOLD`). A single scan timeout must not latch the process.
- Stays open for `SCHWAB_CIRCUIT_COOLDOWN_SECONDS` (default 300) unless a live Schwab HTTP response or successful OAuth call calls `record_success()`.
- Market-data fetches fast-fail while open so a scan does not thrash per ticker.
- **OAuth token exchange and refresh are not blocked** while the breaker is open — completing auth is the recovery path after a scan timeout.
- Market-data `_request_with_backoff` retries a transient timeout once before counting a failure.

Health check: `GET /api/health/deep` shows circuit breaker status via `quote_health` / operator hint. See [[troubleshooting]] and [[schwab-auth]].

## Kill Switches

| Env Var | Scope | Effect |
|---------|-------|--------|
| `LIVE_TRADING_KILL_SWITCH` | Platform-wide | Halt all BUY orders |
| `LIVE_TRADING_KILL_SWITCH_BLOCKS_EXITS` | Platform-wide | Also block SELL orders |
| `USER_TRADING_HALTED` | Per-user (SaaS) | Pause individual user trading |

## Data Quality Policy

`DATA_QUALITY_EXEC_POLICY`: `off`, `warn`, or `block_risk_increasing`

Checks quote staleness (`DATA_QUOTE_MAX_AGE_SEC`), bar freshness (`DATA_BAR_MAX_STALENESS_DAYS`), and optional cross-source validation (`DATA_CROSSCHECK_ENABLED`).

## Related Pages

- [[execution-engine]] — guardrails wrap execution
- [[adaptive-stops]] — stop sizing within guardrails
- [[feature-flags]] — kill switches and data quality flags
- [[system-overview]] — pipeline context
- [[schwab-auth]] — OAuth must still complete while the breaker is open
- [[troubleshooting]] — circuit-breaker recovery
- [[multi-sleeve-trading-system-constitution]] — target allocator caps, DD staircase, crash mode

---

*Last compiled: 2026-08-20*
