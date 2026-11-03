---
source: experiments/backtester/results_2026-10-20.json
created: 2026-10-20
updated: 2026-10-20
tags: [backtest, research, entry-timing]
---

# Entry-bar close location filter (2026-10-20)

> Keeping only breakouts that close in the upper half of the entry bar range does not hold up out of sample. Killed.

## Pre-registration

- **Hypothesis:** breakouts that close weak in the bar's range (low close-location value) fail more often.
- **Rule:** keep a trade only if CLV = (close - low) / (high - low) on the entry bar is at least 0.5.
- **Params:** a single threshold of 0.5, chosen in advance. 1 variant. CLV quintiles are a diagnostic only.

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
| IS | CLV >= 0.5 | 9,015 | 1.2563 | 1.0638 | 53.2 | 4.3% | 0.41 | -16.7% | 65% |
| OOS | base | 6,505 | 1.0427 | 1.0322 | 47.6 | 0.9% | 0.13 | -29.2% | 64% |
| OOS | CLV >= 0.5 | 5,832 | 1.0318 | 1.0304 | 47.3 | 0.7% | 0.12 | -28.5% | 64% |

SPY: IS 16.8% CAGR / Sharpe 0.96 / MDD -33.7%. OOS 11.8% / 0.72 / -24.5%. Bootstrap PF diff: IS 95% CI [-0.016, +0.041] (p 0.22). OOS [-0.039, +0.012] (p 0.87). CLV quintile PF, lowest to highest: 1.155, 1.220, 1.137, 1.167, 1.114. Not monotonic.

## Verdict

**Kill.** Not significant in either half, and OOS PF goes down. The IS portfolio CAGR gain comes from slot tie-breaks, not edge.

## Related Pages

- [[backtest]] - harness and cost model behind the trade book
- [[thirty-day-signal-cut-plan]] - live stack stays frozen; this is offline research only
- [[scan-confidence]] - earlier work on the same control book
- [[hypothesis-cards]] - the path to queue a formal card if this is ever revisited
- [[bt-2026-10-20-entry-bar-close-location]] / [[bt-2026-10-20-crowded-breakout-days]] / [[bt-2026-10-20-day5-failure-exit]] - sibling tests from the same session

---

*Last compiled: 2026-10-20*
