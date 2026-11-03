# Liquid Phase 0 spike — report

Date: 2026-10-21. Source data: `validation_artifacts/liquid_phase0_spike.json`
(regenerate: `python scripts/liquid_phase0_spike.py`).

## Verdict: GO (bounded)

A server-collectable, backtestable historical source exists. Liquid's MCP
itself is snapshot-only and unsuitable as a dashboard dependency, but
Liquid's equity perps are Hyperliquid `xyz` HIP-3 markets, and the public
Hyperliquid info API (`https://api.hyperliquid.xyz/info`, no auth) exposes
the same markets.

## Verified facts (this session, live calls)

- xyz universe: 137 markets in one `metaAndAssetCtxs` call (funding, OI,
  mark price, 24h volume, max leverage per market).
- Mapping validated: xyz:AAPL OI 178,089 coin x $343.63 = $61.2M vs
  Liquid MCP's reported AAPL OI $61.3M. xyz:CVX thin via both sources.
- Funding history: hourly, paginated 500/page. xyz:AAPL full pull =
  8,017 rows, 2025-11-21T15:00Z to 2026-10-21T15:00Z.
- Daily candles: xyz:AAPL 335 bars from 2025-11-21 (listing inception).
- Coverage: last 90 days of `scan_results` = 162 distinct tickers,
  15 covered (9.3%): AAPL, ACN, AMAT, AMD, AMZN, AVGO, BE, COST, CVX,
  GEV, GOOGL, INTC, LRCX, MRVL, MSFT.

## Hard limits (do not paper over)

- History is ~11 months (from Nov 2025 listings), NOT the multi-era
  window the promotion ledger uses. Funding/OI backtests can only cover
  a recent sample; era splits and sample sizes must be reported, and
  insufficient samples labelled as such (never PF 0).
- Coverage is ~9% and mega-cap biased. Any measured effect is a
  large-cap sub-universe result until proven otherwise.
- Liquid's cohort positioning (smart-money/crowd skew) has NO history
  and no public API. It remains Cursor-side MCP research only.

## What shipped with this phase

- `core/liquid_positioning/` (schema, normalizer, provider, store,
  settings): one-call snapshots of all 137 markets, append-only
  `liquid_snapshots` table, thin-OI flag (`LIQUID_MIN_OI_USD`, default
  $1M) so thin markets read as no-signal.
- Collector: `scripts/collect_liquid_snapshots.py --force`; hourly
  scheduled run in `main.py` bot loop at :05 when
  `LIQUID_COLLECT_ENABLED=true` (set in `.env`, mode `shadow`).
- First live collect (2026-10-21 15:21Z): 137 stored, 51 thin,
  0 malformed, 0 provider errors.
- Config via `config.py` getters only: `LIQUID_OVERLAY_MODE`
  (off|shadow|live, default shadow), `LIQUID_COLLECT_ENABLED`,
  `LIQUID_DEX`, `LIQUID_API_URL`, `LIQUID_MIN_OI_USD`.

## Phase 1b/2 addendum (2026-10-21, later session)

- Funding backfill shipped: `scripts/backfill_liquid_funding.py` +
  `core/liquid_positioning/{backfill,features}.py`, `liquid_funding_daily`
  table. Covered scan tickers backfilled: 2,882 ticker-days across
  14 tickers (AAPL/AMZN/GOOGL/MSFT ~335-338d from Nov 2025 listings;
  newer listings shorter — LRCX 16d, CVX 35d). ACN is listed but has
  zero funding history (uncovered for features until it trades).
- Provider now backs off on HTTP 429 (first backfill hit the rate
  limit after 9 tickers; retry logic fixed it).
- As-of features: `funding_features_asof` uses only funding days
  strictly before the entry date (`date < as_of`); pinned by unit test
  against same-day leakage. Features: funding_mean_30d, funding_last,
  funding_z90, funding_pct_rank, funding_days (None when uncovered,
  never zero-filled).
- Research UI shipped (shadow): Research -> Quick check ->
  "Liquid positioning" (`liquidPositioningSection`, aliases
  `?section=liquid|liquid-positioning|positioning`), API
  `GET /api/liquid/overview` + `GET /api/liquid/{ticker}` in
  `webapp/routes/liquid.py` (local app; SaaS tenant router not yet).
  Panel verified live on https://127.0.0.1:8182 with AAPL lookup
  (335d funding sparkline, z90 -0.25, pct rank 30%).
- Open-interest history CANNOT be backfilled (API exposes only current
  OI). OI variables accrue forward from collector snapshots only.

## Next gates before any backtest variable work (Phase 3)

1. Collector accrues >= 5 trading days without provider errors
   (OI history starts here — funding is already backfilled).
2. Hypothesis card filed (funding z-score first, then OI change once
   snapshots accrue, then crowding composite) — single-variable
   first, per signal-edge-review. Sample is ~14 tickers x <=11
   months: label insufficient-sample honestly, never PF 0.
3. Backtest as-of join wired into `backtest.py` behind
   `LIQUID_OVERLAY_MODE=shadow`, with coverage reported per run.
