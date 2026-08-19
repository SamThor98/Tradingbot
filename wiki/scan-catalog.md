---
source: schwab_skill/core/scan_catalog.py
created: 2026-08-19
updated: 2026-08-19
tags: [scanner, strategies, universe, dashboard]
---

# Scan Catalog

> Timeframe grouping, strategy descriptions, and named universes for the dashboard Scan studio. The live bar engine remains daily Stage 2 + VCP. Extra sleeves are real evaluators in shadow/research — they do not promote to LIVE.

## What this is

Operators asked to sort strategies by horizon (Intraday / Daily / Weekly / Monthly), read a short description of each sleeve, and scan more than S&P 1500. The catalog is the metadata layer in `core/scan_catalog.py`.

Live execution is still the daily [[signal-scanner]] (Stage 2 + VCP). Weekly and monthly catalog rows **resample** that daily OHLCV (Friday week / month-end); they are not a separate vendor feed. Intraday includes the existing breakout-confirm overlay (live quote) plus session-structure proxies on the latest daily bar. Evaluator math lives in [[horizon-strategies]].

## Timeframes

| Id | Meaning |
|----|---------|
| `intraday` | Live-quote confirm plus daily-bar gap/range session structure |
| `daily` | Primary live engine plus shadow daily sleeves |
| `weekly` | Resampled Friday bars (30-week SMA family) |
| `monthly` | Resampled month-end bars (10-month SMA family) |

Selecting a weekly/monthly tab still runs the daily pipeline (`get_daily_history`) and stamps `diagnostics.scan_timeframe`. The difference is **which evaluators admit and filter**, not a second market-data engine. Each tab defaults to **one** primary sleeve; extra / Paper rows are opt-in so opposite theses are not OR-filtered together.

## Strategies

Canonical ids live in `core/scan_catalog.py` (`STRATEGIES`). Each row has `display_name`, `description`, `timeframe`, `status` (`live` / `live_overlay` / `shadow` / `research`), and `runnable`.

| Id | Timeframe | Status | Notes |
|----|-----------|--------|-------|
| `breakout_confirm` | intraday | live overlay | Sets `BREAKOUT_CONFIRM_ENABLED=true` for that scan only |
| `gap_and_go` | intraday | shadow | Opening gap hold on the latest daily bar |
| `range_expansion` | intraday | shadow | Wide bar closing through the prior high |
| `trend_breakout` | daily | live | Stage 2 / VCP momentum breakout (the live book) |
| `pullback` | daily | shadow | Filters to pullback-triggered names; does **not** promote `STRATEGY_PULLBACK_MODE` to LIVE |
| `pead_primary` | daily | shadow | Paper PEAD sleeve; Stage 2 still owns executable entries |
| `donchian_20` | daily | shadow | 20-day channel breakout + 200-day SMA |
| `nr7_breakout` | daily | shadow | Fisher NR7 then close through that high |
| `weekly_swing` | weekly | research | Weinstein weekly Stage 2 on resampled bars |
| `weekly_vcp` | weekly | research | Weekly volume dryness (not Minervini VCP) |
| `weekly_breakout` | weekly | research | Weekly close through prior week high |
| `monthly_position` | monthly | research | Faber 10-month SMA timing |
| `monthly_52w_high` | monthly | research | Month-end close near 52-week high |
| `monthly_pullback` | monthly | research | Pullback that tags the 10-month SMA |
| `opening_range_breakout` | intraday | research | **Paper proxy.** Completed-daily RVOL strong-close — not 5-minute ORB |
| `st_reversal_5d` | daily | research | **Paper proxy.** Single-name 5-day loser bounce + $2M ADV |
| `overnight_gap_fade` | daily | research | **Paper proxy.** EOD gap-down recovery label |
| `weekly_reversal` | weekly | research | **Paper proxy.** Weekly loser bounce on completed Friday bars |
| `momentum_12_1` | monthly | research | **Paper proxy.** 12-1 strength screen, top-10 of scan hits |
| `tsmom_12m` | monthly | research | **Paper proxy.** 12-month single-name trend screen |

Literature rows are **additive**. Iterated ids stay in `ITERATED_STRATEGY_IDS` and show a **Yours** chip in Scan studio. Plugin promotion remains OFF → SHADOW → LIVE. Horizon-only names are `executable=false`.

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

Dashboard: `#scanStudioPanel` on the Today scan lane. Prefs persist in `tradingbot.scan.studio.v2`. The v1 key `tradingbot.scan.studio` is read once: universe and custom tickers are kept, but `strategy_ids` reset to that tab's `default_strategy_id` so leftover "select all" sessions do not OR every sleeve. Deep link: `?section=scanstudio`. Editorial visual language follows the Old Logan Figma DS (`ol/bg`, `ol/surface`, `ol/accent`, `ol/gold`): segmented horizon tabs, Yours vs Paper groups, gold rail on the selected primary. Live execution copy stays daily Stage 2 / VCP.

## Related Pages

- [[horizon-strategies]] — evaluator rules, dual-admit, and literature vs iterated merge
- [[signal-scanner]] — Stage A/B pipeline that still runs
- [[stage-2-analysis]] — live breakout thesis
- [[vcp-detection]] — volume contraction gate (skipped for horizon-only admits)
- [[pead]] — shadow earnings-drift sleeve
- [[plugin-modes]] — why shadow strategies are not auto-promoted
- [[webapp-dashboard]] — Scan studio UI
- [[local-dashboard-endpoints]] — `/api/scan-catalog`
- [[saas-endpoints]] — same catalog route on SaaS
- [[frontend-route-contract]] — `scanstudio` alias
- [[static-module-layout]] — `panels/scanStudio.js`

---

*Last compiled: 2026-08-19*
