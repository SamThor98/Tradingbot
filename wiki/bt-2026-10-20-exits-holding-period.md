---
source: experiments/backtester/results_r4_exits_2026-10-20.json
created: 2026-10-20
updated: 2026-10-20
tags: [backtest, research, exits, holding-period]
---

# Exits and holding period (2026-10-20)

> Holding the same entries longer, or trailing them with a wide 5x ATR stop, raises profit factor a lot. Nothing beats SPY on Sharpe out of sample, and the entries trail SPY at every horizon from 5 to 120 days. The signal shows no stock-selection edge; longer holds mostly collect market beta.

## Pre-registration

- **Entries fixed:** the same 16,433 `control_legacy_aug` signals, entered at the next-day open with each trade's own slippage and fees. 16,242 have cached bars, and all variants use those trades.
- **Prices:** read-only `schwab_skill/validation_artifacts/fast_slope/price_cache/*.parquet` (adjusted daily OHLCV, 1,479 tickers).
- **Variants (8 rows, 7 alternatives to the baseline):**
  - B0: the book's original exits, from the chunk ledger.
  - B1: the original exit date, re-priced at the cache close. This is the reproduction check and the like-for-like baseline.
  - T20, T60, T120: time exits with no stop.
  - ATR3, ATR5: exit at the next open after the close falls below the highest close since entry minus k x ATR14.
  - SMA50: exit at the next open after a close below the 50-day SMA.
  - Trails and the SMA exit are capped at 250 bars.
- **Exit grace 15/40:** not re-implemented here. The harness only supports it as an offline counterfactual on the chunk ledger: PF 1.3250 / worst 1.1283 on 16,241 trades, per [[vcp-invalidation-exit]].
- **Split and tests:** in-sample 2015-10 to 2021, out-of-sample 2022 to 2026-06, on both SP1500-now and the PIT S&P 500 (see [[pit-research-platform]]). Same-day ties are random over 10 seeds. PF uses a week-block bootstrap vs B0. Sharpe vs SPY uses a 21-day block bootstrap of daily returns.
- **Multiple testing:** 7 alternatives x 2 universes. Bonferroni at 0.05/7 is about 0.007 per test.
- Script: `experiments/backtester/round4.py`.

## Edge decay (all covered entries, net of costs, vs SPY open-to-close over the same days)

| Horizon | IS mean net | IS SPY | IS excess (95% CI) | OOS mean net | OOS SPY | OOS excess (95% CI) | OOS hit vs SPY |
|---|---:|---:|---|---:|---:|---|---:|
| 5d | -0.10% | 0.31% | -0.41% [-0.54, -0.27] | -0.20% | 0.27% | -0.47% [-0.69, -0.24] | 44% |
| 10d | 0.29% | 0.65% | -0.36% [-0.57, -0.16] | 0.03% | 0.54% | -0.51% [-0.82, -0.20] | 46% |
| 20d | 1.12% | 1.45% | -0.33% [-0.64, -0.04] | 0.59% | 1.37% | -0.78% [-1.22, -0.36] | 43% |
| 60d | 3.69% | 3.89% | -0.20% [-0.81, +0.38] | 2.12% | 4.45% | -2.33% [-3.13, -1.51] | 40% |
| 120d | 6.27% | 6.85% | -0.58% [-1.31, +0.11] | 7.16% | 9.36% | -2.19% [-3.68, -0.65] | 40% |

The entries **underperform SPY at every horizon**, and significantly so at every OOS horizon. Most of the gain from holding longer is market drift.

## Results

SPY buy-and-hold: IS CAGR 16.8%, Sharpe 0.96, MDD -33.7%. OOS 12.0%, 0.73, -24.5%. P>0 is the probability that the variant's Sharpe beats SPY.

