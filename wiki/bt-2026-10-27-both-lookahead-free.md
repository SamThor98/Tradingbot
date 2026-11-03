---
source: experiments/backtester/results_both_lookahead_free_2026-10-27.json
created: 2026-10-27
updated: 2026-10-27
tags: [backtest, research, trend-breakout, weekly, relative-performance, lookahead, correction]
---

# Lookahead-free re-test of "trend_breakout + same-week weekly_swing" (2026-10-27)

> Re-simulation with entry at the open after the Friday on which weekly_swing is confirmed. The pooled "both" edge is gone: it was a lookahead premium of about +0.7pp per trade.

Offline and read-only. No orders, no app code changes. Script: `experiments/backtester/both_lookahead_free_2026-10-27.py`. Results: `results_both_lookahead_free_2026-10-27.json`. Corrects [[bt-2026-10-22-trend-breakout-vs-weekly-swing]]; follows the diagnostic in [[bt-2026-10-27-debate-candidates]]. Rules under test: [[horizon-strategies]] (weekly_swing), [[stage-2-analysis]] and [[vcp-detection]] (trend_breakout core), [[scan-catalog]].

## Verdict
1. **Lookahead-free "both" has no edge.** OOS (2022-01 to 2026-09), per trade vs SPY: T20 -0.23% [-0.86, +0.43], STATE -0.32% [-0.80, +0.21]. Both CIs include zero, profit factor on excess 0.94 and 0.89. It is no better than weekly_swing alone (-0.36% / -0.29%) or trend_breakout alone (-0.43% / -0.43%).
2. **The Oct 22 result was lookahead.** Same universe and exits with the old entry (TB next open) reproduce +0.38% STATE OOS. Moving the entry to after the Friday confirmation drops it to -0.32%. The premium is +0.70pp [+0.56, +0.85] STATE and +0.71pp [+0.45, +0.96] T20, significant, and it comes from entering Mon-Thu before the confirmation existed.
3. **Do not write the "both" hypothesis card.** Nothing survives. The only exploratory lead is the mirror order (weekly_swing confirmed first, trend_breakout fires in a later week), see below. It is not significant after correction.

## Method
- Universe: 1,479 cached tickers (SP1500-now), signals 2015-10-16 to 2026-09-17. IS = signals to 2021-12-31, OOS = 2022-01-01 on. The price cache ends 2026-09-21, so October 2026 is not covered. The partial current week is never used, because entry needs a session after the Friday.
- Validation: the re-simulated weekly_swing events and T20 returns match the Oct 22 pool exactly (40,092 of 40,092, 0 return mismatches). Immediate trend_breakout T20 returns match the pool on 74,725 events with 0 mismatches.
- Entry for every arm in the table below the first block: open of the first session after the Friday, one trade per ticker-week. Costs 0.34% round trip on the trade and on the benchmark window.
- Exits: T20 (close of session 20); STATE_TB (first close with Stage 2 failing, next open, cap 40 sessions); STATE_WS (weekly trigger off, cap 52 weeks, weekly_swing arms only). STATE_WS is weekly_swing's own exit and is the one the earlier -0.92% diagnostic used.
- Excess vs SPY and vs the repo sector ETF. Week-block bootstrap, 2,000 draws; differences use joint week resampling. PF is on per-trade excess.
- Arms: BOTH_main = weekly_swing event at Friday F and a trend_breakout event in the same week (on or before F). a5 / a10 = trend_breakout event within sessions [F-5, F] / [F-10, F]. b = trend_breakout core trigger on at F's close. WS_only = weekly_swing event with no trend_breakout event that week. TB_only_delayed = trend_breakout week with no weekly_swing event, entry delayed to the same post-Friday open.

