---
name: degraded-mode-policy
description: >-
  Consistent behavior under stale data, endpoint failures, and fallback use:
  data-quality labeling, provenance/confidence, circuit-breaker handling, and
  auto-throttle of risk-increasing orders. Use when working on data_health,
  fallback logic, circuit breakers, fail-closed risk gates, Provenance, or
  auto-throttle in execution policies.
---

# Degraded-Mode Policy

## Canonical Contract

Every read carries a trust label, and every risk-increasing action consults it.
Degraded data must **fail safe** (block/throttle new risk), never fail open.

Pairs with: [`schwab-api`] (fallback chain), [`execution-quality-lab`]
(throttle), [`schwab-endpoint-catalog`] (per-endpoint degraded_mode).

## The single source of trust

`data_health.assess_*` produces a `data_quality` label; `Provenance.from_lineage`
turns lineage dicts into `{source, as_of, confidence, is_stale}`. These two must
never disagree — both read the same keys (`provider`, `used_fallback`,
`fallback_reason`, `data_quality`).

| data_quality | confidence | risk-increasing behavior |
|--------------|-----------|--------------------------|
| `ok` | high | normal |
| `degraded` | medium | throttle (policy hold in live) |
| `stale` | low | throttle / block |
| `conflict` | low | throttle / block |

## Degradation sources & responses

| Source | Detection | Response |
|--------|-----------|----------|
| Schwab DNS/timeout | `schwab_circuit` (5-min unstable) | fast-fail; `place_order` returns circuit error; `circuit_breaker_state` gauge set |
| Rolling error rate >2% | `data_provider` breaker | route history to yfinance until recovery probe |
| Quote stale | age > `DATA_QUOTE_MAX_AGE_SEC` | `quote_fresh=False` pre-trade blocker |
| Bars stale | age > `SCAN_STAGE_A_MAX_BAR_AGE_DAYS` | Stage A rejects candidate |
| Fallback used | `used_fallback` / `fallback_provider` | `data_fallback_total` metric; confidence → medium |
| SPY/regime data missing | `RISK_FAIL_CLOSED_ON_DATA_OUTAGE` (default true) | treat regime bearish → block new entries |

## Auto-throttle (Phase 3)

`execution_policies.decide(..., data_quality=...)` sets `throttle=True` when
`data_quality ∈ {degraded, stale, conflict}` on a risk-increasing order. In
`EXEC_POLICY_MODE=live` this becomes `recommend_hold`. In shadow it is recorded
only. Exits / risk-reducing orders are never throttled.

## Rules

1. **Fail closed for new risk; never block exits.** Cancels and risk-reducing
   orders proceed even when data is degraded.
2. **Label, then decide.** Compute `data_quality`/`Provenance` first; gate on it.
3. **Emit on every degradation** — `data_fallback_total`, `circuit_breaker_state`,
   `data_stale_ratio`, `provider_confidence_total` (see `core/observability.py`).
4. **Render the label.** Panels show provenance so operators see degraded state.
5. **Default to safe modes.** New degraded-mode behaviors roll out OFF → SHADOW → LIVE.

## Key Files

- `schwab_skill/data_health.py` — `data_quality` labeling
- `schwab_skill/core/contracts/provenance.py` — `Provenance.from_lineage`
- `schwab_skill/circuit_breaker.py`, `schwab_skill/data_provider.py` — breakers
- `schwab_skill/core/execution_policies.py` — auto-throttle decision
- `schwab_skill/core/observability.py` — degradation metrics
- `schwab_skill/config.py` — `RISK_FAIL_CLOSED_ON_DATA_OUTAGE`, `DATA_QUOTE_MAX_AGE_SEC`, `EXEC_POLICY_MODE`