| Universe | Variant | Slice | n | PF mean / worst | Win % | Avg hold (d) | Port trades | CAGR (seed SD) | Sharpe | MDD | Exposure | PF gain vs B0 95% CI | Sharpe vs SPY 95% CI (P>0) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| SP1500 | B0 original | IS | 9,798 | 1.221 / 1.045 | 52.5 | 14 | 868 | 6.5% (2.8) | 0.49 | -20.6% | 73% | - | [-1.30, +0.39] (0.15) |
| SP1500 | B0 original | OOS | 6,444 | 1.058 / 1.053 | 47.7 | 14 | 584 | 1.0% (2.9) | 0.14 | -31.5% | 64% | - | [-1.43, +0.22] (0.08) |
| SP1500 | B1 cache-priced | OOS | 6,444 | 1.079 / 1.073 | 48.2 | 14 | 584 | 1.8% | 0.19 | -30.5% | 64% | [+0.017, +0.026] | (0.11) |
| SP1500 | T20 | OOS | 6,444 | 1.150 / 1.118 | 50.6 | 20 | 390 | 0.5% | 0.11 | -33.5% | 64% | [-0.00, +0.23] | (0.07) |
| SP1500 | T60 | OOS | 6,444 | 1.399 / 1.252 | 52.6 | 60 | 140 | 3.4% | 0.27 | -24.3% | 68% | [+0.08, +0.53] | (0.13) |
| SP1500 | T120 | IS | 9,798 | 2.354 / 1.220 | 62.9 | 120 | 110 | 14.2% (5.5) | 0.60 | -49.7% | 80% | [+0.58, +1.42] | (0.12) |
| SP1500 | T120 | OOS | 6,444 | 2.188 / 1.815 | 58.1 | 117 | 70 | 8.8% (3.6) | 0.57 | -23.8% | 69% | [+0.67, +1.40] | [-1.01, +0.75] (0.36) |
| SP1500 | ATR3 | IS | 9,798 | 1.591 / 1.210 | 43.4 | 42 | 282 | 14.6% (3.7) | 0.99 | -18.5% | 77% | [+0.24, +0.56] | (0.62) |
| SP1500 | ATR3 | OOS | 6,444 | 1.144 / 1.069 | 36.1 | 36 | 227 | 2.3% | 0.22 | -24.7% | 67% | [-0.07, +0.20] | (0.12) |
| SP1500 | ATR5 | IS | 9,798 | 1.885 / 1.259 | 46.6 | 96 | 138 | 12.9% | 0.68 | -38.3% | 82% | [+0.47, +0.99] | (0.23) |
| SP1500 | ATR5 | OOS | 6,444 | 1.675 / 1.474 | 39.9 | 80 | 109 | 6.0% (4.7) | 0.44 | -22.4% | 71% | [+0.33, +0.81] | [-1.04, +0.58] (0.30) |
| SP1500 | SMA50 | IS | 9,798 | 1.504 / 1.110 | 37.4 | 35 | 370 | 13.6% | 0.92 | -18.1% | 77% | [+0.16, +0.48] | (0.54) |
| SP1500 | SMA50 | OOS | 6,444 | 1.090 / 1.074 | 31.5 | 30 | 295 | 0.3% | 0.10 | -28.5% | 67% | [-0.11, +0.18] | (0.06) |
| PIT S&P 500 | B0 original | OOS | 1,960 | 1.048 / 1.025 | 47.7 | 14 | 544 | 4.9% | 0.42 | -23.5% | 62% | - | (0.24) |
| PIT S&P 500 | T20 | OOS | 1,960 | 1.183 / 1.151 | 50.7 | 20 | 378 | 7.0% | 0.55 | -20.3% | 63% | [-0.01, +0.31] | (0.34) |
| PIT S&P 500 | T60 | OOS | 1,960 | 1.485 / 1.292 | 54.2 | 60 | 135 | 1.6% | 0.18 | -19.9% | 65% | [+0.03, +0.69] | (0.09) |
| PIT S&P 500 | T120 | OOS | 1,960 | 2.712 / 2.179 | 61.4 | 117 | 70 | 3.9% (1.2) | 0.36 | -16.0% | 69% | [+0.98, +1.98] | (0.20) |
| PIT S&P 500 | ATR3 | OOS | 1,960 | 1.064 / 0.970 | 35.0 | 35 | 220 | 4.0% | 0.35 | -19.2% | 67% | [-0.22, +0.18] | (0.20) |
| PIT S&P 500 | ATR5 | IS | 2,842 | 2.068 / 1.689 | 48.8 | 99 | 134 | 11.8% (1.9) | 0.92 | -17.8% | 81% | [+0.52, +1.15] | (0.49) |
| PIT S&P 500 | **ATR5** | **OOS** | 1,960 | **1.961 / 1.673** | 41.5 | 83 | 101 | **11.7% (3.3)** | **0.87** | -16.2% | 72% | [+0.44, +1.21] | **[-0.63, +1.03] (0.69)** |
| PIT S&P 500 | SMA50 | OOS | 1,960 | 1.062 / 1.001 | 31.5 | 31 | 272 | 5.3% | 0.41 | -19.8% | 67% | [-0.21, +0.19] | (0.24) |

The full per-era and IS rows are in the JSON.

## Verdict

- **No variant beats SPY on Sharpe or CAGR OOS with significance.** The best is ATR5 on the PIT S&P 500: OOS Sharpe 0.87 vs SPY 0.73 and CAGR 11.7% vs 12.0%, with P(Sharpe > SPY) 0.69 and a CI spanning zero.
- **PF does not compare across holding periods.** Longer holds raise PF mechanically, because more drift is captured per trade. T120 and ATR5 PF gains survive Bonferroni, but the edge-decay table shows those trades still lag SPY over the same days.
- **ATR5: inconclusive, worth a shadow look.** It is the only exit that improves OOS PF, drawdown, and Sharpe together in both universes. The improvement is relative to the current exits, not to SPY. It would need the [[hypothesis-cards]] path after the [[thirty-day-signal-cut-plan]] freeze.
- **Kill:** T20, T60, ATR3, SMA50. Each has no significant OOS gain over B0 after correction, or loses on Sharpe or worst era. T120 is killed because MDD reaches -50% IS and only about 70 portfolio trades are taken per window.
- **Biggest finding:** the Stage 2/VCP entries do not beat SPY over 5-120 days out of sample. The book has no stock-selection alpha, so any edge would have to come from exits and risk control.

## Caveats

- **Price source:** B1 vs B0 shows that re-pricing on cache closes adds +0.02-0.05 PF, because closes stand in for intrabar stop fills. All cache-priced variants carry that optimism, so compare them to B1, not B0.
- **Survivorship:** both universes carry some, and delisted names are missing. Truncated holds at the data end: T120 562, ATR5 239, others 33 or fewer.
- **Small samples:** long-hold portfolios take only 70-140 trades per window, so CAGR and Sharpe are noisy (seed SD up to 5.5 points).
- **Multiple testing:** about 14 tests per metric. Only the long-hold PF gains clear Bonferroni.

## Related Pages

- [[backtest]] - harness and costs
- [[vcp-invalidation-exit]] - exit grace 15/40 counterfactual numbers
- [[adaptive-stops]] - the live ATR stop multiples these trails widen
- [[scan-confidence]] - earlier evidence that holds of 21-40 days carry the book
- [[bt-2026-10-20-spy-200d-regime-filter]] - round 3 regime test
- [[bt-2026-10-20-day5-failure-exit]] - round 1 early-exit test
- [[thirty-day-signal-cut-plan]] - live stack frozen; offline research only

---

*Last compiled: 2026-10-20*
