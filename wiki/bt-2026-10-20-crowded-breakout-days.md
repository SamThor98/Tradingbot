---
source: experiments/backtester/results_2026-10-20.json
created: 2026-10-20
updated: 2026-10-20
tags: [backtest, research, regime]
---

# Skip crowded breakout days (2026-10-20)

> Skipping signal days with many simultaneous breakouts. Round 1 was inconclusive. Round 2 used next-open entries and a PIT universe, and the idea is now killed: not significant, not threshold-robust, and it fails on the point-in-time S&P 500.

## Pre-registration

- **Hypothesis:** days when many SP1500 names break out together are late-stage herding days, and those trades underperform.
- **Rule:** skip every entry on a date whose count of book entries is above the in-sample (pre-2022) 80th percentile of daily counts. That threshold is 12.
- **Params:** p80, fixed in advance and computed in-sample only. 1 variant.
- **Lookahead flag:** the same-day count is known only at that day's close, across the whole universe. Live, it is the end-of-day scan count. That is usable for next-open entries but not for intraday entries. The book enters at the signal-day close, so this test carries mild same-bar lookahead.

## Setup (shared by all three 2026-10-20 tests)

- Book: frozen `control_legacy_aug` five-era trade chunks (16,433 trades, SP1500, Schwab bars). Baseline reproduces the catalog: PF mean 1.1637 / worst era 1.0322.
- Costs: `net_return` already carries 15 bps/side slippage plus per-share fees (`slippage_pct` + `fees_pct`). Any re-priced exit pays the same costs.
- Split: in-sample = late_bull, volatility_chop, crash_recovery (2015-2021). Out-of-sample = bear_rates, recent_current (2022 to 2026-06-30). Rules were fixed before the run, so OOS is a stability check, not a refit.
- Significance: week-block bootstrap (2,000 draws) of PF(arm) minus PF(base) on the same weeks.
- Portfolio: daily mark-to-market, 10 slots at 10% of NAV, same-day ties by `signal_score` descending. SPY is total-return-adjusted (yfinance) over the same calendar window.
- Script: `experiments/backtester/overlay_tests.py`. Output: `experiments/backtester/results_2026-10-20.json`. Offline only. No env, scanner, or order change.
- Caveats: the universe is today's SP1500 list (names like ABNB and APP show up in early eras), so it has survivorship bias. The book has 16k trades for 10 slots, so portfolio CAGR mostly reflects tie-break order. Use PF for the verdict. Portfolio and SPY windows start 2015-01-01; the first trade is 2015-10-16.

## Result

| Slice | Arm | n | PF mean | Worst era | Win % | CAGR | Sharpe | MDD | Exposure |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| IS | base | 9,928 | 1.2443 | 1.0497 | 53.0 | 2.1% | 0.23 | -15.6% | 65% |
| IS | skip crowded | 6,175 | 1.2472 | 1.0555 | 52.9 | 2.5% | 0.26 | -21.9% | 64% |
| OOS | base | 6,505 | 1.0427 | 1.0322 | 47.6 | 0.9% | 0.13 | -29.2% | 64% |
| OOS | skip crowded | 3,670 | 1.1023 | 1.0789 | 48.2 | -3.6% | -0.17 | -30.3% | 61% |

SPY is the same as on the sibling pages. Per-era OOS PF: bear_rates 1.126 (base 1.032), recent_current 1.079 (base 1.053). Bootstrap PF diff: IS [-0.104, +0.103] (p 0.50). OOS [-0.063, +0.171] (p 0.19).

## Verdict

**Inconclusive.** The OOS PF lift looks right and the worst era improves, but the 95% CI includes zero, and the 10-slot portfolio does worse OOS. Removing 40% of trades changes which names fill the slots. The same-bar lookahead flag above also applies. Worth a formal hypothesis card using next-day entry timing after the 30-day freeze. Not a live change.

## Round 2 addendum (2026-10-20): next-day-open entry, threshold robustness, PIT universe

