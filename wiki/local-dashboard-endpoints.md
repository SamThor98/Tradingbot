---
source: Brain/API/Local Dashboard Endpoints.md
created: 2026-04-13
updated: 2026-07-16
tags: [api, local, endpoints]
---

# Local Dashboard Endpoints

> All routes from `webapp/main.py` — single-user FastAPI app.

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health/deep` | Full health: DB, tokens, quotes |
| POST | `/api/scan` | Trigger scan |
| GET | `/api/scan/status` | Current/last scan status + signals |
| GET | `/api/check/{ticker}` | Quick technical check |
| GET | `/api/report/{ticker}` | Full multi-section report |
| GET | `/api/portfolio` | Account positions with P&L |
| GET | `/api/portfolio/risk-dashboard` | Unified risk pack: metrics, correlation, stress (Schwab account) |
| POST | `/api/portfolio/manual/positions` | Price a manually entered ticker/qty book (public, no API key) |
| POST | `/api/portfolio/risk-dashboard/manual` | Risk pack for a manual book (public; 15-name cap, 1 build/min/IP, 5-min cache) |
| GET | `/api/book/calendar` | P/L calendar (realized + MTM) for year/month |
| GET | `/api/book/tax` | ST/LT realized totals + optional tax estimate |
| POST | `/api/book/tax/prefs` | Save federal/state tax rates (API key if configured) |
| POST | `/api/book/snapshot` | Manual EOD position snapshot for MTM |
| GET | `/api/book/journal` | Journal ticker list (open + noted) |
| GET | `/api/book/journal/{symbol}` | Thesis + note timeline for a symbol |
| POST | `/api/book/journal/{symbol}/thesis` | Upsert thesis/plan text |
| POST | `/api/book/journal/notes` | Add quick note or full review |
| GET | `/api/sectors` | Sector heatmap |
| POST | `/api/trades/{id}/approve` | Approve + execute (requires API key) |
| POST | `/api/trades/{id}/reject` | Reject trade (requires API key) |
| GET | `/api/trades/{id}/preflight` | Pre-trade checklist |
| GET | `/api/sec/analyze/{ticker}` | SEC filing analysis |
| GET | `/api/performance` | Backtest/shadow/live metrics |

## Related Pages

- [[webapp-dashboard]] — architecture overview
- [[saas-endpoints]] — SaaS equivalent
- [[tenant-dashboard-endpoints]] — per-tenant routes

---

*Last compiled: 2026-04-13*
