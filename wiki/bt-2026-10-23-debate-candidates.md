---
source: experiments/backtester/results_debate_2026-10-23.json
created: 2026-10-23
updated: 2026-10-23
tags: [backtest, research, trend-breakout, weekly, relative-performance, debate]
---

# Debate candidates 2026-10-23: LTC, LQDT, CENTA (both), JNJ, VRTX (TB), EMR, REGN (WS)

Debate chair request. Offline and read-only. No orders, no app code changes. Script: `experiments/backtester/debate_2026_10_23.py`. Prices refreshed with yfinance through 2026-10-23.
Related: [[bt-2026-10-22-trend-breakout-vs-weekly-swing]], [[bt-2026-10-22-weekly-swing-six-names-breadth]], [[bt-2026-10-21-weekly-swing-stretch]], [[horizon-strategies]].

## Method
- Setups: BOTH = trend_breakout core event (Stage 2 + 4-day VCP volume) with weekly_swing firing in the same W-FRI week. Pool arm: TB_with_WS. TB = trend_breakout events. WS = weekly_swing events.
- Entry at the next open. Costs 0.34% round trip. Exits: STATE (signal off, TB cap 40 sessions) and T20.
- Excess is measured vs SPY and vs the sector SPDR over the same window. Each name is shrunk toward the matching pool, arm, exit and period: w = n/(n+kappa), kappa floor 10. CIs use a 2,000-draw week-block bootstrap.
- In-sample (IS) runs 2015-10-16 to 2021. Out-of-sample (OOS) runs 2022 to 2026-10.

## Results: OOS excess vs SPY per trade, raw → shrunk
| Rank | Name | Setup | n IS/OOS | STATE OOS | T20 OOS | STATE OOS vs sector | Fires now |
|---|---|---|---|---|---|---|---|
| 1 | JNJ | TB | 38/25 | +2.72 → +1.79 (CI +0.14..+5.5) | +0.83 → +0.42 | +2.16 → +1.50 | No: Stage 2 off, last event 9/24 |
| 2 | LQDT | BOTH | 6/1 | +15.3 → +1.22 (n=1) | +7.07 → +1.08 | +16.2 → +1.96 | TB event 10/22; WS state on (event 7/31) |
| 3 | CENTA | BOTH | 8/3 | -1.28 → +0.13 | -1.76 → -0.03 | -1.28 → +0.34 | TB event 10/20; WS event 10/16, off last week |
| 4 | LTC | BOTH | 2/5 | -2.96 → -0.39 (0/5 hits) | -6.68 → -1.91 (CI -8.9..-4.5) | -1.76 → +0.01 | TB event 10/21; WS state on (event 6/26) |
| 5 | VRTX | TB | 30/47 | -0.83 → -0.77 | +0.84 → +0.58 | -0.77 → -0.66 | No: Stage 2 on, last event 9/29 |
| 6 | EMR | WS | 23/17 | -2.15 → -1.68 | +0.86 → +0.30 | -1.53 → -1.02 | WS state on, event 9/25 |
| 7 | REGN | WS | 14/13 | -3.41 → -2.26 (CI -6.4..-0.4) | -0.34 → -0.33 | -3.00 → -1.74 | WS event 10/16 |

Pool OOS means: BOTH +0.38 (STATE) and +0.48 (T20). TB-only -0.53 and -0.58. WS -1.02 and -0.33.

## Contradiction flags
- **LTC**: contradicts the pooled BOTH edge, as SAIC did. Own OOS BOTH trades are 0 for 5 vs SPY. T20 OOS averages -6.7% with a CI entirely below zero.
- **CENTA**: weak contradiction. OOS is negative (n=3), IS is mildly positive (n=8).
- **JNJ**: positive outlier vs a negative TB pool (OOS CI excludes zero at STATE), but the setup is not firing now.
- **REGN**: OOS STATE is negative, CI below zero. This agrees with the negative WS pool.

## Caveats
- Every per-name sample is small. All BOTH names have n<=11, and LQDT has OOS n=1. Shrinkage mostly returns the pool.
- 7 names x 2 exits x 2 benchmarks were checked, so expect a few false positives at the 5% level.
- Survivorship: the pools come from the current-universe cache, and delisted names are missing.
- "Fires now" is a state check on fresh data. A "WS state on" with an old event means no new weekly_swing event this week.

Follow-up: [[bt-2026-10-27-debate-candidates]] (lookahead caveat on the BOTH arm).