## Pooled results, OOS, excess vs SPY per trade
| Arm | n | T20 | STATE_TB | STATE_WS |
|---|---|---|---|---|
| BOTH_main | 4,008 / 4,042 / 3,979 | -0.23% [-0.86, +0.43] PF 0.94 | -0.32% [-0.80, +0.21] PF 0.89 | -0.90% [-1.55, -0.29] PF 0.77 |
| a5 (TB within 5 sessions) | 4,219 | -0.21% | -0.29% | -0.90% |
| a10 (TB within 10 sessions) | 5,097 | -0.23% | -0.34% | -0.89% |
| b (TB trigger on at F) | 2,894 | -0.31% | -0.33% | -0.77% |
| WS_only | 12,931 | -0.36% [-0.77, +0.06] | -0.29% [-0.52, -0.03] | -1.04% [-1.44, -0.61] |
| WS_all | 16,939 | -0.33% | -0.29% | -1.01% |
| TB_only_delayed | 24,797 | -0.47% [-0.90, -0.02] | -0.44% [-0.75, -0.13] | n/a |
| TB_all_delayed | 28,805 | -0.43% | -0.43% | n/a |
| TB immediate, all events | 28,805 | -0.40% | -0.40% | n/a |
| Oct 22 definition (TB next open, lookahead) | 4,260 | +0.48% [-0.12, +1.15] | +0.38% [-0.09, +0.92] | n/a |

IS (2015-10 to 2021) BOTH_main: T20 +0.18% [-0.32, +0.71] PF 1.06; STATE_TB -0.19% [-0.53, +0.13]; STATE_WS -0.45% [-0.87, -0.09]. IS n = 5,763 in 316 weeks. OOS spans 232 to 235 weeks.

Differences (BOTH_main minus comparator, OOS, SPY-relative): vs WS_only T20 +0.13pp [-0.32, +0.60], STATE_TB -0.03pp [-0.43, +0.39]; vs TB_all_delayed T20 +0.20pp [-0.27, +0.68]. None is distinguishable from zero. Against STATE_WS, BOTH_main is worse than WS_only in IS (-0.86 to -0.17pp, significant) and indistinguishable in OOS.

Vs the sector ETF, BOTH_main OOS is +0.22% (T20) and +0.04% (STATE_TB), against -0.14% / -0.16% for WS_only. The difference is +0.36pp [-0.03, +0.76] (T20), not significant, and it flips sign in IS (-0.07pp). Not usable.

## Robustness
- **PIT S&P 500 membership** (Sharadar quarterly, delisted names still missing): BOTH_main OOS T20 -0.52% [-1.27, +0.22] n=1,289, STATE_TB -0.50% [-1.07, +0.05] n=1,297. Worse, not better.
- **By year** (BOTH_main T20 mean excess, n): 2022 -0.33% (472), 2023 -1.11% (927), 2024 +0.59% (1,166), 2025 +0.42% (714), 2026 to Sept -0.98% (729). Signs alternate; there is no stable edge.
- **Mirror order (c, exploratory)**: weekly_swing confirmed in week W, trend_breakout fires in week W+1, entry TB next open. OOS T20 -0.08% [-0.70, +0.56] n=3,515, STATE_TB +0.01% [-0.50, +0.52] n=3,527, PF 1.00. Against trend_breakout without a prior-week weekly_swing event (-0.45% / -0.46%) the STATE_TB difference is +0.04 to +0.94pp (nominal p 0.037), T20 -0.16 to +0.89pp. Nominal only. This is one of 46 pairwise comparisons (9 pairs x exits x periods) and does not pass the Bonferroni threshold.

## Caveats
- **Multiple testing.** 22 primary OOS cells (arm x T20/STATE_TB) give a Bonferroni alpha of 0.0023. No primary cell is significantly positive. Variants a5, a10 and b were defined after the diagnostic split, so treat them as sensitivity checks, not independent confirmations. They are nearly the same events as BOTH_main (a5 is 105% of its size).
- **Overfitting.** No parameters were tuned. Variants are fixed definitions. The three exit definitions are all reported.
- **Survivorship.** The cache is today's SP1500 plus a PIT S&P 500 check. Delisted names are still missing, which biases every arm upward. The PIT subset is lower, not higher.
- **Samples and clustering.** Thousands of trades, but only 232 to 246 OOS weeks, and trades cluster by week. CIs use week blocks. Per-name results (below) are small.
- **Not a portfolio test.** These are per-trade excesses, de-duplicated per ticker-week. No slot limits, sizing, CAGR, Sharpe or drawdown. Overlapping trades are not netted.
- **Costs.** A flat 0.34% round trip on both the trade and SPY. No impact or gap modeling beyond the Monday open.
- **Mixed exits.** For weekly_swing-anchored arms STATE_TB is a trend_breakout-style exit applied to a weekly_swing entry. STATE_WS is the native weekly one. Cross-arm comparisons are cleanest on T20.

