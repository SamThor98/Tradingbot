---
source: experiments/backtester/results_weekly_swing_2026-10-21.json
created: 2026-10-21
updated: 2026-10-21
tags: [backtest, research, weekly, horizon-strategies]
---

# Weekly swing backtest: ALL, BIIB, and stretch buckets (2026-10-21)

> Offline replay of the scan desk `weekly_swing` sleeve. ALL and BIIB individually have tiny, negative-edge histories. Universe-wide, the setup does not beat SPY OOS. Stretch above 5% vs below 3% shows no reliable difference: positive in sample on SP1500 only, null OOS and on the PIT S&P 500.

## Rule used (quoted from `core/horizon_strategies.py`, `evaluate_weekly_swing`)

"Weinstein-style weekly Stage 2: close > 10w SMA > 30w SMA, 30w rising, near highs." On completed W-FRI weeks resampled from daily bars:

- `stacked = last_close > sma10 > sma30 > 0`
- `rising = sma30[-1] > sma30[-9]` (`_sma_rising(sma30, lookback=8)`)
- `near_high = last_close >= 0.85 * max(weekly high, last 52 weeks)`
- `triggered = stacked and rising and near_high`

My vectorized copy matched the repo function on 10 of 10 spot-check weeks for ALL. The sleeve defines **no exit** ([[horizon-strategies]]: shadow/research, `executable=false`), so these choices were declared in advance:

- **Event:** the trigger turns on after being off the previous week.
- **Entry:** next trading day's open after the signal Friday.
- **STATE exit:** the next open after the first completed week with the trigger off, capped at 52 weeks. Trades still open at the data end are dropped.
- **T20 exit:** the close 20 trading days after entry.
- **Costs:** 0.34% round trip (15 bps per side plus fees).
- **SPY comparison:** SPY open-to-close over the same days, net of the same cost.
- **Stretch:** daily close / 50-day SMA - 1 at the signal close.

That is 2 exit definitions x 3 stretch buckets. Data: read-only `fast_slope/price_cache` (1,479 tickers, 2014-06-02 to 2026-09-21). Universes are SP1500-now and the PIT S&P 500 ([[pit-research-platform]]). IS is signals from 2015-10-16 to 2021, OOS is 2022 onward. Script: `experiments/backtester/weekly_swing.py`.

## 1. Single tickers (full history, 2014-06 to 2026-09)

| Ticker | Exit | n | Win % | Avg ret | Median | Worst MAE | Avg MAE | Seq. equity MDD | Avg hold (d) | SPY avg same days | Excess vs SPY | Beat SPY |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ALL | STATE | 40 | 35% | +0.49% | -1.37% | -14.6% | -3.7% | -30.0% | 33 | +1.77% | -1.28% | 33% |
| ALL | T20 | 40 | 50% | -0.83% | -0.12% | -15.5% | -4.4% | -47.5% | 20 | +0.27% | -1.10% | 43% |
| BIIB | STATE | 23 | 22% | -1.28% | -1.16% | -9.9% | -4.8% | -38.8% | 23 | +0.32% | -1.60% | 39% |
| BIIB | T20 | 24 | 50% | +0.92% | -0.03% | -16.9% | -6.6% | -37.0% | 20 | -0.35% | +1.27% | 50% |

**Small-sample warning: n is 23-40 trades over 11 years.** A single trade moves the averages by about 1 point, and no single-ticker result here is distinguishable from zero. "Seq. equity MDD" compounds 100% into each trade in turn.

**Current state:** in the cached data (last bar 2026-09-21), ALL was not triggered, with stretch -5.6%. BIIB was triggered, with stretch +3.8%. The desk's current stretches (ALL 1.7%, BIIB 10%) come from later bars than this cache and were not re-verified.

## 2. Universe-wide by stretch bucket

