---
source: Brain/API/Tenant Dashboard Endpoints.md
created: 2026-04-13
updated: 2026-09-21
tags: [api, tenant, endpoints]
---

# Tenant Dashboard Endpoints

> Per-tenant API router from `webapp/tenant_dashboard.py` (plus `routes/tenant_book.py`, `routes/tenant_position_intel.py`, `routes/tenant_manual_portfolio.py`), included in the SaaS API. All require JWT unless noted.

These are the hosted equivalents of the local operator dashboard. Decision-card payloads share `webapp/decision_card_service.py` with local.

## Status & OAuth

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Token presence, last scan, validation |
| GET | `/api/health/deep` | Cached quote-health snapshot |
| GET | `/api/validation/status` | Platform validation artifact |
| GET | `/api/oauth/schwab/authorize-url` | Account OAuth authorize URL |
| GET | `/api/oauth/schwab/market/authorize-url` | Market OAuth authorize URL |
| GET | `/api/oauth/schwab/{account\|market}/callback` | OAuth callbacks |

## Scan, research, Intel

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/scan/status` | Last-scan shape (no in-process worker) |
| GET | `/api/decision-card/{ticker}` | Scan-row decision card (`brief`, rank v2) |
| GET | `/api/check/{ticker}` | Quick technical check |
| GET | `/api/chart/{ticker}` | OHLC |
| GET | `/api/report/{ticker}` | Full report |
| GET | `/api/sec/analyze/{ticker}` | Latest filing analysis |
| GET | `/api/sec/compare` | Filing compare |
| GET | `/api/research/dossier/{ticker}` | Research dossier |
| GET | `/api/research/sharadar/{ticker}` | Sharadar Bundle PIT snapshot (`?refresh=1` bypasses cache) |
| GET | `/api/research/sharadar-compare/{ticker_a}/{ticker_b}` | Ticker vs ticker daily + latest ART (research only) |
| GET | `/api/position-intel` | Intel tab (per-tenant cache) |
| GET | `/api/position-intel/lookup` | Single-ticker Intel lookup |

## Portfolio, book, trades

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Tenant positions |
| GET | `/api/portfolio/risk-dashboard` | Unified risk pack |
| POST | `/api/portfolio/manual/positions` | Price a typed-in book |
| POST | `/api/portfolio/risk-dashboard/manual` | Risk pack for a typed-in book |
| GET | `/api/book/calendar` | P/L calendar |
| GET | `/api/book/tax` | ST/LT realized + tax estimate |
| POST | `/api/trades/{id}/approve` | Approve + execute |
| GET | `/api/trades/{id}/preflight` | Pre-trade checklist |
| GET | `/api/settings/profiles` | Profile catalog |

## Learning

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/decision-dashboard` | System decision snapshot |
| GET | `/api/calibration/summary` | Calibration (AppState) |
| POST | `/api/evolve/run` | Post-mortem tuner |
| POST | `/api/challenger/run` | Challenger cycle |
| GET | `/api/ablation/status` | Idle hosted stub (`hosted_unavailable`) |
| POST | `/api/ablation/run` | Rejected — local operator only |

## Related Pages

- [[saas-endpoints]] — parent API
- [[saas-api]] — architecture
- [[local-dashboard-endpoints]] — local equivalents
- [[schwab-auth]] — OAuth flow
- [[sharadar-bundle]] — Sharadar research snapshot + compare

---

*Last compiled: 2026-09-21*