## PKG own history (lookahead-free)
Raw per-trade excess vs SPY, then shrunk with w = n/(n+kappa), kappa floor 10, toward the matching pooled arm.

| Definition (exit) | IS n, raw, shrunk | OOS n, raw, shrunk |
|---|---|---|
| BOTH_main (T20) | 5, +3.2%, +0.7% | 3, +7.9%, +1.6% (pool -0.2%) |
| BOTH_main (STATE_TB) | 5, +2.0%, -0.1% | 3, +7.9%, +1.4% (pool -0.3%) |
| WS_only (T20) | 10, -0.4%, -0.1% | 11, -1.9%, -1.1% |
| WS_only (STATE_TB) | 10, -1.8%, -0.8% | 11, -2.1%, -1.0% |
| TB, all events, TB next open (STATE_TB) | 38, +0.4%, +0.3% | 33, +2.15%, +1.45% |
| Mirror order (STATE_TB) | 5, -0.5%, -0.2% | 5, +1.3%, +0.2% |

- **Reconciling quant's +2.15%.** It equals PKG's raw OOS result over all 33 trend_breakout events (STATE exit, vs SPY). It is not a "both" number. Shrunk, it is +1.45%. The earlier +1.51% (n=3) was the lookahead "both" arm. Under the clean definition the same three trades (2024-02-09, 2024-07-12, 2026-06-05) shrink to +1.4% to +1.6%, but n=3 and the pooled arm is -0.2% to -0.3%, so this is anecdotal.
- **Today PKG is not a "both" under any definition.** weekly_swing turned on at Friday 2026-10-23 (entry 10-26 open was available). The last trend_breakout event was 2026-08-04. There is none in that week or the 10 prior sessions, and the core trigger was off at the Friday close (Stage 2 on, VCP volume off). It is a weekly_swing-only entry. A trend_breakout event on or after 10-27 would make it a mirror-order case (own history n=5/5, shrunk about +0.2%, pooled about 0).
- **PKG weekly_swing-only history is negative OOS:** n=11, -1.9% (T20) and -2.1% (STATE_TB) raw, -1.1% / -1.0% shrunk. Its overall weekly_swing record (n=14 OOS) is roughly flat only because the three "both" trades are included.
- **Sector ETF flag.** The repo maps PKG to XLY (Consumer Discretionary). PKG is a packaging company, which GICS classes as Materials (XLB). BOTH_main OOS T20 excess vs XLB is about +4.3% (n=3) against +9.4% vs XLY. The sector figures above use XLY and are unreliable for this name. Mapping check suggested, not changed.
- **Does it change anything?** No. The lookahead-free pooled edge is about zero or negative, PKG's own "both" support is three trades, its live setup is weekly_swing-only with a negative own record, and the +2.15% belongs to a trend_breakout trigger that is not firing.

## Related Pages
- [[bt-2026-10-22-trend-breakout-vs-weekly-swing]] - Original result, corrected by this page
- [[bt-2026-10-27-debate-candidates]] - Diagnostic split and the ten candidates
- [[horizon-strategies]] - weekly_swing definition
- [[scan-catalog]] - trend_breakout is the live book
- [[hypothesis-cards]] - Card format; none written for "both"
- [[thirty-day-signal-cut-plan]] - Live-stack freeze through 2026-11-07

---

*Last compiled: 2026-10-27*