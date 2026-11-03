---
source: schwab_skill/core/scan_catalog.py
created: 2026-08-19
updated: 2026-09-17
tags: [scanner, strategies, universe, dashboard]
---

# Scan Catalog

> Timeframe grouping, strategy descriptions, and named universes for the dashboard Scan studio. The live bar engine remains daily Stage 2 + VCP. Extra sleeves are real evaluators in shadow/research — they do not promote to LIVE.

## What this is

Operators asked to sort strategies by horizon (Daily / Weekly / Monthly), read a short description of each sleeve, and scan more than S&P 1500. The catalog is the metadata layer in `core/scan_catalog.py`. Scan studio no longer offers an Intraday tab.

Live execution is still the daily [[signal-scanner]] (Stage 2 + VCP). Weekly and monthly catalog rows **resample** that daily OHLCV (Friday week / month-end); they are not a separate vendor feed. Session-structure evaluators (`gap_and_go`, `range_expansion`, `breakout_confirm`) still exist in the strategy catalog for engine/API use; they are not a Scan studio horizon. Evaluator math lives in [[horizon-strategies]].

## Timeframes

| Id | Meaning |
|----|---------|
| `daily` | Primary live engine plus shadow daily sleeves |
| `weekly` | Resampled Friday bars (30-week SMA family) |
| `monthly` | Resampled month-end bars (10-month SMA family) |

Selecting a weekly/monthly tab still runs the daily pipeline (`get_daily_history`) and stamps `diagnostics.scan_timeframe`. The difference is **which evaluators admit, rank, and fill Stage B**, not a second market-data engine. A dedicated weekly / PEAD / Donchian scan uses that sleeve's own score and the same Stage B budget as Stage 2 — it is not ranked or capped as a leftover of the live book. Each tab defaults to **one** primary sleeve; extra / Paper rows are opt-in so opposite theses are not OR-filtered together. Each catalog row carries a qualitative **return profile** (`style`, `hold`, `payoff`, `edge`) shown on Scan studio cards. These are sleeve-design notes, not a live backtest print.

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
- Dashboard HTML also embeds that payload in `#scanCatalogBootstrap` so Scan studio paints all sleeves after a refresh even if the catalog GET is cached or delayed. `scanStudio.js` is cache-busted via an import map (`?v=` app version).
- `POST /api/scan` body extras: `universe_preset`, `scan_timeframe`, `strategy_ids`.

Dashboard: `#scanStudioPanel` on the Today scan lane is the **live local operator control** (and the same static panel on SaaS). The panel is a three-step setup: **universe** (radio cards) → **horizon** (tabs) → **strategies** (checkboxes). A **This scan will use** summary always shows the current universe, horizon(s), and every checked strategy name. The focused horizon is expanded; other horizons stay in collapsed `<details>` with a selected count. `POST /api/scan` sends **every checked** `strategy_ids` across Daily / Weekly / Monthly — the focused tab only changes which board is expanded. If the checked sleeves share one timeframe, `scan_timeframe` follows that sleeve; mixed checks keep the focused tab. Prefs persist in `tradingbot.scan.studio.v3`. The v1 key `tradingbot.scan.studio` is read once: universe and custom tickers are kept, but `strategy_ids` reset to that tab's `default_strategy_id` so leftover "select all" sessions do not OR every sleeve. Deep link: `?section=scanstudio`. `GET /api/public-config` advertises `scan_studio.enabled` on both local and SaaS. Editorial visual language follows the Old Logan Figma DS (`ol/bg`, `ol/surface`, `ol/accent`, `ol/gold`). Live execution copy stays daily Stage 2 / VCP — extra sleeves remain shadow/research. The candidate table Strategy column lists every `passed_names` hit for the row (not a folded +N chip).

The Backtest **Queue a backtest** composer (`#btComposerCatalog` on `?screen=backtest`) reuses this catalog: same `universe_preset` radios, horizon tabs, and strategy checkboxes, plus Stage 2 / VCP / quality-score variables. `POST /api/backtest-runs` StrategySpec v1 accepts `universe_preset`, `scan_timeframe`, and `strategy_ids`. Named indexes load through `signal_scanner._load_watchlist`. Selected sleeves dual-admit in `backtest.py` with the same Stage A arms as a scan. See [[backtest]].

## Related Pages

- [[horizon-strategies]] — evaluator rules, dual-admit, and literature vs iterated merge
- [[signal-scanner]] — Stage A/B pipeline that still runs
- [[stage-2-analysis]] — live breakout thesis
- [[vcp-detection]] — volume contraction gate (skipped for horizon-only admits)
- [[pead]] — shadow earnings-drift sleeve
- [[plugin-modes]] — why shadow strategies are not auto-promoted
- [[webapp-dashboard]] — Scan studio UI
- [[backtest]] — Queue composer shares this catalog
- [[local-dashboard-endpoints]] — `/api/scan-catalog`
- [[saas-endpoints]] — same catalog route on SaaS
- [[frontend-route-contract]] — `scanstudio` alias
- [[static-module-layout]] — `panels/scanStudio.js`

---

*Last compiled: 2026-09-11*
