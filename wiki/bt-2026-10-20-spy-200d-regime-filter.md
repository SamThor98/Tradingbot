---
source: experiments/backtester/results_r3_regime_2026-10-20.json
created: 2026-10-20
updated: 2026-10-20
tags: [backtest, research, regime, spy]
---

# SPY 200-day regime filter (2026-10-20)

> Opening breakouts only when SPY closed above its 200-day SMA (checked at the prior close) does not help the book. Parking idle cash in SPY lifts CAGR, but no variant beats SPY buy-and-hold out of sample. Killed.

## Pre-registration

- **Hypothesis:** breakout trades opened while SPY is below its 200-day SMA drag the book down, so filtering them improves PF and risk-adjusted return.
- **Signal:** regime on day d = SPY close(d-1) > SMA200 of closes through d-1. There is no lookahead. Book entries are at the next-day open (see [[bt-2026-10-20-crowded-breakout-days]]).
- **Variants (declared in advance):** (a) filter on, idle cash earns 0. (b) Filter on, idle cash and unused slots held in SPY, with 2 bps on each dollar moved between SPY and trades. (c) SPY timing alone: hold SPY while the regime is on, trading at the open, 5 bps per side. **Robustness checks:** (a) with a 150-day and a 250-day SMA. That is 5 variants. SPY buy-and-hold and the unfiltered book are references, not variants.
- Split, costs, PIT S&P 500 universe, and random tie-breaks over 10 seeds are as in [[bt-2026-10-20-next-open-gap-skip]]. Significance vs SPY uses a 21-day block bootstrap (2,000 draws) of daily returns: the Sharpe difference and the annualized excess return. PF significance is the week-block bootstrap vs the unfiltered next-open book.
- Script: `experiments/backtester/round3.py`.

## Finding before the result

**12.2% of the book's entries happen while SPY is below its 200-day SMA** (11.7% at 150d, 11.6% at 250d). The harness's climate gate is not a strict SPY-200 entry filter in this book. The regime flipped 31 times in-sample and 30 times out-of-sample, which is whipsaw-heavy.

## Result

SPY buy-and-hold: IS CAGR 16.8%, Sharpe 0.96, MDD -33.7%. OOS 12.0%, 0.73, -24.5%.

