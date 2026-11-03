---
source: experiments/backtester/results_earnings_vetoes_2026-10-29.json
created: 2026-10-29
updated: 2026-10-29
tags: [backtest, research, earnings, vetoes, shrinkage, walk-forward, trend-breakout, weekly-swing, debate]
---

# Earnings inside the hold, and small-sample per-name vetoes (2026-10-29)

> (A) An earnings date in the first 10 sessions does NOT lower mean excess vs SPY (no OOS cell significant), but it roughly doubles the stop-gap rate (OOS 19-21% vs 10-11%) and fattens the left tail. Half-size, not skip, is the defensible rule, and it is a risk rule, not an alpha rule. (B) Small-sample vetoes (n 3-14) do nothing for weekly_swing; for trend_breakout the veto bucket trails by -0.5%/trade over all years, CI excluding zero, but OOS is not significant. Rank correlation of own history with the next trade is about 0 (raw or shrunk).

Debate chair request. Offline and read-only. No orders, no app code, no Schwab endpoints. Script: `experiments/backtester/earnings_vetoes_2026_10_29.py`, earnings fetchers `fetch_earnings_2026_10_29.py` (yfinance) and `fetch_earnings_edgar_2026_10_29.py` (SEC EDGAR). Pools reused from [[bt-2026-10-22-weekly-swing-six-names-breadth]] and the Oct 22 TB pool (see [[bt-2026-10-27-both-lookahead-free]]). Related: [[bt-2026-10-28-ws-only-candidates]], [[sector-map-mismatches]], [[quant-debate-2026-10-29]].

## Method
- TB = trend_breakout core, entry at the next open after the signal. WS = weekly_swing, entry at the open after the Friday confirmation. Exits T20 and STATE. Costs 0.34% round trip. IS 2015-10 to 2021, OOS 2022 to 2026-09. Per-trade excess vs SPY and vs the repo sector ETF.
- Earnings dates: 301 names from yfinance (realized report dates); 1,170 from SEC EDGAR 8-K Item 2.02 filing dates, which is point-in-time as the announcement date. 8 names have no dates and are dropped. Coverage: TB 151,290 of 152,078 trades, WS 79,581 of 80,044. The flag is set when an earnings date falls in [entry-1 day, entry+9 sessions].
- R = 8% (a fixed -8% stop reference, since the pools have no stop). Stop-gap = an open in sessions 2-10 below entry x 0.92.
- CIs come from a week-block bootstrap with 2,000 draws.

## A. Earnings in the first 10 sessions (earn vs no-earn)
| cell | n earn / no | mean xSPY earn / no | diff [95% CI] | diff vs sector [CI] | median earn / no | p5 ret earn / no | worse than -1R | stop-gap |
|---|---|---|---|---|---|---|---|---|
| TB T20 IS | 7431 / 38255 | +0.39 / -0.09 | +0.48 [+0.10, +0.87] | +0.18 [-0.13, +0.51] | -0.01 / -0.29 | -15.3 / -11.9 | 15.2 / 10.6% | 15.0 / 7.1% |
| TB T20 OOS | 4813 / 24261 | -0.13 / -0.48 | +0.35 [-0.36, +1.13] | +0.51 [-0.09, +1.11] | -0.95 / -0.94 | -16.3 / -14.1 | 18.2 / 15.7% | 19.2 / 9.9% |
| TB STATE IS | 7431 / 38255 | +0.12 / -0.21 | +0.33 [+0.06, +0.60] | +0.17 [-0.09, +0.43] | -1.38 / -1.43 | -10.3 / -8.2 | 9.1 / 5.5% | 15.0 / 7.1% |
| TB STATE OOS | 4796 / 24186 | -0.23 / -0.40 | +0.17 [-0.34, +0.72] | +0.22 [-0.30, +0.70] | -1.61 / -1.80 | -11.3 / -9.5 | 11.6 / 8.3% | 19.3 / 9.9% |
| WS T20 IS | 3539 / 18986 | +0.54 / +0.14 | +0.40 [-0.11, +0.92] | +0.21 [-0.26, +0.67] | -0.11 / -0.11 | -15.9 / -12.9 | 15.8 / 11.9% | 16.7 / 8.4% |
| WS T20 OOS | 2737 / 14086 | -0.27 / -0.36 | +0.08 [-0.72, +0.93] | +0.15 [-0.57, +0.94] | -0.67 / -0.78 | -17.4 / -14.8 | 18.7 / 16.2% | 21.4 / 10.8% |
| WS STATE IS | 3539 / 18986 | +0.60 / -0.21 | +0.81 [+0.22, +1.41] | +0.62 [+0.02, +1.26] | -1.96 / -2.14 | -12.2 / -10.7 | 14.4 / 10.4% | 16.7 / 8.4% |
| WS STATE OOS | 2721 / 13964 | -1.08 / -0.96 | -0.12 [-0.83, +0.55] | -0.18 [-0.78, +0.45] | -3.13 / -2.91 | -14.7 / -12.1 | 18.7 / 14.4% | 21.5 / 10.9% |

