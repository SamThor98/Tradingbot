---
source: schwab_skill/research/pit_universe.py
created: 2026-08-24
updated: 2026-10-16
tags: [research, pit, labels, warehouse]
---

# PIT research platform

> Point-in-time universe/fundamentals, triple-barrier labels, CPCV, factor analytics, and a research warehouse. Does not replace the canonical Schwab-only backtest runner.

## Universe and fundamentals

`watchlist_loader.py` still uses Wikipedia **today's** members for live scans. Research membership uses `PITUniverseProvider.universe_asof(date)`:

- **stub** — fixture list-dates (tests; SNAP/SNOW absent on 2016-06-01)
- **sharadar** — direct `https://api.sharadar.com/v1.0` `sp500` table (not Nasdaq Data Link); reconstruct as-of from `action=current` plus added/removed events. **Fail-closed** without `SHARADAR_API_KEY`, on HTTP 401/403, or when `action=current` returns a DJIA-sized sample (`n < 50`, typical free tier). Live `PIT_UNIVERSE_PROVIDER` stays `stub` until this is reviewed. Universe is S&P 500, not SP1500. ART fundamentals, daily multiples, insiders, and `holdings_ticker` are joined for dashboard snapshots and `fundamentals_asof` (see [[sharadar-bundle]]). Missing values stay NA — never invented zeros.

Deferred registry fields (`market_cap`, `float_shares`, `institutional_ownership`, `insider_ownership`, `estimate_revisions`, `roe`) stay `enabled: false` until coverage ≥ `PIT_FUNDAMENTAL_COVERAGE_MIN` (default 0.70). Missing values stay NA — never invented zeros.

Replay: `scripts/replay_pit_universe_era.py`. Reports PIT vs Wikipedia-now trade-count and PF delta. Does **not** silently replace `run_multi_era_backtest_schwab_only.py`.

## Triple-barrier labels and CPCV

Labels match adaptive-stop knobs + exit-grace 15/40: `{label, barrier_hit, holding_days, ret_net}`. Meta-label P(win|Stage-2) is SHADOW-only (`META_LABEL_MODE`). Combinatorial purged K-fold embargo ≥ max hold. Retrain: `scripts/retrain_barrier_label_challenger.py`. **Do not switch `PROB_RANK_MODE` off shadow.**

## Factor analytics

`scripts/run_factor_analytics.py` reports IC by era, IC decay, quantile forward returns, turnover. Optional equity tearsheet is **book diagnostics**, not a PF gate. Writes `validation_artifacts/factor_analytics/<run_id>/`. Does not overwrite [[backtest]] catalog numbers. Documented negative IC on `stage2_only_aug`: `signal_score` −0.0268 (see [[signal-ranking]]).

## Param sweep

`scripts/run_param_sweep.py` is **not promotion-eligible**. Survivors must be re-run through the Schwab-only live-parity runner. Isolated from `signal_scanner.py` / `execution.py`.

## Allocator

`core/sleeve_optimizer.py` maps sleeve desired-weights → net book (long-only, name/cluster/corr/DD/cash). Policies tagged S0 / S1 / S3 / allocator-only. LIVE sizes; it does not invent entries or place orders. OFF/SHADOW/LIVE via existing `ALLOCATOR_MODE`.

## Warehouse

DuckDB views over versioned Parquet; Pandera schemas from `feature_registry.json`. Ops vs research names: `research/ops_alignment.py` — enabled names must be SQL columns, aliases, or documented `raw_features_json` overflow.

## FRED overlay

`FRED_REGIME_MODE=shadow` only. Rates/spreads as PIT panel features — **not a live hard gate**. Fail-closed without `FRED_API_KEY`.

## Related Pages

- [[purged-net-rank]] — embargoed ridge on net return; not promoted
- [[probabilistic-ranking-research-architecture]] — prob-rank KEEP SHADOW
- [[feature-store]] — ops SQL vs research Parquet
- [[signal-ranking]] — negative-IC evidence
- [[multi-sleeve-trading-system-constitution]] — sleeve caps and allocator
- [[backtest]] — canonical Schwab-only runner
- [[adaptive-stops]] — stop knobs used by the barrier labeler
- [[sharadar-bundle]] — dashboard snapshot over the same Bundle tables
- [[autonomous-investment-network]] — Discovery agents must use this PIT path, not today's index members

---

*Last compiled: 2026-10-16*
