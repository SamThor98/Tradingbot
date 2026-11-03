---
source: experiments/backtester/results_r2_SP1500_now_2026-10-20.json
created: 2026-10-20
updated: 2026-10-20
tags: [backtest, research, risk]
---

# Skip wide initial stops (2026-10-20)

> Dropping trades whose initial stop is wider than the in-sample 80th percentile (7.3%) hurts out of sample, significantly so on the point-in-time S&P 500. Killed.

## Pre-registration

- **Hypothesis:** a wide initial stop marks a loose, volatile base, and those trades have worse expectancy.
- **Rule:** skip a trade when `stop_pct` is above the in-sample 80th percentile: 7.28% on SP1500, 5.61% on the PIT S&P 500.
- **Params:** p80, fixed in advance. 1 variant.

## Setup (round 2, 2026-10-20)

- Book: frozen `control_legacy_aug` chunks, 16,433 trades. Entry is re-priced at the **next-day open**, so there is no same-bar lookahead. The exit is unchanged. Costs are the trade's own 15 bps/side slippage plus fees. No trades were lost.
- Split: in-sample = 2015-10-16 to 2021-12-31 (late_bull, volatility_chop, crash_recovery). Out-of-sample = 2022 to 2026-06-30 (bear_rates, recent_current). Thresholds come from in-sample data only.
- Significance: week-block bootstrap (2,000 draws) of PF(arm) minus PF(next-open base).
- Portfolio: 10 slots at 10% of NAV with daily mark-to-market. Same-day ties are broken **randomly**, averaged over 10 seeds; the CAGR seed SD is reported. SPY is adjusted close over the same window.
- **Survivorship check:** the trades were filtered to the point-in-time S&P 500 using Sharadar `sp500` membership, taken as of the quarter start before each entry (`experiments/backtester/pit_sp500_quarterly.json`, 4,825 trades). This removes names that joined the index later. It cannot add names that were in the index then and have since left, because the book never fetched their bars. It covers the S&P 500 only, not the 1500.
- Script: `experiments/backtester/round2.py`. Outputs: `results_r2_SP1500_now_2026-10-20.json` and `results_r2_SP500_PIT_2026-10-20.json`. Offline only.
- Next-open base, SP1500: IS PF 1.218 / worst 1.043, CAGR 5.8%, Sharpe 0.44, MDD -20.9%, exposure 73%. OOS PF 1.059 / 1.055, CAGR -0.9%, Sharpe 0.02, MDD -32.4%, exposure 64%. SPY: IS 16.8% / Sharpe 0.96 / MDD -33.7%. OOS 11.8% / 0.72 / -24.5%.
- Next-open base, PIT S&P 500: IS PF 1.265 / 1.100, CAGR 7.0%. OOS PF 1.048 / 1.025, CAGR 3.1%.

## Result

| Universe | Slice | n | PF mean / worst | Win % | CAGR (seed SD) | Sharpe | MDD | Exposure | Boot PF diff 95% CI (p<=0) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| SP1500 now | IS | 7,942 | 1.195 / 1.075 | 52.4 | 5.6% (3.0) | 0.48 | -16.4% | 73% | [-0.097, +0.040] (0.78) |
| SP1500 now | OOS | 4,446 | 1.013 / 0.946 | 46.9 | 0.6% (2.7) | 0.11 | -24.5% | 63% | [-0.146, +0.034] (0.90) |
| PIT S&P 500 | IS | 2,287 | 1.230 / 1.101 | 54.1 | 5.8% | 0.58 | -12.2% | 69% | [-0.139, +0.078] (0.71) |
| PIT S&P 500 | OOS | 1,219 | 0.879 / 0.847 | 45.9 | -0.3% | 0.03 | -17.7% | 58% | [-0.322, -0.045] (0.99) |

## Verdict

**Kill.** Wide-stop trades carry the edge. Removing them breaks the worst-era floor OOS in both universes, and the harm is significant on the PIT S&P 500. Drawdown falls only because exposure falls.

## Related Pages

- [[backtest]] - harness and cost model behind the trade book
- [[pit-research-platform]] - Sharadar point-in-time membership used for the survivorship check
- [[thirty-day-signal-cut-plan]] - live stack stays frozen; offline research only
- [[bt-2026-10-20-crowded-breakout-days]] / [[bt-2026-10-20-next-open-gap-skip]] / [[bt-2026-10-20-wide-stop-skip]] - round 2 sibling tests
- [[bt-2026-10-20-entry-bar-close-location]] / [[bt-2026-10-20-day5-failure-exit]] - round 1 tests

---

*Last compiled: 2026-10-20*
