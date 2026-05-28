---
name: schwab-endpoint-catalog
description: >-
  Canonical Schwab endpoint map for the Trading Cockpit: which API surfaces
  exist, how they are classified (market context / symbol intel / portfolio
  risk / execution / reliability), their session, latency budgets, fallback
  rules, and degraded-mode behavior. Use when adding a Schwab endpoint, wiring
  a provider, defining a cockpit DTO, planning capability gaps, or touching
  core/endpoint_catalog.py, core/contracts/, or core/providers/.
---

# Schwab Endpoint Catalog

## Canonical Contract

This is the **canonical capability map** for TradingBot's Schwab integration.
The machine-readable source of truth is `schwab_skill/core/endpoint_catalog.py`
(`EndpointSpec` registry). This document and that module must stay consistent;
if they diverge, the module wins and this doc should be updated.

Pairs with: [`schwab-api`] (auth/token/runtime patterns), [`signal-scanner`]
(decision logic), [`front-end-design`] (cockpit UX).

## Classification engines

| Engine | Owns | Cockpit lane |
|--------|------|--------------|
| `market_context` | regime, breadth, movers, volatility, price history | Market Regime |
| `symbol_intel` | quotes, options chains, fundamentals/search | Ranked Opportunities |
| `portfolio_risk` | accounts, positions, transactions | Portfolio Risk + Exposure |
| `execution` | order place/get/replace/cancel/preview | Execution Blotter |
| `reliability` | OAuth / token health | Data Reliability (cross-cutting) |

## Status legend

- **live** — called in the codebase today.
- **gap** — planned; not yet integrated. `phase` field says when (P1/P2/P3).

## Endpoint map (base: `https://api.schwabapi.com`)

| Key | Engine | Method | Path | Session | Status | Phase |
|-----|--------|--------|------|---------|--------|-------|
| `oauth.token` | reliability | POST | `/v1/oauth/token` | both | live | live |
| `marketdata.pricehistory.daily` | market_context | GET | `/marketdata/v1/pricehistory` | market | live | live |
| `marketdata.pricehistory.minute` | market_context | GET | `/marketdata/v1/pricehistory` | market | gap | P2 |
| `marketdata.movers` | market_context | GET | `/marketdata/v1/movers/{index}` | market | gap | P2 |
| `marketdata.markets.hours` | market_context | GET | `/marketdata/v1/markets` | market | gap | P2 |
| `marketdata.quotes` | symbol_intel | GET | `/marketdata/v1/quotes` | market | live | live |
| `marketdata.options.chains` | symbol_intel | GET | `/marketdata/v1/chains` | market | gap | P2 |
| `marketdata.instruments` | symbol_intel | GET | `/marketdata/v1/instruments` | market | gap | P2 |
| `trader.accounts` | portfolio_risk | GET | `/trader/v1/accounts` | account | live | live |
| `trader.accounts.transactions` | portfolio_risk | GET | `/trader/v1/accounts/{hash}/transactions` | account | gap | P3 |
| `trader.orders.place` | execution | POST | `/trader/v1/accounts/{hash}/orders` | account | live | live |
| `trader.orders.get` | execution | GET | `/trader/v1/accounts/{hash}/orders/{orderId}` | account | live | live |
| `trader.orders.replace` | execution | PUT | `/trader/v1/accounts/{hash}/orders/{orderId}` | account | live | live |
| `trader.orders.cancel` | execution | DELETE | `/trader/v1/accounts/{hash}/orders/{orderId}` | account | live | live |
| `trader.orders.preview` | execution | POST | `/trader/v1/accounts/{hash}/previewOrder` | account | gap | P1 |

Query the registry programmatically:

```python
from core import endpoint_catalog as cat

cat.all_endpoints()        # tuple[EndpointSpec, ...]
cat.by_engine("execution") # list[EndpointSpec]
cat.by_status("gap")       # planned surfaces
cat.coverage_summary()     # {engine: {"live": n, "gap": m}}
cat.get_endpoint("marketdata.quotes")
```

## Rate / latency guardrails

Each `EndpointSpec` carries `p95_latency_budget_ms`. The observability layer
(`core/observability.py`) emits `schwab_request_latency_ms` per endpoint/session
so budgets are enforceable in release gates. Concurrency is bounded by
`SCAN_STAGE_A_MAX_WORKERS` / `SCAN_STAGE_B_MAX_WORKERS` (default 4) to avoid 429s;
`schwab_circuit` (5-min unstable window) fast-fails when DNS/reads degrade.

## Fallback + degraded-mode rules (summary)

- **pricehistory.daily** → yfinance unless `SCHWAB_ONLY_DATA`; stale bars rejected in Stage A.
- **quotes** → Polygon last-trade/prev-close, yfinance for guardrail pricing; stale past `DATA_QUOTE_MAX_AGE_SEC`.
- **accounts / orders** → **no third-party fallback** (account-mutating); `EXECUTION_SHADOW_MODE` simulates; `RISK_FAIL_CLOSED_ON_DATA_OUTAGE` blocks new entries on outage.
- Every fallback emits `data_fallback_total{provider,reason}`; confidence labeling flows through `Provenance.from_lineage`.

## Adding a new endpoint (checklist)

1. Add an `EndpointSpec` row to `core/endpoint_catalog.py` (set `status`, `phase`, budgets, fallback, degraded_mode).
2. Add the call inside the relevant provider in `core/providers/` (never call Schwab from a route or panel directly).
3. Normalize the response into the matching contract in `core/contracts/` — add fields, never break existing ones.
4. Emit observability via `core.observability` (latency is automatic through `market_data._request_with_backoff`; add `observe_lineage` on fallbacks).
5. Ship behind a mode flag (OFF → SHADOW → LIVE); default `off`.
6. Update this table + add/extend a test in `tests/test_cockpit_contracts.py`.

## Key Files

- `schwab_skill/core/endpoint_catalog.py` — `EndpointSpec` registry (source of truth)
- `schwab_skill/core/contracts/` — canonical DTOs (`MarketSnapshot`, `SymbolDecisionCard`, `ExecutionState`, `PortfolioRiskState`, `Provenance`)
- `schwab_skill/core/providers/` — raw vendor JSON → DTO normalization layer
- `schwab_skill/core/observability.py` — frozen metric schema (latency, fallback, stale ratio, confidence)
- `schwab_skill/market_data.py` — lowest data layer (quotes/history + fallback + retry), now instrumented
- `schwab_skill/config.py` — `get_cockpit_providers_mode`, `get_observability_metrics_enabled`