| Universe | Variant | Slice | Trades | PF mean / worst | Win % | CAGR (seed SD) | Sharpe | MDD | Exposure (trades) | PF gain vs base 95% CI | Sharpe minus SPY 95% CI (P>0) | Excess ann. return vs SPY 95% CI |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| SP1500 | no filter | IS | 9,928 | 1.218 / 1.043 | 52.5 | 5.8% (1.9) | 0.45 | -20.8% | 73% | - | [-1.34, +0.32] (0.13) | [-21%, +1%] |
| SP1500 | no filter | OOS | 6,505 | 1.059 / 1.055 | 47.7 | -0.9% (4.3) | 0.02 | -32.4% | 64% | - | [-1.59, +0.15] (0.05) | [-26%, +1%] |
| SP1500 | (a) 200d, cash 0 | IS | 8,914 | 1.215 / 1.022 | 52.2 | 6.8% (2.8) | 0.54 | -16.4% | 68% | [-0.06, +0.10] | [-1.33, +0.44] (0.18) | [-21%, +2%] |
| SP1500 | (a) 200d, cash 0 | OOS | 5,520 | 1.003 / **0.965** | 46.7 | -2.4% (2.2) | -0.09 | -31.7% | 56% | [-0.13, +0.04] | [-1.80, +0.08] (0.04) | [-29%, -1%] |
| SP1500 | (b) 200d, cash in SPY | IS | 8,914 | same as (a) | 52.2 | 11.7% (3.0) | 0.64 | -34.2% | 68% | - | [-0.73, +0.02] (0.03) | [-10%, +2%] |
| SP1500 | (b) 200d, cash in SPY | OOS | 5,520 | same as (a) | 46.7 | 3.4% (2.1) | 0.26 | -27.3% | 56% | - | [-0.97, 0.00] (0.03) | [-16%, +1%] |
| SP1500 | (a) 150d robust | OOS | 5,549 | 0.976 / 0.892 | 46.3 | -0.9% | 0.01 | -26.6% | 57% | [-0.14, +0.02] | (0.06) | - |
| SP1500 | (a) 250d robust | OOS | 5,496 | 1.055 / 1.032 | 47.0 | -1.7% | -0.05 | -30.9% | 56% | [-0.08, +0.06] | (0.05) | - |
| PIT S&P 500 | no filter | IS | 2,859 | 1.265 / 1.100 | 54.3 | 7.0% | 0.64 | -13.1% | 72% | - | (0.24) | - |
| PIT S&P 500 | no filter | OOS | 1,966 | 1.048 / 1.025 | 47.7 | 3.1% | 0.30 | -23.0% | 62% | - | [-1.27, +0.37] (0.15) | [-22%, +4%] |
| PIT S&P 500 | (a) 200d, cash 0 | IS | 2,506 | 1.254 / 1.060 | 53.9 | 5.3% | 0.53 | -12.0% | 65% | [-0.10, +0.12] | (0.16) | - |
| PIT S&P 500 | (a) 200d, cash 0 | OOS | 1,637 | 0.963 / **0.840** | 46.7 | 0.2% | 0.07 | -21.6% | 54% | [-0.16, +0.06] | [-1.57, +0.20] (0.06) | [-25%, +1%] |
| PIT S&P 500 | (b) 200d, cash in SPY | IS | 2,506 | same as (a) | 53.9 | 11.7% | 0.68 | -33.7% | 65% | - | (0.03) | - |
| PIT S&P 500 | (b) 200d, cash in SPY | OOS | 1,637 | same as (a) | 46.7 | 5.0% | 0.35 | -27.0% | 54% | - | [-0.82, -0.01] (0.02) | [-13%, +1%] |
| PIT S&P 500 | (a) 150d / 250d robust | OOS | 1,638 / 1,645 | 0.936 / 0.757 ; 0.997 / 0.969 | 46.5 | -0.2% / 0.1% | 0.04 / 0.07 | - | 54% | not significant | (0.06 / 0.08) | - |
| SPY only | (c) SPY timing 200d | IS | 31 switches | - | - | 10.8% | 0.89 | -19.5% | 88% in SPY | - | [-0.59, +0.42] (0.34) | [-13%, +1%] |
| SPY only | (c) SPY timing 200d | OOS | 31 switches | - | - | 8.4% | 0.77 | -19.5% | 76% in SPY | - | [-0.67, +0.74] (0.52) | [-15%, +6%] |

## Verdict

**Kill the regime filter.** It does not raise PF: OOS PF falls in both universes, and the worst era drops below 1.0 (bear_rates 0.965 SP1500, 0.840 PIT). None of the PF changes are significant, and the 150d/250d checks show no robust benefit. Parking idle cash in SPY (b) lifts CAGR by about 5-6 points, but that is SPY beta, not breakout edge. It still trails buy-and-hold, significantly so on Sharpe OOS in the PIT universe. SPY timing (c) gives Sharpe roughly equal to buy-and-hold OOS (0.77 vs 0.73, P 0.52) with a shallower drawdown and lower CAGR. **No variant beats SPY buy-and-hold on CAGR or Sharpe out of sample with significance.**

## Caveats

- Survivorship: the SP1500 rows use today's constituents. The PIT S&P 500 filter cannot restore names that have since been removed from the index.
- About 30 regime switches per window means whipsaw. Costs are 5 bps per SPY side for (c) and 2 bps per dollar moved for (b), and no taxes are modeled.
- Portfolio CAGR seed SD is 2-4 points. The OOS window is 4.5 years and two eras.

## Related Pages

- [[backtest]] - harness, cost model, and the SPY climate gate
- [[plugin-modes]] - regime v2 is the live regime control; this offline test does not touch it
- [[thirty-day-signal-cut-plan]] - live stack frozen; offline research only
- [[pit-research-platform]] - PIT S&P 500 membership
- [[bt-2026-10-20-crowded-breakout-days]] / [[bt-2026-10-20-next-open-gap-skip]] / [[bt-2026-10-20-wide-stop-skip]] - round 2 tests
- [[bt-2026-10-20-entry-bar-close-location]] / [[bt-2026-10-20-day5-failure-exit]] - round 1 tests

---

*Last compiled: 2026-10-20*