| Universe | Slice | Exit | <3%: n / win / avg / excess | 3-5%: n / win / avg / excess | >5%: n / win / avg / excess | Avg hold <3 / >5 | Avg MAE <3 / >5 | (>5) - (<3) excess, 95% CI (P>0) |
|---|---|---|---|---|---|---|---|---|
| SP1500 | IS | STATE | 11,559 / 30% / +0.32% / -0.30% | 3,708 / 34% / +0.53% / -0.29% | 7,886 / 40% / +2.18% / +0.38% | 27 / 40 | -4.5% / -6.9% | [+0.01, +1.33] pp (0.98) |
| SP1500 | IS | T20 | 11,559 / 54% / +0.21% / -0.01% | 3,708 / 52% / +0.23% / +0.09% | 7,886 / 54% / +1.14% / +0.52% | 20 / 20 | -5.8% / -7.1% | [-0.13, +1.22] pp (0.93) |
| SP1500 | OOS | STATE | 7,580 / 28% / -0.19% / -1.08% | 2,766 / 31% / +0.27% / -0.72% | 6,542 / 32% / -0.02% / -1.06% | 23 / 33 | -4.9% / -7.6% | [-0.69, +0.74] pp (0.53) |
| SP1500 | OOS | T20 | 7,547 / 51% / +0.37% / -0.54% | 2,775 / 52% / +0.65% / -0.08% | 6,617 / 49% / +0.28% / -0.20% | 20 / 20 | -6.3% / -7.7% | [-0.36, +1.07] pp (0.81) |
| PIT S&P 500 | IS | STATE | 4,589 / 32% / +0.24% / -0.35% | 1,248 / 37% / +0.50% / -0.47% | 2,156 / 40% / +1.33% / -0.36% | 28 / 43 | -4.1% / -5.7% | [-0.64, +0.59] pp (0.46) |
| PIT S&P 500 | IS | T20 | 4,589 / 55% / +0.17% / -0.06% | 1,248 / 55% / +0.26% / +0.16% | 2,156 / 54% / +0.41% / -0.06% | 20 / 20 | -5.2% / -5.6% | [-0.59, +0.65] pp (0.47) |
| PIT S&P 500 | OOS | STATE | 2,970 / 28% / -0.20% / -1.11% | 953 / 31% / -0.21% / -0.94% | 1,836 / 33% / +0.33% / -0.89% | 24 / 36 | -4.3% / -6.4% | [-0.64, +1.14] pp (0.69) |
| PIT S&P 500 | OOS | T20 | 2,962 / 51% / +0.23% / -0.67% | 954 / 51% / -0.02% / -0.68% | 1,863 / 48% / +0.07% / -0.34% | 20 / 20 | -5.5% / -6.3% | [-0.43, +1.13] pp (0.81) |

The CI is a week-clustered bootstrap (2,000 draws) of mean excess vs SPY, for the >5% bucket minus the <3% bucket. Worst single-trade MAE ranges from -29% to -90%, with the -90% from one SP1500 name. Sequential equity MDD is not meaningful across thousands of overlapping trades and is omitted. Every bucket's OOS excess vs SPY is negative: -0.1 to -1.1 pp per trade.

## Verdicts

- **ALL, BIIB:** **inconclusive / no evidence of edge.** Each history is too small (n 23-40) to say anything, and the point estimates mostly trail SPY over the same holds. Neither ticker's history supports a buy.
- **Stretch >5% vs <3%:** **no reliable effect.** The only significant result is IS STATE on SP1500, +0.7 pp, with the CI just clearing zero. It disappears OOS (P 0.53) and on the PIT S&P 500 in both periods. Extended entries carry deeper average MAE: -6.4% to -7.7% vs -4.1% to -6.3%. Stretch above 5% does not reliably mean worse forward returns, and below 3% does not reliably mean better. Across 8 bucket-difference tests, the one IS hit is consistent with chance.
- **The setup itself:** universe-wide it trails SPY OOS under both exits. That is consistent with [[bt-2026-10-20-exits-holding-period]] (Stage 2 entries lag SPY).

## Caveats

- Survivorship: SP1500-now uses today's names, and the PIT S&P 500 cannot add names that have since left the index. The data ends 2026-09-21.
- Prices are adjusted daily closes and opens, with no intrabar fills. Signals from the same week overlap heavily; the bootstrap clusters by week but not by ticker.
- The exits are my pre-registered choices, because the sleeve has none. A different exit could change the picture.

## Relative rerun (2026-10-21, later the same day)

The rule, events, STATE/T20 exits, and 0.34% costs are unchanged. Prices were refreshed read-only with yfinance (adjusted, 2013-01 to the 2026-10-21 intraday bar), which is why n is larger than in the cache run above. Every benchmark is measured over the identical window (entry open to exit fill), net of the same cost.

