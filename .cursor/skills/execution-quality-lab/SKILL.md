---
name: execution-quality-lab
description: >-
  Execution intelligence scaffolding: order lifecycle normalization, expected-vs-
  realized slippage, spread/latency/fill-quality attribution, and policy-driven
  routing (market vs limit, reprice loop, auto-throttle). Use when working on
  execution.py order routing, exec quality, slippage, reprice loops,
  core/execution_policies.py, ExecutionState/blotter, or /api/cockpit/execution/*.
---

# Execution Quality Lab

## Canonical Contract

Execution intelligence is built on one normalized lifecycle object
(`ExecutionState`) and one named, attributable routing decision
(`execution_policies.decide` → `policy_id`). All slippage / spread / latency
attribution flows through `ExecutionQuality` on that DTO.

Pairs with: [`schwab-api`] (order placement), [`degraded-mode-policy`]
(throttle on bad data), [`dashboard-panel-scaffold`] (blotter lane).

## Unified order lifecycle

`ExecutionProvider` reconciles the three status vocabularies into one
`ExecutionStateName` state machine:

| Source | Field | Examples |
|--------|-------|----------|
| App (`PendingTrade`) | `status` | pending → executed/failed/rejected |
| SaaS (`Order`) | `status` | queued → executed/failed |
| Broker (`order_monitor`) | polled | WORKING/FILLED/CANCELED/REJECTED/EXPIRED |

Canonical states: `staged · pending_approval · queued · working · partial ·
filled · cancelled · rejected · expired · failed · unknown`. Terminal set is
`TERMINAL_STATES`.

## Slippage / quality attribution

`ExecutionQuality` fields (lifted from `execution.py`'s `_execution_quality` diag):
`expected_price`, `realized_slippage_bps`, `spread_bps_at_submit`,
`reprice_count`, `latency_ms`. The diag keys are `spread_bps`,
`expected_slippage_bps`, `would_block`, `block_reasons`, `would_prefer_limit`,
`reprice_attempts`, `quote_snapshot{bid,ask,last}`, and (Phase 3) `policy`.

`cockpit_service.build_execution_quality(metrics_summary, blotter)` aggregates:
- lifecycle state counts
- avg/max realized slippage bps, avg spread bps, avg reprice count
- policy events from `execution_safety_metrics.json`
  (`exec_quality_evaluated`, `exec_quality_live_blocked`,
  `exec_quality_shadow_would_block`, `exec_quality_shadow_would_prefer_limit`,
  `exec_policy_evaluated`)

Surfaced at `GET /api/cockpit/execution/quality`.

## Smart execution policies

`core/execution_policies.decide(...)` returns a normalized decision with a
stable `policy_id` (`exec_policy_v1`):
- **market vs limit** — prefer LIMIT when liquid + a usable touch price exists
- **reprice strategy** — `aggressive` when spread ≤ `EXEC_POLICY_TIGHT_SPREAD_BPS`, else `patient`
- **auto-throttle** — `throttle=True` (and `recommend_hold` in live) when
  `data_quality ∈ {degraded, stale, conflict}` on a risk-increasing order

### Rollout (OFF → SHADOW → LIVE) via `EXEC_POLICY_MODE`

- **off**: no decision computed.
- **shadow (default)**: `place_order` computes the decision, records
  `exec_policy_evaluated`, and attaches it to `result["_execution_quality"]["policy"]`
  — **routing is unchanged**.
- **live**: a future change consumes the decision to set order type / hold.

The shadow hook lives in `execution.py` inside the exec-quality block, fully
guarded by try/except so it can never break the order path.

## Adding execution-quality signals (checklist)

1. Compute the new metric inside `execution.py`'s `_execution_quality` diag.
2. Map it onto `ExecutionQuality` in `core/providers/execution_provider.py`.
3. Aggregate it in `cockpit_service.build_execution_quality`.
4. Surface it in the blotter lane (`cockpit.js`).
5. Gate any routing change behind `EXEC_POLICY_MODE` (shadow first).
6. Add a test in `tests/test_cockpit_phase3.py`.

## Key Files

- `schwab_skill/core/execution_policies.py` — `decide()` + `policy_id`
- `schwab_skill/core/providers/execution_provider.py` — lifecycle + quality mapping
- `schwab_skill/core/cockpit_service.py` — `build_execution_quality`, `build_blotter`
- `schwab_skill/execution.py` — `place_order`, exec-quality diag, shadow policy hook
- `schwab_skill/order_monitor.py` — broker poll + fill extraction
- `schwab_skill/execution_persistence.py` — `execution_safety_metrics.json`
- `schwab_skill/webapp/main.py` — `/api/cockpit/execution/quality`
