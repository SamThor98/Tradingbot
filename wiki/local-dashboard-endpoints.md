---
source: Brain/API/Local Dashboard Endpoints.md
created: 2026-04-13
updated: 2026-10-15
tags: [api, local, endpoints]
---

# Local Dashboard Endpoints

> All routes from `webapp/main.py` plus included routers — single-user FastAPI app served at **https://127.0.0.1:8182/** (`python scripts/start_local_dashboard.py`). Port 8000 is not the operator dashboard.

Auth: optional `WEB_API_KEY` (`X-API-Key`) on mutating routes. Read-only GETs are typically open on loopback.

## Health & config

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Basic health |
| GET | `/api/health/deep` | DB, tokens, quote probe |
| GET | `/api/public-config` | Non-secret client config (`saas_mode=false`, SSE on, plugin/ablation writes on) |
| GET | `/api/config` | API key required + CORS origins |
| GET | `/api/status` | Token presence, last scan, validation |
| GET | `/api/validation/status` | Continuous validation artifact |
| GET | `/api/runtime-contract` | Runtime mode / scan transport |
| GET | `/api/events` | SSE live updates (local only) |

## Scanning & studio

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/scan-catalog` | Timeframes, strategy copy, universes |
| POST | `/api/scan` | In-process scan (`universe_preset`, `scan_timeframe`, `strategy_ids`) |
| GET | `/api/scan/status` | Current/last scan + signals |
| GET | `/api/scan-lifecycle` | Running-job watchdog snapshot |

## Research & Intel

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/check/{ticker}` | Quick technical check |
| GET | `/api/chart/{ticker}` | OHLC for sparkline/chart |
| GET | `/api/report/{ticker}` | Full multi-section report |
| GET | `/api/decision-card/{ticker}` | Scan-row decision card (`brief`, rank v2) |
| GET | `/api/sec/analyze/{ticker}` | Latest filing analysis |
| GET | `/api/sec/compare` | Filing compare |
| GET | `/api/research/dossier/{ticker}` | Research dossier |
| GET | `/api/position-intel` | Intel tab tables (held positions, ~5 min cache) |
| GET | `/api/position-intel/lookup` | Single-ticker Intel lookup |
| GET | `/api/event-probability/catalog` | Configured event → company exposure maps |
| GET | `/api/event-probability/{ticker}` | Event risk panel payload (`?as_of=` for point-in-time) |
| POST | `/api/event-probability/ingest` | Fetch configured markets, append history |
| GET | `/api/research/pricing/{ticker}` | SEC companyfacts DCF + reverse DCF (`?refresh=1` force ingest) |
| POST | `/api/research/pricing/{ticker}/ingest` | Fetch the XBRL companyfacts document once |
| POST | `/api/research/pricing/{ticker}/recompute` | Local growth/WACC/terminal overlay (no network) |
| GET | `/api/research/sharadar/{ticker}` | Sharadar Bundle PIT snapshot (`?refresh=1` bypasses cache) |
| GET | `/api/research/sharadar-compare/{ticker_a}/{ticker_b}` | Ticker vs ticker daily + latest ART (research only) |

Event-probability, pricing, and Sharadar routes are deliberately **not** folded into
`/api/check/{ticker}` so Quick Check stays fast; the panels load in parallel. See
[[event-probability-layer]], [[companyfacts-pricing]], and [[sharadar-bundle]].

## Portfolio, book, trades

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Account positions with P&L |
| GET | `/api/portfolio/risk-dashboard` | Unified risk pack |
| POST | `/api/portfolio/manual/positions` | Price a typed-in book (public, compute-only) |
| POST | `/api/portfolio/risk-dashboard/manual` | Risk pack for a typed-in book |
| GET | `/api/book/calendar` | P/L calendar |
| GET | `/api/book/tax` | ST/LT realized + tax estimate |
| GET | `/api/sectors` | Sector heatmap |
| POST | `/api/trades/{id}/approve` | Approve + execute (API key if set) |
| GET | `/api/trades/{id}/preflight` | Pre-trade checklist |

## Learning / operator

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/decision-dashboard` | System decision snapshot |
| GET | `/api/calibration/summary` | Calibration strip |
| GET | `/api/settled-lessons` | Today card: settled evidence, pocket label, current proposal |
| POST | `/api/settled-lessons/decision` | Record keep / sharpen / revert (does not apply settings or place orders) |
| POST | `/api/evolve/run` | Post-mortem tuner (apply=false) |
| POST | `/api/challenger/run` | Challenger cycle |
| GET | `/api/ablation/status` | Strategy-comparison cycle status |
| POST | `/api/ablation/run` | Start in-process ablation |
| GET | `/api/prob-rank/shadow-evidence` | Prob-rank vs rank-v2 ledger |
| GET | `/api/agents/grok/capabilities` | Grok agent mode, model, and published card (no secrets) |
| POST | `/api/agents/grok` | One hybrid handoff. Default mode `off` does not call xAI |

## Related Pages

- [[webapp-dashboard]] — architecture overview
- [[saas-endpoints]] — SaaS top-level API
- [[tenant-dashboard-endpoints]] — per-tenant SaaS router (dashboard parity)
- [[scan-catalog]] — `/api/scan-catalog` payload
- [[event-probability-layer]] — `/api/event-probability/*` contract and config
- [[frontend-route-contract]] — screen / `?section=` contract
- [[sharadar-bundle]] — `/api/research/sharadar/{ticker}` and compare
- [[grok-agent-orchestration]] — `/api/agents/grok` handoff contract

---

*Last compiled: 2026-09-21*
