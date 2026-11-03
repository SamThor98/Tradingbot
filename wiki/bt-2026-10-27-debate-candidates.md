---
source: experiments/backtester/results_debate_2026-10-27.json
created: 2026-10-27
updated: 2026-10-27
tags: [backtest, research, trend-breakout, weekly, relative-performance, debate, lookahead]
---

# Debate candidates 2026-10-27: FBNC, NTNX, PKG (both), PAYX, ADUS, CRM, CACI, TTEK, BDX, RLI (WS)

Debate chair request. Offline and read-only. No orders, no app code changes. Script: `experiments/backtester/debate_2026_10_27.py` (from `debate_2026_10_23.py`). Prices refreshed with yfinance through the last completed session, 2026-10-26. Today's partial bar was dropped because the session was still open.
Related: [[bt-2026-10-27-both-lookahead-free]] (full lookahead-free re-simulation), [[bt-2026-10-23-debate-candidates]], [[bt-2026-10-22-trend-breakout-vs-weekly-swing]], [[bt-2026-10-22-weekly-swing-six-names-breadth]], [[bt-2026-10-21-weekly-swing-stretch]], [[horizon-strategies]].

## Headline
1. **The pooled "both signals" edge looks like an entry-timing artifact.** The BOTH arm (trend_breakout with weekly_swing in the same week) enters at the trend_breakout next open. For a trend_breakout signal on Mon-Thu, that entry is before the Friday weekly_swing confirmation, so it uses information the trade could not have had. Lookahead-free versions have no edge (OOS excess vs SPY per trade, STATE): signal on Friday -0.31% (n=1,108), vs +0.63% for Mon-Thu (n=3,180). Entering after the weekly_swing Friday (WS_with_TB arm) is -0.92% (n=4,017), no better than WS without trend_breakout at -1.05%. The earlier "+0.38% / works one way" result in [[bt-2026-10-22-trend-breakout-vs-weekly-swing]] should not be used to size or rank trades until re-tested with entry after confirmation.
2. No candidate has a significant positive own history. Two are significantly negative OOS (CRM, ADUS) and survive a Bonferroni correction over 10 names.
3. Only 4 of 10 names are a clean new weekly_swing event in the last completed week (PAYX, ADUS, CACI, TTEK), plus FBNC, NTNX and PKG as WS-only. Only NTNX has a trend_breakout event and a new weekly_swing event in the same week, and its trend_breakout entry (10-22 open) came before the 10-23 weekly confirmation.

## Method
- Setups: BOTH = trend_breakout core event (Stage 2 + 4-day VCP volume) with a weekly_swing event in the same W-FRI week (pool arm TB_with_WS). WS = weekly_swing event.
- Entry at the next open. Costs 0.34% round trip. Exits: STATE (signal off; trend_breakout cap 40 sessions, weekly_swing cap 52 weeks) and T20.
- Excess is per trade vs SPY and vs the sector ETF from the repo's `.sector_map_cache.json` over the same window and costs. That map is imperfect (PKG is XLY, PAYX and CACI are XLK).
- Shrinkage toward the matching pool, arm, exit and period: w = n/(n+kappa), kappa floor 10. CIs use a 2,000-draw week-block bootstrap. The 99.5% interval is the Bonferroni check for the 10-name OOS STATE family.
- In-sample (IS) is 2015-10-16 to 2021. Out-of-sample (OOS) is 2022 to 2026-10.
- Clean entry now: for BOTH, a trend_breakout event and a NEW weekly_swing event in the same week (last completed week 10-23, or this week provisionally). For WS, a new weekly_swing event in the last completed week or provisionally this week, not state-on from an older trigger. Provisional means the partial weekly bar, which can flip by Friday.

