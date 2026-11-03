---
source: Brain/API/SaaS Endpoints.md
created: 2026-04-13
updated: 2026-08-24
tags: [api, saas, endpoints]
---

# SaaS Endpoints

> Routes from `webapp/main_saas.py` — multi-tenant API with Supabase JWT auth. The same dashboard HTML/JS as local is served; `GET /api/public-config` sets `saas_mode=true`.

Included routers: `tenant_dashboard`, `tenant_book`, `tenant_position_intel`, `tenant_manual_portfolio`. See [[tenant-dashboard-endpoints]] for the operator-dashboard surface.

## Intentional differences vs local

| Concern | Local | SaaS |
|---------|-------|------|
| Auth | Optional API key | Supabase JWT |
| Scan / backtest | In-process thread | Celery |
| SSE `/api/events` | On | Off (`sse_enabled=false`) |
| Plugin-mode writes | On | Off (`plugin_mode_writes_enabled=false`) |
| Ablation run | In-process | Rejected (`ablation_writes_enabled=false`) |
| Billing / 2FA / `/api/me` | N/A | Present |

## Top-level routes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/auth/session` | JWT | Current session |
| GET | `/api/me` | JWT | User profile + live-trading flags |
| POST | `/api/credentials/schwab` | JWT | Upload Schwab tokens |
| GET | `/api/scan-catalog` | None | Timeframes, strategy copy, universes |
| GET | `/api/public-config` | None | Client config (`saas_mode`, transports, write gates) |
| GET | `/api/config` | None | API key required + CORS origins |
| POST | `/api/scan` | JWT | Queue async scan (Celery). Body may include `universe_preset`, `scan_timeframe`, `strategy_ids`. |
| GET | `/api/scan/{task_id}` | JWT | Poll Celery scan status |
| GET | `/api/scan-results` | JWT | Latest stored scan rows |
| POST | `/api/settings/enable-live-trading` | JWT | Enable live execution |
| GET | `/api/health/live` | None | Liveness probe |
| GET | `/api/health/ready` | None | Readiness (DB + Redis) |
| GET | `/metrics` | None | Prometheus metrics |

## Related Pages

- [[saas-api]] — architecture overview
- [[tenant-dashboard-endpoints]] — per-tenant dashboard routes (Intel, SEC, book, trades)
- [[local-dashboard-endpoints]] — local equivalent
- [[scan-catalog]] — `/api/scan-catalog` on SaaS too

---

*Last compiled: 2026-08-24*
