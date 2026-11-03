---
source: schwab_skill/webapp/webapp.db (scan_results, app_state last_scan); yfinance closes through 2026-10-20
created: 2026-10-21
updated: 2026-10-21
tags: [scan-quality, ranking, tracking]
---

# Scan Quality 2026-10-21

> Scan desk premarket readout: data health of today's scan and how past scan picks played out.

## Today's scan (job 5a031eba03)

- Params: `universe_preset=sp1500`, `scan_timeframe=weekly`, default strategy (weekly_swing). 24 signals, all `executable=false` (horizon sleeve, research only).
- Universe is 100 names (`watchlist_source=sp1500_focused`), almost all tickers A-C plus TECH. Same on 10-19 and 10-20. Not a full S&P 1500 scan.
- `data_quality=degraded`, reason `quote_unavailable` (no live quotes; prices are last daily bar).
- `setup_confidence` sits at the 45.0 default on 20 of 24 rows, so it barely separates names.
- 18 Altman distress flags, 23 high SEC risk tags, 16 low breakout volume, advisory low confidence on all 25.

## Past picks (scan_results 2026-09-21 to 2026-10-20, 621 ticker-date picks, entry = close on scan date)

| Horizon | n | Mean ret | Excess vs SPY | Hit vs SPY |
|---|---|---|---|---|
| T+5 | 483 | -0.29% | +0.20% | 55% |
| T+10 | 241 | +0.68% | -0.05% | 49% |
| T+20 | 36 | +0.19% | +1.10% | 67% |

- `signal_score` tercile shows no edge. T+5 excess: low +0.64%, mid +0.01%, high -0.04%. T+20: low +3.3%, high -0.26% (n=12 each). Consistent with [[scan-confidence]]: live score is flat to inverted.
- Extension (`extension_pct_sma50`) has Spearman -0.19 vs T+5 excess. More extended names did worse, matching [[scan-confidence]].
- `setup_confidence` > 45 beat <= 45 at T+5 (+1.25% vs +0.15%, n=35 vs 412). Small sample, overlapping windows.

## Price-input check (2026-10-21, yfinance unadjusted daily closes)

| Ticker | Scan price / SMA50 / ext | yfinance 10-20 close | yfinance 10-21 / SMA50 / ext | 9/21 close / SMA50 / ext |
|---|---|---|---|---|
| ALL | 249.91 / 245.82 / +1.7% | 249.91 | 249.59 / 245.87 / +1.5% | 242.86 / 258.00 / -5.9% |
| BIIB | 243.10 / 220.94 / +10.0% | 243.10 | 242.48 / 221.71 / +9.4% | 219.07 / 210.96 / +3.8% |
| TECH | 72.58 / 72.38 / +0.3% | 72.58 | 72.59 / 72.40 / +0.3% | 72.47 / 72.12 / +0.5% |
| AIZ | 283.61 / 277.42 / +2.2% | 283.61 | 285.03 / 277.74 / +2.6% | 277.38 / 282.09 / -1.7% |
| APD | 302.58 / 292.26 / +3.5% | 302.58 | 301.68 / 293.49 / +2.8% | 279.46 / 298.65 / -6.4% |

- No price-input error. Scan prices equal the 10-20 close exactly (scan ran premarket 10-21 with `quote_unavailable`, so it used the last daily bar even though `business_as_of` says 10-21). SMA50s match within ~0.1%.
- The backtester's ALL -5.6% / BIIB +3.8% are 9/21 values (independent bars give -5.9% / +3.8%). The gap is the date, not the data: ALL rallied from 242.86 to ~250 while its SMA50 fell; BIIB ran from 219 to 243.
- Small aside: feature_store's 9/21 premarket row for ALL holds the 9/18 close (249.83, SMA50 258.17), the same one-bar lag.

## Setup-confidence claim (debate chair / Quant, 40d, n~66k, -2.0 pts)

- Not reproducible from feature_store: none of its 196k rows (raw_features_json on 5.4k) stores `setup_confidence`. It only exists in scan_results payloads (849 rows from 9/21 on), and none of those are 40 trading days old yet.
- The confidence model (`conf_57e9a8a229`, research_store/models) was fit on about 16.4k labeled rows (5 walk-forward era folds, target `net_return`), not 66k. Its three features (sma_200_slope, trend_strength_score, sma_20_slope) all carry sign -1: it rewards *weaker* trend slope. So Quant's 66k sample is probably a different dataset or definition; ask for its source before acting on it.
- Our +1.25% vs +0.15% (T+5, n=35 vs 412, overlapping windows, one month) is too thin to call positive. The two results differ in horizon (5d vs 40d) and sample, so they don't directly contradict. Treat confidence > 45 as **unproven either way**, and drop it as a ranking tiebreak until a 40d check on scan_results is possible (from about mid-November).
## Implications

Rank the desk table by low extension, not by signal_score (setup_confidence is unproven, see above). Fix the 100-name universe before trusting any scan as an S&P 1500 read. Proposal only; Sam decides.

## Related Pages

- [[scan-confidence]]
- [[signal-ranking]]
- [[scan-catalog]]
- [[trade-verdict-2026-10-20]]

---

*Last compiled: 2026-10-21*