About 16% of entries carry earnings. Per-trade expected impact of half-sizing those entries is -0.01% to -0.06% per average trade (it gives up a small, non-significant mean gain) in exchange for roughly halving the gap exposure on 16% of the book. Skipping gives up more of that and is not supported. No full 10-slot portfolio sim was run.
Anecdote only: WELL on 10-22 gapped through its stop on earnings (-1.14R), consistent with the tail finding, not evidence on the mean.

**Verdict A:** earnings in the hold is a volatility and gap risk, not a negative-edge signal. Pass the rule as half-size (risk control); do not skip. No OOS mean difference.

## B. Walk-forward small-sample vetoes (T20; own prior same-setup trades closed before entry, n 3-14)
Veto = own mean excess below the PIT pool mean and own share beating SPY below 30%. Shrunk = w*own + (1-w)*pool with w = n/(n+10).
| cell | n / veto n | veto fwd | rest fwd | diff [95% CI] | beat SPY veto / rest | Spearman raw | Spearman shrunk |
|---|---|---|---|---|---|---|---|
| TB IS | 15889 / 3006 | -0.13 | +0.36 | -0.49 [-0.93, -0.08] | 49.2 / 49.3% | +0.004 | -0.002 |
| TB OOS | 1500 / 361 | -0.77 | -0.11 | -0.66 [-2.05, +0.70] | 40.4 / 46.7% | +0.044 | +0.049 |
| TB ALL | 17389 / 3367 | -0.20 | +0.32 | -0.52 [-0.95, -0.15] | 48.3 / 49.0% | +0.011 | +0.006 |
| WS IS | 14461 / 1966 | +0.10 | +0.14 | -0.04 [-0.52, +0.44] | 48.2 / 48.9% | +0.002 | -0.003 |
| WS OOS | 2472 / 364 | -0.16 | +0.01 | -0.16 [-1.45, +1.14] | 45.6 / 46.6% | -0.001 | +0.002 |
| WS ALL | 16933 / 2330 | +0.06 | +0.12 | -0.06 [-0.53, +0.38] | 47.8 / 48.5% | +0.002 | -0.001 |

STATE exits are not used here, because their exit dates are not stored in the pool. Anecdote only: SAIC was vetoed on 14 trades and is now +1.30R.

**Verdict B:** for weekly_swing, small-sample vetoes are noise; drop them. For trend_breakout there is a weak, rule-level effect of about -0.5%/trade (IS-driven; OOS points the same way but n=361 and CI spans zero). Own history as a continuous predictor is useless (|rho| < 0.05), and shrinking does not help the ranking. It only stops a 3-trade history from dominating. Treat a TB veto as at most a size tilt, not a hard block.

## Caveats
- Survivorship: delisted names are missing from the pools, which flatters levels. Differences are less affected.
- Lookahead: the yfinance dates (301 names) are realized dates, not dates known at entry. The EDGAR dates are filing dates. Reporting dates are normally announced 2-4 weeks ahead, so the leak is small. There is no live calendar test.
- Multiple testing: 8 cells in A and 6 in B. With Bonferroni over 14 tests, only TB ALL in B (and none of A OOS) is near the bar. Treat everything as exploratory.
- Overlapping trades are serially correlated, which the week-block bootstrap only partly handles. The -8% R is a fixed stand-in, not the live ATR stop. There is no portfolio-level half-size sim. Sector-map errors ([[sector-map-mismatches]]) affect the vs-sector columns.