The pre-registered rule is the same: skip signal days whose entry count is above the in-sample p80, which is 12. p70 (10) and p90 (15) are **robustness checks, not refits**. That is 3 variants, with p80 as the primary. Entry is at the next-day open, so the signal-day count is known before entry. This removes the same-bar lookahead flagged above. Setup is the same as in [[bt-2026-10-20-next-open-gap-skip]]: random tie-breaks and a PIT S&P 500 check.

| Universe | Arm | Slice | n | PF mean / worst | Win % | CAGR (seed SD) | Sharpe | MDD | Exposure | Boot PF diff 95% CI (p<=0) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| SP1500 | next-open base | IS | 9,928 | 1.218 / 1.043 | 52.5 | 5.8% (1.9) | 0.44 | -20.9% | 73% | - |
| SP1500 | next-open base | OOS | 6,505 | 1.059 / 1.055 | 47.7 | -0.9% (4.3) | 0.02 | -32.4% | 64% | - |
| SP1500 | **p80** | IS | 6,175 | 1.217 / 1.037 | 52.5 | 4.6% (1.4) | 0.38 | -19.8% | 72% | [-0.109, +0.095] (0.52) |
| SP1500 | **p80** | OOS | 3,670 | 1.112 / 1.069 | 48.2 | -2.0% (2.9) | -0.05 | -31.6% | 62% | [-0.063, +0.144] (0.22) |
| SP1500 | p70 | OOS | 2,916 | 1.086 / 1.082 | 47.6 | -3.5% | -0.16 | -34.4% | 61% | [-0.098, +0.155] (0.34) |
| SP1500 | p90 | OOS | 4,747 | 1.031 / 0.993 | 47.6 | -2.2% | -0.06 | -32.6% | 63% | [-0.101, +0.061] (0.62) |
| PIT S&P 500 | next-open base | OOS | 1,966 | 1.048 / 1.025 | 47.7 | 3.1% | 0.29 | -23.0% | 62% | - |
| PIT S&P 500 | **p80** | IS | 1,725 | 1.284 / 1.111 | 54.6 | 7.2% | 0.69 | -13.3% | 67% | [-0.130, +0.187] (0.37) |
| PIT S&P 500 | **p80** | OOS | 1,077 | 1.048 / 0.981 | 47.2 | 3.2% | 0.31 | -19.5% | 57% | [-0.125, +0.165] (0.39) |
| PIT S&P 500 | p70 / p90 | OOS | 852 / 1,384 | 1.056 / 0.945 ; 0.991 / 0.876 | - | 2.6% / 0.3% | - | - | - | not significant |

**Verdict, updated: Kill.** The round 1 OOS lift mostly survives next-open entry on SP1500: PF 1.112 vs 1.059 at p80. It is still not significant (p 0.22). It is not monotonic in the threshold, since p90 is worse than the base OOS. It also does not hold on the point-in-time S&P 500: OOS bear_rates PF is 0.98 at p80 and 0.88 at p90, which breaks the worst-era floor. The 10-slot portfolio never gains CAGR. Variants tried for this idea across both rounds: 1 primary at same-bar entry, plus 1 primary and 2 robustness thresholds at next-open entry.

## Related Pages

- [[backtest]] - harness and cost model behind the trade book
- [[pit-research-platform]] - PIT S&P 500 membership used in the round 2 check
- [[bt-2026-10-20-next-open-gap-skip]] / [[bt-2026-10-20-wide-stop-skip]] - round 2 siblings
- [[thirty-day-signal-cut-plan]] - live stack stays frozen; this is offline research only
- [[scan-confidence]] - earlier work on the same control book
- [[hypothesis-cards]] - the path to queue a formal card if this is ever revisited
- [[bt-2026-10-20-entry-bar-close-location]] / [[bt-2026-10-20-crowded-breakout-days]] / [[bt-2026-10-20-day5-failure-exit]] - sibling tests from the same session

---

*Last compiled: 2026-10-20*

