---
source: experiments/backtester/results_r2_SP1500_now_2026-10-20.json
created: 2026-10-20
updated: 2026-10-20
tags: [backtest, research, entry-timing]
---

# Skip next-open gap-ups over 2% (2026-10-20)

> With next-day-open entries, skipping names that gap up more than 2% removes 2% of trades and changes nothing. Killed.

## Pre-registration

- **Hypothesis:** chasing a breakout that gaps up overnight pays a worse entry, and those trades underperform.
- **Rule:** skip a trade when next open / signal close - 1 is more than 2%.
- **Params:** 2%, fixed in advance. 1 variant.

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
| SP1500 now | IS | 9,773 | 1.221 / 1.040 | 52.5 | 6.6% (2.5) | 0.50 | -21.1% | 73% | [-0.012, +0.019] (0.35) |
| SP1500 now | OOS | 6,334 | 1.049 / 1.038 | 47.4 | 0.6% (4.7) | 0.11 | -30.3% | 64% | [-0.032, +0.006] (0.91) |
| PIT S&P 500 | IS | 2,837 | 1.266 / 1.095 | 54.2 | 6.5% | 0.59 | -13.2% | 72% | [-0.012, +0.015] |
| PIT S&P 500 | OOS | 1,931 | 1.035 / 1.028 | 47.4 | 2.8% | 0.27 | -22.0% | 62% | [-0.043, +0.004] (0.94) |

## Verdict

**Kill.** Only about 330 trades gap more than 2%. OOS PF goes slightly down in both universes. The CAGR changes are smaller than the seed noise.

## Related Pages

- [[backtest]] - harness and cost model behind the trade book
- [[pit-research-platform]] - Sharadar point-in-time membership used for the survivorship check
- [[thirty-day-signal-cut-plan]] - live stack stays frozen; offline research only
- [[bt-2026-10-20-crowded-breakout-days]] / [[bt-2026-10-20-next-open-gap-skip]] / [[bt-2026-10-20-wide-stop-skip]] - round 2 sibling tests
- [[bt-2026-10-20-entry-bar-close-location]] / [[bt-2026-10-20-day5-failure-exit]] - round 1 tests

---

*Last compiled: 2026-10-20*