- **Sector ETFs:** ALL vs KIE and XLF; BIIB vs XBI and IBB.
- **Peers (equal weight):** ALL vs PGR, TRV, CB, HIG, AIG. BIIB vs AMGN, GILD, REGN, VRTX, ALNY.
- **Alpha:** 252-day pre-entry OLS on SPY and (sector ETF - SPY), using KIE and XBI.
- **Shrinkage:** empirical Bayes toward the universe weekly-swing pool (80,133 rows, 1,473 tickers, signals 2015-10 to 2026-09, SP1500-now, today's sector map), with kappa = s^2 / tau^2 floored at 10. Bootstrap CI: 2,000 draws, trades nearly one per week. Script: `experiments/backtester/weekly_swing_relative.py`.

**Current state (bar 2026-10-21, intraday):** ALL stretch +1.85% (close 250.14 vs SMA50 245.60). BIIB stretch +9.0% (241.67 vs 221.69). Both are **triggered** on the last completed week (2026-10-16).

| Ticker | Exit | n | Raw mean (hit) | x SPY | x sector 1 | x sector 2 | x peers | Alpha | Shrunk x SPY (w, pool) | Flags |
|---|---|---:|---|---|---|---|---|---|---|---|
| ALL | STATE | 44 | +1.06% (41%) | -0.76% [-2.4, +0.9] | KIE +0.58% [-0.7, +1.8] | XLF -0.22% [-1.6, +1.2] | +0.71% [-0.3, +1.8] | +0.74% [-0.7, +2.1] | **-0.65%** (0.62, pool -0.47%) | none |
| ALL | T20 | 44 | -0.43% (52%) | -0.88% [-2.5, +0.6] | KIE -0.39% [-1.4, +0.5] | XLF -0.83% [-2.3, +0.5] | -0.26% [-1.2, +0.6] | -0.33% [-1.3, +0.6] | **-0.72%** (0.82, pool -0.03%) | none |
| BIIB | STATE | 28 | +0.11% (29%) | -0.22% [-2.9, +3.1] | XBI -1.85% [-4.4, +0.3] | IBB -0.89% [-2.9, +1.1] | -0.13% [-3.0, +2.8] | -1.14% [-3.1, +0.9] | **-0.34%** (0.51, pool -0.47%) | insufficient_sample |
| BIIB | T20 | 29 | +0.62% (48%) | +1.07% [-1.4, +3.8] | XBI +0.24% [-2.5, +2.7] | IBB +0.20% [-1.9, +2.4] | -0.59% [-2.8, +1.8] | +0.66% [-1.5, +3.0] | **+0.79%** (0.74, pool -0.03%) | insufficient_sample |

Brackets are 95% CIs in percentage points. The average betas are ALL 0.84 to SPY and 0.90 to KIE-SPY, and BIIB 0.92 / 0.49. Shrunk excess vs the SPDR sector ETF: ALL STATE -0.13% (XLF), ALL T20 -0.65%, BIIB STATE -0.60% (XLV), BIIB T20 +0.23%.

**Stretch on relative terms (pooled, >5% minus <3%, mean excess per trade):**

| Exit | Slice | vs SPY: 95% CI (P>0) | vs SPDR sector: 95% CI (P>0) |
|---|---|---|---|
| STATE | IS | [+0.01, +1.33] pp (0.98) | [+0.21, +1.48] pp (1.00) |
| STATE | OOS | [-0.65, +0.72] pp (0.55) | [-0.24, +1.15] pp (0.90) |
| T20 | IS | [-0.13, +1.20] pp (0.94) | [+0.11, +1.30] pp (0.99) |
| T20 | OOS | [-0.38, +1.08] pp (0.83) | [-0.22, +0.86] pp (0.88) |

**Verdict:**

- **ALL and BIIB:** no evidence of edge on any relative basis. Every 95% CI spans zero except BIIB STATE vs XBI, which is negative (P>0 0.046) and is one of about 24 single-ticker tests, so treat it as noise. The shrunk values sit near the pool: ALL about -0.7% vs SPY per trade, BIIB -0.3% to +0.8%. BIIB is flagged `insufficient_sample` (n < 30).
- **Stretch:** above 5% is not worse than below 3% on relative terms. It tends slightly better in sample, significantly so vs the sector ETF, but the difference is not significant OOS. BIIB's 9% stretch is no reason to avoid it, and ALL's 1.85% is no reason to prefer it, on this evidence.

**Caveats:**

- The 2026-10-21 bar is intraday, so the current stretch will move by the close.
- The pool is SP1500-now with today's sector map, so it is not point-in-time and has survivorship bias. The peer baskets are today's names and were not market-cap matched point-in-time.
- yfinance adjusted prices differ slightly from the Schwab cache, so n differs from the run above.
- There are many tests (2 tickers x 2 exits x 6 metrics, plus 8 stretch diffs) and no correction was applied, so read the single P-values loosely.

## Related Pages

- [[horizon-strategies]] - sleeve definitions, including `weekly_swing`
- [[scan-catalog]] - catalog row for `weekly_swing` (research)
- [[scan-confidence]] - earlier evidence that extension predicts early stop-outs on the daily book
- [[bt-2026-10-20-exits-holding-period]] - entries lag SPY at 5-120 days
- [[backtest]] - cost model

---

*Last compiled: 2026-10-21*
