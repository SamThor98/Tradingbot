---
source: experiments/backtester/results_2026-10-20.json
created: 2026-10-20
updated: 2026-10-20
tags: [backtest, research, exits]
---

# Day-5 failure exit (2026-10-20)

> Exiting at the day-5 close when a trade is below entry cuts many trades that would have recovered. PF falls in every era. Killed.

## Pre-registration

- **Hypothesis:** early stop-outs are the main PF drag (see [[scan-confidence]]), so a trade that is underwater after 5 bars should be cut early.
- **Rule:** if the trade is still open after bar 5 and close[5] is below the entry price, exit at close[5]. Costs are the trade's own slippage plus fees. Otherwise the original exit is unchanged.
- **Params:** 5 bars and a 0% threshold, fixed in advance. 1 variant. The base exit is the bare book's exit, not the live 15/40 grace stack.

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
| IS | day-5 exit | 9,928 | 1.1339 | 0.9430 | 38.6 | 0.5% | 0.10 | -31.4% | 65% |
| OOS | base | 6,505 | 1.0427 | 1.0322 | 47.6 | 0.9% | 0.13 | -29.2% | 64% |
| OOS | day-5 exit | 6,505 | 0.9954 | 0.9564 | 35.3 | -2.3% | -0.08 | -29.7% | 64% |

Bootstrap PF diff: IS [-0.176, -0.044]. That is significantly worse. OOS [-0.104, +0.019] (p of no harm 0.08).

## Verdict

**Kill.** PF is significantly worse in sample, below 1.0 OOS, and fails the worst-era floor. Breakouts that dip in the first week often recover. This matches the [[vcp-invalidation-exit]] finding that giving trades room helps.

## Related Pages

- [[backtest]] - harness and cost model behind the trade book
- [[thirty-day-signal-cut-plan]] - live stack stays frozen; this is offline research only
- [[scan-confidence]] - earlier work on the same control book
- [[hypothesis-cards]] - the path to queue a formal card if this is ever revisited
- [[bt-2026-10-20-entry-bar-close-location]] / [[bt-2026-10-20-crowded-breakout-days]] / [[bt-2026-10-20-day5-failure-exit]] - sibling tests from the same session

---

*Last compiled: 2026-10-20*
