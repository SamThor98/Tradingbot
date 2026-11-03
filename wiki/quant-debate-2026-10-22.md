---
source: n/a (quant agent analysis; yfinance daily OHLC to 2026-10-21 plus 2026-10-22 intraday; schwab_skill/webapp/webapp.db scan_results and app_state, read-only)
created: 2026-10-22
updated: 2026-10-22
tags: [quant, debate, predictions, UNP, SXT, WELL, earnings, base-rates, stage2]
---

# Quant debate log 2026-10-22 (UNP, SXT, WELL)

Follows [[quant-debate-2026-10-21]], using the same model: entry = given price, stop = 2 x ATR14, target = 2R, 60-day max hold, history from 2015-10, setup = close > SMA50 > SMA200, non-overlapping trades, Wilson 95% CI. Everything below is an **in-sample** historical estimate unless labeled otherwise. Related: [[signal-scanner]], [[stage-2-analysis]], [[guardrails]].

ITGR, SAFT and SLAB were dropped because each has a pending cash deal (KKR $127, MAPFRE $105, TXN $231). Their ATR collapsed to about 0.2-0.4% of price, so the trend model does not apply.

| | UNP | SXT | WELL |
|---|---|---|---|
| Entry / stop / target | 287.87 / 279.12 / 305.38 | 132.19 / 125.79 / 144.99 | 234.19 / 224.17 / 254.23 |
| ATR14 / risk % | 4.38 / 3.0% | 3.20 / 4.8% | 5.01 / 4.3% |
| p (general uptrend) | 0.358 [0.254-0.478], n=67 | 0.381 [0.250-0.532], n=42 | 0.479 [0.369-0.592], n=73 |
| p (within 1% of 50d) | 0.421 [0.279-0.578], n=38 | 0.500 [0.307-0.693], n=22 | 0.629 [0.463-0.768], n=35 |
| EV R [CI] | +0.07 [-0.24, +0.43] | +0.14 [-0.25, +0.60] | +0.44 [+0.11, +0.78] |
| Kelly / half | 0.037 / 0.019 | 0.071 / 0.036 | 0.219 / 0.110 |
| Shares per $100k at 1% risk | 114 | 156 | 99 |
| R:R if scan stop used | 1.22 (stop 5.0%) | 1.59 | 1.56 |
| Corr SPY 60d / 1y; beta 60d | -0.01 / 0.10; -0.01 | 0.21 / 0.21; 0.39 | -0.04 / -0.03; -0.07 |
| Corr MGY / SNA 60d | -0.02 / 0.45 | -0.31 / 0.21 | -0.14 / 0.10 |
| From 52w high; 20d / 60d return | -8.9%; +4.5% / -2.2% | -4.8%; +2.6% / +6.1% | -8.2%; +1.9% / -3.9% |
| Next earnings | reported 10/22 | 10/30 | 10/26 |

The CIs of the "near 50d" and general conditions overlap for every name, so being near the 50d makes no detectable difference.

## UNP earnings (66 reports, 2010-04 to 2026-07; day 0 = report day, 07:00 ET release)
- Mean absolute day-0 move is 3.2%. The day-0 true range averages 1.89x the pre-event ATR, and the ATR after the event averages 1.11x the pre-event ATR (max 1.37x).
- Drift after the report: 20d +1.81% [+0.31, +3.32] and 60d +4.09% [+1.72, +6.46] across all reports. Moves tend to fade: Spearman(day0, 20d) = -0.31, p=0.012, and the 20d direction continued the day-0 direction only 24/66 times (0.36 [0.26-0.48]).
  - Day 0 up (n=35): 20d +0.43% [-1.58, +2.45] (51% up); 60d +2.65% [-1.02, +6.33].
  - Day 0 down (n=31): 20d +3.37% [+1.21, +5.54] (81% up).
  - Gap > +1% (n=27): 20d +0.47% [-2.08, +3.02].
- Entry at the reaction-day close with a 2xATR stop and 2R target:
  - All reports: p 0.50 [0.38-0.62], EV +0.50R.
  - Uptrend-only: p 0.41 [0.26-0.59], EV +0.24R [-0.23, +0.78]; 10 of 17 stops hit within 10 days.
  - Using pre-event ATR instead of post-event ATR barely matters (uptrend p 0.43 vs 0.41).
- Setup days in the 0-10 days after a report: p 0.393 [0.236-0.576], EV +0.18. Unconditional: p 0.358, EV +0.07. There is no detectable difference.
- **Oct 22 reaction (live, about 9:38 ET):** open 287.54, low 278.59, high 295.90, last 295.71 (+2.7%). The intraday low went **below** the 2xATR stop of 279.12.

## Bear case: UNP
Debate chair assigned the bear case to UNP. By EV alone WELL ranks first.
1. Even before earnings, the EV is +0.07R [-0.24, +0.43] and the lower bound is negative. The edge is indistinguishable from zero.
2. With the 2.0x stop the trade would already have been stopped out on today's intraday low (278.59 < 279.12). With the scan stop (273.48), R:R is only 1.22.
3. Today is an up day 0 (+2.7%). Historically that bucket has had no 20d drift (+0.43%, CI spans 0) and day-0 moves fade (rho -0.31). Buying the gap buys the historically weakest bucket.
4. Earnings-day range is about 1.9x ATR. A stop sized on today's inflated ATR rises about 11%, which cuts shares per $100k from 114 to about 100 at the same 1% risk.
5. UNP is 8.9% below its 52w high with a negative 60d return, so the regime is a weak uptrend. Its 60d correlation to SNA is 0.45, the highest overlap with the open book.
6. Post-earnings conditioning does not raise p meaningfully: 0.39 vs 0.36, CIs overlap.