## Ranking: OOS STATE excess vs SPY per trade, raw -> shrunk (best to worst by shrunk)
| Rank | Name | Setup | n IS/OOS | STATE OOS vs SPY | vs sector | T20 OOS vs SPY | IS STATE raw | Clean entry now |
|---|---|---|---|---|---|---|---|---|
| 1 | PKG | BOTH | 5/3 | +7.80 -> +1.51 (CI -1.2..+12.9) | +8.20 -> +2.26 | +9.02 -> +2.45 (n=3) | +1.88 | No for BOTH (TB event 8/4); WS event 10/23 |
| 2 | NTNX | BOTH | 5/5 | +0.49 -> +0.41 (CI -8.1..+10.8) | -2.70 -> -0.27 | -1.11 -> -0.05 | -3.24 | BOTH same-week yes (TB 10/21, WS 10/23), but TB entry preceded WS confirm |
| 3 | FBNC | BOTH | 9/5 | +0.00 -> +0.30 (CI -3.3..+4.6) | +1.75 -> +1.05 | +1.96 -> +0.97 | +0.76 | No for BOTH (TB event 9/10); WS event 10/23 |
| 4 | BDX | WS | 22/7 | +0.87 -> -0.33 | +1.40 -> +0.44 | +1.30 -> +0.19 | -1.21 | No: WS event 10/9, state on |
| 5 | PAYX | WS | 22/16 | -0.26 -> -0.58 | +0.28 -> +0.09 | -1.07 -> -0.71 | -0.56 | Yes: WS event 10/23 |
| 6 | TTEK | WS | 24/9 | -0.61 -> -0.84 | -1.42 -> -0.76 | -0.99 -> -0.58 | +2.12 | Yes: WS event 10/23 |
| 7 | CACI | WS | 21/19 | -1.28 -> -1.17 | -1.49 -> -1.03 | +0.67 -> +0.23 | -0.96 | Yes: WS event 10/23 |
| 8 | RLI | WS | 17/17 | -1.47 -> -1.28 | -1.04 -> -0.72 | -1.74 -> -1.08 | -0.16 | No: WS event 10/16, state on |
| 9 | CRM | WS | 19/7 | -2.66 -> -1.62 (CI -4.1..-1.2) | -3.38 -> -1.46 | -3.11 -> -1.22 | +2.85 | No: WS event 10/16, state on |
| 10 | ADUS | WS | 17/15 | -4.17 -> -2.76 (CI -8.0..-1.4) | -3.61 -> -2.19 | -4.18 -> -2.26 | -1.15 | Yes: WS event 10/23 |

Pool OOS means (excess vs SPY, STATE / T20): BOTH +0.38 / +0.48 (n=4,288 / 4,260). WS -1.02 / -0.33 (n=16,888 / 16,939). TB-only -0.53 / -0.58. Lookahead-free references: BOTH with Friday signal -0.31 / -0.17, WS_with_TB -0.92 / -0.23, WS_only -1.05 / -0.36.

## Lookahead-free view of the three BOTH names (entry after the WS Friday, WS_with_TB history)
| Name | n IS/OOS | STATE OOS vs SPY raw -> shrunk | T20 OOS raw -> shrunk |
|---|---|---|---|
| PKG | 5/3 | +10.24 -> +1.20 | +7.86 -> +1.64 |
| FBNC | 9/5 | -0.50 -> -0.80 | +2.00 -> +0.52 |
| NTNX | 5/5 | +2.25 -> -0.03 | -2.54 -> -1.00 |

All n are tiny. PKG's own record is 3 OOS trades.

## Contradiction flags
- **NTNX**: contradicts the pooled BOTH edge. IS STATE -3.24% (CI -5.1..-1.0, 1 of 5 beat SPY), IS T20 -8.58% (0 of 5). OOS T20 -1.11% vs the +0.48% pool.
- **FBNC**: mild. OOS STATE raw 0.00% vs the +0.38% pool, 2 of 5 beat SPY, IS/OOS signs flip.
- **PKG**: no contradiction, but n=3 OOS.
- **CRM**: IS +2.85% flips to OOS -2.66% (1 of 7 beat SPY, CI below zero, survives Bonferroni). Own history is worse than the negative WS pool.
- **ADUS**: OOS -4.17% (2 of 15 beat SPY, CI below zero, survives Bonferroni). Worse than the WS pool.
- **BDX, CACI**: own OOS is better than the negative WS pool (BDX STATE +0.87%, CACI T20 +0.67%), but CIs include zero and n is small for BDX (7).
- **TTEK, PAYX, RLI**: no pooled-edge contradiction. IS/OOS flips for TTEK (+2.12 -> -0.61), PAYX T20 and RLI T20.

## Caveats
- Small samples: every BOTH name has OOS n<=5, and several WS names have OOS n<=9. Shrinkage mostly returns the pool for them.
- Multiple testing: 10 names x 2 exits x 2 benchmarks x 2 periods = 80 comparisons, plus 48 for the lookahead-free alternates. At 5% expect about 4 false positives in the 80. The primary family is 10 (OOS STATE vs SPY), so the Bonferroni level is 0.5% two-sided.
- Survivorship: the pools and per-name history come from the current universe, and delisted names are missing. Sector map is today's, not point-in-time.
- The lookahead finding is a diagnostic split, not a full re-simulation of the BOTH arm with entry after confirmation. A full re-run is the next test.
- "Clean entry" for a 10-23 weekly event means the backtested entry was the 10-26 open, which has passed. Entering now is 1+ sessions later than tested.
- Excess is per trade, not annualized, and is not adjusted for beta (no residual alpha in this run).

Follow-up: [[bt-2026-10-28-ws-only-candidates]] (12 weekly_swing-only names, 2026-10-28).

Sector map errors found in this run (CACI, PKG): [[sector-map-mismatches]].
