---
source: schwab_skill/core/scan_catalog.py
created: 2026-08-19
updated: 2026-08-19
tags: [scanner, strategies, universe, dashboard]
---

# Scan Catalog

> Timeframe grouping, strategy descriptions, and named universes for the dashboard Scan studio. The live bar engine remains daily Stage 2 + VCP.

## What this is

Operators asked to sort strategies by horizon (Intraday / Daily / Weekly / Monthly), read a short description of each sleeve, and scan more than S&P 1500. The catalog is the metadata layer that makes that possible **without** claiming weekly or monthly OHLCV engines that do not exist yet.

Live execution is still the daily [[signal-scanner]] (Stage 2 + VCP). Weekly and monthly catalog rows are **horizon labels** on that engine. Intraday is the existing breakout-confirm overlay (live quote), not a standalone minute-bar book.

## Timeframes

| Id | Meaning |
|----|---------|
| `intraday` | Confirm a daily setup on the live quote (`BREAKOUT_CONFIRM_ENABLED`) |
| `daily` | Primary live engine |
| `weekly` | Multi-week hold of the daily Stage 2 / VCP thesis |
| `monthly` | Position-style hold of the same thesis |

Dedicated weekly/monthly bar scanners are **not live**. Selecting those tabs still runs the daily pipeline and stamps `diagnostics.scan_timeframe`.

## Strategies

Canonical ids live in `core/scan_catalog.py` (`STRATEGIES`). Each row has `display_name`, `description`, `timeframe`, `status` (`live` / `live_overlay` / `shadow` / `research`), and `runnable`.

| Id | Timeframe | Status | Notes |
|----|-----------|--------|-------|
| `breakout_confirm` | intraday | live overlay | Selecting it sets `BREAKOUT_CONFIRM_ENABLED=true` for that scan only |
| `trend_breakout` | daily | live | Stage 2 / VCP momentum breakout (the live book) |
| `pullback` | daily | shadow | Filters to pullback-triggered names; does **not** promote `STRATEGY_PULLBACK_MODE` to LIVE |
| `pead_primary` | daily | shadow | Paper PEAD sleeve; Stage 2 still owns executable entries |
| `weekly_swing` | weekly | research | Proxies `trend_breakout` |
| `monthly_position` | monthly | research | Proxies `trend_breakout` |

Plugin promotion remains OFF → SHADOW → LIVE. Scan studio selection is a **filter + description**, not a LIVE promotion.

## Universes

`POST /api/scan` accepts `universe_preset` in addition to the older `universe_mode=tickers` override.

| Id | Source | Available |
|----|--------|-----------|
| `sp1500` | Wikipedia S&P 500+400+600 (default) | yes |
| `sp500` | Wikipedia S&P 500 | yes |
| `nasdaq100` | Wikipedia Nasdaq-100 (fallback list if fetch fails) | yes |
| `sector_etfs` | `LIQUID_ETF_HINTS` (SPY, QQQ, XL*) | yes |
| `focused` | Deterministic SP1500 sample (`SIGNAL_UNIVERSE_TARGET_SIZE`) | yes |
| `custom` | Explicit `tickers[]` (capped by `SAAS_SCAN_MAX_CUSTOM_TICKERS`, default 40) | yes |
| `russell2000` | Listed in the catalog only | **no** — no constituent feed yet |

## API

- `GET /api/scan-catalog` — public, cheap read. Payload: `timeframes`, `strategies`, `universes`, `defaults`, `notes`.
- `POST /api/scan` body extras: `universe_preset`, `scan_timeframe`, `strategy_ids`.

Dashboard: `#scanStudioPanel` on the Today scan lane. Prefs persist in `tradingbot.scan.studio`. Deep link: `?section=scanstudio`.

## Related Pages

- [[signal-scanner]] — Stage A/B pipeline that still runs
- [[stage-2-analysis]] — live breakout thesis
- [[vcp-detection]] — volume contraction gate
- [[pead]] — shadow earnings-drift sleeve
- [[plugin-modes]] — why shadow strategies are not auto-promoted
- [[webapp-dashboard]] — Scan studio UI
- [[local-dashboard-endpoints]] — `/api/scan-catalog`
- [[saas-endpoints]] — same catalog route on SaaS
- [[frontend-route-contract]] — `scanstudio` alias
- [[static-module-layout]] — `panels/scanStudio.js`

---

*Last compiled: 2026-08-19*