## Base rates: 221-signal day with 0 Stage 2 fails
- The Oct 22 scan (`app_state.last_scan`) shows the following. Prior scans showed 21-43 signals; Oct 21 had `stage2_fail` = 1196.
  - `stage_a_waves` 1, `stage_a_executable_waves` 0, `horizon_waves` 1.
  - `stage_a_candidates` = `shortlisted` = 236, `pruned` 0.
  - `entry_shadow_stage2_evaluated` 0.
  - `rank_filter_v2_skipped`: insufficient_scores.
  - `stage2_shadow_would_filter` 48.
  - 33 of 221 have `trend_stack_ok` = False.
- Conclusion: Stage 2 was **not evaluated** (bypassed or broken), not merely permissive. "0 fails" carries no information, and the 221 count is a pipeline artifact. It should not be used as a breadth or base-rate signal.
- Signal count vs 5d forward return across 18 historical jobs (out-of-sample, 21-42 signals each): Spearman -0.09 for mean return and +0.06 for win rate. There is no relationship, and the power is negligible.
- Today's 221 names have an average pairwise 60d return correlation of 0.084, so the effective n is about 11.3, not 221.

## Predictions to score (by 2027-01-19, about 60 trading days)
- UNP (entry 287.87): P(305.38 before 279.12) = 0.36 [0.25-0.48]. Note the intraday low on 10/22 already touched the stop zone.
- SXT: P(144.99 before 125.79) = 0.38 [0.25-0.53].
- WELL: P(254.23 before 224.17) = 0.48 [0.37-0.59].

## Round 2 (corrected scan, about 10:18 ET): CHEF, CRM, SAIC
All estimates are in-sample (2015 onward). "Breakout" means close >= 99% of the prior 20d high; "near 52wH" means within 3% of the prior 252d high. Live prices are within 0.5% of the given prices.

| | CHEF | CRM | SAIC |
|---|---|---|---|
| Entry / stop / target | 110.05 / 102.09 / 125.96 | 246.47 / 229.05 / 281.31 | 139.05 / 131.46 / 154.22 |
| ATR14 | 3.98 (3.6%) | 8.71 (3.5%) | 3.79 (2.7%) |
| Uptrend now? | **No**: below 50d (111.25) | Yes, but 200d slope is -1.2% over 20d | Yes, 200d rising (+2.9% over 20d) |
| p uptrend [CI] | 0.333 [0.23-0.45], n=66 | 0.411 [0.31-0.53], n=73 | 0.373 [0.27-0.49], n=67 |
| p 20d breakout | 0.353 [0.24-0.49], n=51 | 0.418 [0.30-0.55], n=55 | 0.413 [0.28-0.56], n=46 |
| p near 52wH | 0.286 [0.16-0.45], n=35 | 0.419 [0.28-0.57], n=43 | 0.448 [0.28-0.63], n=29 |
| EV R (uptrend) [CI] | +0.00 [-0.31, +0.36] | +0.23 [-0.08, +0.58] | +0.12 [-0.20, +0.48] |
| EV R (breakout) | +0.06 | +0.25 [-0.11, +0.65] | +0.24 [-0.15, +0.67] |
| Kelly (breakout) | 0.03 | 0.13 | 0.12 |
| Shares per $100k at 1% risk | 125 | 57 | 131 |
| From 52wH; 60d return | -7.1%; +9.9% | -8.4%; +33.2% | -2.5%; +12.5% |
| Corr SPY 60d / 1y | -0.08 / 0.17 | 0.12 / 0.12 | 0.21 / 0.05 |
| Corr MGY / SNA 60d | -0.08 / -0.05 | 0.11 / 0.12 | -0.03 / 0.13 |
| Next earnings | 10/28 (inside 1 week) | 12/2 AMC (in window) | about 12/3 (yfinance estimate, unconfirmed) |

**Bull pick: SAIC.** It is the only name with a rising 200d. It closed +1.9% above its 20d high, sits 2.5% under its 52w high, and has the tightest ATR (2.7%), which gives the most shares per $100k. The near-52wH bucket has p 0.448 and EV +0.34R, and the breakout bucket has EV +0.24R. Correlation to SPY, MGY and SNA is low. Earnings fall about 30 trading days out.
**Red flags:**
- Every EV CI includes 0, and breakout n is only 29-46.
- The SAIC earnings date is not confirmed.
- CRM has the higher unconditional p, but its 200d is falling and it is up 33% in 60d (a rebound, not a mature trend).
- CHEF fails the uptrend test and reports on 10/28.

### Round 2 predictions (score by about 2027-01-19)
- SAIC: P(154.22 before 131.46) = 0.41 (breakout bucket, CI 0.28-0.56).
- CRM: P(281.31 before 229.05) = 0.42 (CI 0.30-0.55).
- CHEF: P(125.96 before 102.09) = 0.33 (CI 0.23-0.45).

**Scored 2026-10-29:** WELL stopped 10-28 through the earnings gap (fill 222.81, -1.14R); UNP was stopped on the 10-22 bar (-1.00R) or is +1.13R if counted from 10-23; SAIC is +1.30R and still open. See the Outcome section of [[trade-verdict-2026-10-22]].
