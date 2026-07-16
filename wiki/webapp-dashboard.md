---
source: Brain/Architecture/WebApp Dashboard.md
created: 2026-04-13
updated: 2026-07-16
tags: [architecture, webapp, dashboard, ux]
---

# WebApp Dashboard

> FastAPI local dashboard for scanning, trade approval, portfolio, and system health.

## Navigation (locked 2026-06-26)

Four top-level tabs in `index.html` / `app.js`:

| Tab | Mode | Focus |
|-----|------|-------|
| Today | `operations` | Scan workflow + pending queue |
| Research | `research` | Quick check, backtest, diligence, portfolio (Positions / Risk / Book) |
| System | `diagnostics` | Health, validation, calibration |
| Settings | `settings` | Schwab connect, live-order controls, risk presets |

See [[frontend-route-contract]] and [[section-migration-map]] for deep links and section ids.

Display modes: Simple / Standard / Pro (`?display=` or header selector). Simple hides advanced scan columns and Settings risk presets.

## Two UIs

- `app.js` + `index.html` — full-featured dashboard
- `simple.js` + `simple.html` — lightweight scan + diagnostics

## Key Route Groups

| Group | Example | Auth |
|-------|---------|------|
| Health | `GET /api/health/deep` | None |
| Scanning | `POST /api/scan`, `GET /api/scan/status` | Optional API key |
| Research | `GET /api/check/{ticker}`, `GET /api/report/{ticker}` | None |
| SEC | `GET /api/sec/analyze/{ticker}`, `GET /api/sec/compare` | None |
| Portfolio | `GET /api/portfolio`, `GET /api/sectors` | None |
| Trades | `POST /api/trades/{id}/approve` | Required API key |
| Settings | `POST /api/settings/profile` | Optional API key |

## Response Pattern

All API routes return `ApiResponse(ok, data, error)` via `_ok()` / `_err()` helpers.

## Middleware

- CORS with `build_allowed_origins()`
- Request metrics and timing (`X-Response-Time` header)
- `Cache-Control: no-store` on `/api/` routes

## Related Pages

- [[frontend-route-contract]] — frozen deep-link and screen contract
- [[section-migration-map]] — locked section layout
- [[portfolio-book]] — Book sub-tab (calendar, tax, journal)
- [[local-dashboard-endpoints]] — full endpoint reference
- [[tenant-dashboard-endpoints]] — SaaS per-tenant routes
- [[system-overview]] — architecture context

---

*Last compiled: 2026-07-16*
