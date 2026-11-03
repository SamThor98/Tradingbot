---
source: Brain/Architecture/WebApp Dashboard.md
created: 2026-04-13
updated: 2026-09-14
tags: [architecture, webapp, dashboard, ux]
---

# WebApp Dashboard

> FastAPI local dashboard for scanning, trade approval, portfolio, and system health.

## Navigation (locked 2026-06-26)

Five top-level tabs in `index.html` / `app.js`:

| Tab | Mode | Focus |
|-----|------|-------|
| Today | `operations` | Scan workflow + pending queue |
| Research | `research` | Portfolio book and one-ticker check (Brief/Deep) |
| Intel | `intel` | Position conviction, volatility, options |
| System | `diagnostics` | Health, validation, calibration, settings |
| Backtest | `backtest` | Leaderboard, one-click 5y/8y queue, next steps, related evidence |

Research sub-tabs are exclusive (Portfolio → Quick check). Cards stack full-width so hidden tab siblings do not leave a vacant grid column. Backtest lives on its own tab (Ctrl+5). Results surface net profit factor against the mean PF ≥ 1.20 promotion bar; a single window is not LIVE-ready. Settings stay merged into System; old `?screen=settings` links still work.

See [[frontend-route-contract]] and [[section-migration-map]] for deep links and section ids.

Display modes: Simple / Standard / Pro (`?display=` or header selector). Simple hides advanced scan columns and Settings risk presets.

## Two UIs

- `app.js` + `index.html` — full-featured dashboard
- `simple.js` + `simple.html` — lightweight scan + diagnostics

## Key Route Groups

| Group | Example | Auth |
|-------|---------|------|
| Health | `GET /api/health/deep` | None |
| Scanning | `GET /api/scan-catalog`, `POST /api/scan`, `GET /api/scan/status` | Catalog is public; scan is optional API key |
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

- [[local-ops-box]] — dedicated host that keeps this UI up on 8182
- [[frontend-route-contract]] — frozen deep-link and screen contract
- [[section-migration-map]] — locked section layout
- [[portfolio-book]] — Book sub-tab (calendar, tax, journal)
- [[local-dashboard-endpoints]] — full endpoint reference
- [[scan-catalog]] — Scan studio timeframes, descriptions, universes
- [[tenant-dashboard-endpoints]] — SaaS per-tenant routes (same dashboard, JWT + Celery)
- [[system-overview]] — architecture context

---

*Last compiled: 2026-09-08*
