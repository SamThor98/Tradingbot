---
source: experiments/backtester/results_debate_2026-10-28.json
created: 2026-10-28
updated: 2026-10-28
tags: [backtest, research, weekly-swing, relative-performance, debate, shrinkage]
---

# Weekly_swing-only candidates 2026-10-28: CRM, TTEK, ADUS, PGNY, WDAY, BDX, CTAS, CACI, SONO, USPH, DHR, ELF

> Per-name relative history for the 12 weekly_swing-only names on today's scan: none has a statistically supported positive record. ELF looks positive but is two trades from one 2022-23 run.

Debate chair request. Offline and read-only. No orders, no app code changes, no Schwab endpoints. Script: `experiments/backtester/debate_2026_10_28.py` (from `debate_2026_10_27.py`). Prices refreshed with yfinance through the last completed session, 2026-10-27 (all 13 series end on 10-27). Today's partial bar (10-28) was dropped. Weekly bars: the last completed week is Fri 2026-10-23; 10-26 and 10-27 are a partial week.
Related: [[bt-2026-10-27-debate-candidates]] (same method, 10 names; CRM, ADUS, CACI, TTEK, BDX repeat here with the same numbers on slightly newer prices), [[bt-2026-10-27-both-lookahead-free]], [[bt-2026-10-22-weekly-swing-six-names-breadth]], [[bt-2026-10-21-weekly-swing-stretch]], [[horizon-strategies]], [[scan-quality-2026-10-28]].

## Headline
1. **None of the 12 has a positive own-history that survives scrutiny.** On the primary test (OOS STATE excess vs SPY, 12 names), nothing is significantly positive. Four are significantly negative after Bonferroni: CRM, ADUS, USPH, DHR (DHR on n=5, so its CI is soft).
2. **ELF is the only positive name** (STATE OOS +16.03% raw -> +4.62% shrunk; T20 +6.55% -> +1.65%). It is n=6, the CI spans zero (-7.6..+45.2), IS is negative (STATE -1.97%), and STATE rests on two trades from one run (+43.1% and +75.8% excess, 2022-08 to 2023-09). Without those two it is negative. Treat as no tested edge.
3. **CTAS and BDX are about zero** (shrunk -0.22 / -0.33 STATE vs SPY). They "beat the pool" only because the WS pool is negative (-1.02 STATE / -0.33 T20). Neither has a fresh event: both are state-only from 10-09.
4. **Only 6 of 12 have a fresh new WS event in the 10-23 week**: TTEK, ADUS, PGNY, WDAY, CACI, ELF. The other six (CRM, BDX, CTAS, SONO, USPH, DHR) are state-only from an older trigger. PGNY's and CRM's weekly state is already off on the partial 10-27 bar (provisional; can flip back by Friday).
5. **"Beats the pool" is not evidence of edge here.** No name's 95% CI lower bound sits above the pool mean, in either exit.

## Method
- Setup: weekly_swing (WS) = weekly close > 10w SMA > 30w SMA, 30w SMA rising over 8 weeks, close >= 0.85 x 52w high. Event = first week the state turns on. Entry = next open after that Friday close. Costs 0.34% round trip. Exits: STATE (first week the state turns off, exit next open; cap 52 weeks) and T20 (close of the 20th session). Open trades are excluded.
- Pool for shrinkage = all WS events in `pool_weekly_swing_2026-10-22.parquet`, matching exit and period (not refreshed; same pool as 10-27). OOS pool mean vs SPY: **-1.02% STATE (n=16,888), -0.33% T20 (n=16,939)**.
- Shrinkage: w = n/(n+kappa), kappa floor 10 (empirical-Bayes kappa from the pool when larger), shrunk = w x raw + (1-w) x pool.
- Excess = trade return minus SPY (or the repo sector ETF) over the same entry-to-exit window. CIs: 2,000-draw week-block bootstrap on the mean.
- IS = signals 2015-10-16 to 2021-12-31. OOS = 2022-01-01 on.

## Ranking by shrunk OOS STATE excess vs SPY (percent per trade)
| Rank | Name | Sector (repo map) | n IS/OOS | STATE OOS vs SPY raw -> shrunk | 95% CI | beat SPY | vs sector | T20 OOS vs SPY | beat SPY | T20 vs sector | Beats WS pool S / T20 (shrunk) | WS event |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ELF | XLP | 13/6 | +16.03 -> +4.62 | -7.56..+45.21 | 3/6 | +18.62 -> +6.59 | +6.55 -> +1.65 | 4/6 | +7.93 -> +2.60 | Y / Y | FRESH 10-23 |
| 2 | CTAS | XLI | 23/12 | +0.59 -> -0.22 | -3.48..+5.98 | 6/12 | +1.40 -> +0.65 | -0.43 -> -0.37 | 7/12 | +1.00 -> +0.52 | Y / N | state-only (last 10-09) |
| 3 | BDX | XLV | 22/7 | +0.87 -> -0.33 | -3.21..+5.00 | 4/7 | +1.40 -> +0.44 | +1.30 -> +0.19 | 4/7 | +1.97 -> +0.75 | Y / Y | state-only (last 10-09) |
| 4 | TTEK | XLI | 24/9 | -0.61 -> -0.84 | -3.28..+3.25 | 3/9 | -1.42 -> -0.76 | -0.99 -> -0.58 | 4/9 | -2.08 -> -0.83 | Y / N | FRESH 10-23 |
| 5 | CACI | XLK | 21/19 | -1.28 -> -1.17 | -3.19..+0.63 | 8/19 | -1.49 -> -1.03 | +0.67 -> +0.23 | 11/19 | -0.06 -> -0.01 | N / Y | FRESH 10-23 |
| 6 | PGNY | XLV | 6/4 | -2.00 -> -1.26 | -4.90..+0.90 | 1/4 | -5.34 -> -1.60 | +2.39 -> +0.24 | 1/4 | -1.01 -> -0.20 | N / Y | FRESH 10-23, provisional state OFF 10-27 |
| 7 | CRM | XLK | 19/7 | -2.66 -> -1.62 | -4.08..-1.24 | 1/7 | -3.38 -> -1.46 | -3.11 -> -1.22 | 2/7 | -1.43 -> -0.47 | N / N | state-only (last 10-16), provisional state OFF 10-27 |
| 8 | WDAY | XLK | 31/8 | -3.09 -> -1.84 | -7.68..+1.53 | 2/8 | -3.25 -> -1.51 | -0.12 -> -0.26 | 2/8 | +0.25 -> +0.14 | N / Y | FRESH 10-23 |
| 9 | DHR | XLV | 27/5 | -5.74 -> -2.40 | -7.90..-3.97 | 0/5 | -2.11 -> -0.81 | -1.80 -> -0.70 | 3/5 | -0.58 -> -0.12 | N / N | state-only (last 10-09) |
| 10 | SONO | XLK | 6/4 (T20 6/5) | -7.64 -> -2.66 | -20.67..+6.36 | 1/4 | -7.20 -> -2.12 | -8.78 -> -2.45 | 1/5 | -9.59 -> -2.68 | N / N | state-only (last 09-25) |
| 11 | ADUS | XLV | 17/15 | -4.17 -> -2.76 | -7.98..-1.36 | 2/15 | -3.61 -> -2.19 | -4.18 -> -2.26 | 4/15 | -3.93 -> -2.11 | N / N | FRESH 10-23 |
| 12 | USPH | XLV | 9/7 | -10.01 -> -4.31 | -15.97..-4.32 | 0/7 | -8.83 -> -3.63 | -3.46 -> -1.33 | 3/7 | -2.99 -> -1.03 | N / N | state-only (last 10-16) |

## In-sample vs out-of-sample (raw, per trade vs SPY)
| Name | IS STATE vs SPY raw | IS T20 vs SPY raw | OOS STATE raw | OOS T20 raw | IS/OOS sign flip |
|---|---|---|---|---|---|
| ELF | -1.97 (n=13) | -0.27 (n=13) | +16.03 | +6.55 | STATE T20 |
| CTAS | +1.97 (n=23) | +1.01 (n=23) | +0.59 | -0.43 | T20 |
| BDX | -1.21 (n=22) | -0.67 (n=22) | +0.87 | +1.30 | STATE T20 |
| TTEK | +2.12 (n=24) | -0.16 (n=24) | -0.61 | -0.99 | STATE  |
| CACI | -0.96 (n=21) | -0.80 (n=21) | -1.28 | +0.67 | T20 |
| PGNY | +1.54 (n=6) | +2.84 (n=6) | -2.00 | +2.39 | none |
| CRM | +2.85 (n=19) | +1.02 (n=19) | -2.66 | -3.11 | STATE T20 |
| WDAY | -2.09 (n=31) | -0.57 (n=31) | -3.09 | -0.12 | none |
| DHR | +2.70 (n=27) | +1.55 (n=27) | -5.74 | -1.80 | STATE T20 |
| SONO | +13.88 (n=6) | +5.11 (n=6) | -7.64 | -8.78 | T20 |
| ADUS | -1.15 (n=17) | -0.72 (n=17) | -4.17 | -4.18 | none |
| USPH | +3.71 (n=9) | +0.32 (n=9) | -10.01 | -3.46 | STATE T20 |

## Per-name verdicts
- **ELF** (fresh): the one positive record, and not credible. n=6 OOS, CI -7.6..+45.2, IS negative, STATE driven by one 2022-23 run. T20 vs sector (+7.93 raw, CI +0.85..+17.9) is nominally significant, 1 of 96 cells. Stretch is +8.3% over the 50d SMA.
- **CTAS** (state-only 10-09): neutral. OOS n=12, STATE median -0.19%, one +26.5% outlier (2023-11 to 2024-11); without it -1.76%.
- **BDX** (state-only 10-09): neutral. n=7, STATE raw +0.87% but median +1.85% and ex-best -0.73%. IS -1.21%. Too small to read.
- **TTEK** (fresh): at the pool. STATE -0.84, T20 -0.58 shrunk. IS +2.12% flipped to OOS -0.61%.
- **CACI** (fresh): at or below the pool on STATE (-1.17), slightly positive T20 (+0.23). Largest OOS n (19). No edge either way.
- **PGNY** (fresh, short history): STATE -1.26 shrunk, 1 of 4 beat SPY. T20 +0.24 is one +21% trade; ex-best -3.8%. Provisional weekly state is off on 10-27.
- **CRM** (state-only 10-16): significantly negative, survives Bonferroni. 1 of 7 beat SPY. IS +2.85% flipped. Provisional weekly state off on 10-27.
- **WDAY** (fresh): negative in both periods (IS -2.09%, OOS -3.09%; the IS CI is below zero). 2 of 8 beat SPY.
- **DHR** (state-only 10-09): 0 of 5 beat SPY, STATE -5.74% raw. IS +2.70% flipped. n=5, so the Bonferroni significance is soft.
- **SONO** (state-only 09-25, short history): n=4 OOS and CIs of +/-20 points. Unreadable. IS +13.9% flipped to OOS -7.6%.
- **ADUS** (fresh): significantly negative, survives Bonferroni. 2 of 15 beat SPY. IS also negative. Worst fresh name.
- **USPH** (state-only 10-16): significantly negative, survives Bonferroni. 0 of 7 beat SPY. IS +3.71% flipped.

## Short histories
- **PGNY**: first price bar 2019-10-25 (IPO Oct 2019), 11 WS events in total. The IS window effectively starts after the 30-week SMA warms up, so IS n=6 covers 2020-2021 only, OOS n=4.
- **SONO**: first bar 2018-08-02, 11 WS events. IS n=6, OOS n=4 (T20 n=5).
- **ELF**: first bar 2016-09-22, 20 WS events, IS n=13, OOS n=6.
- The other nine have bars from 2013 and 9-31 IS events.

## Sector map check (repo `.sector_map_cache.json` vs real GICS)
| Name | Repo map | Real GICS sector | Match |
|---|---|---|---|
| CRM | XLK | Information Technology (XLK) | yes |
| WDAY | XLK | Information Technology (XLK) | yes |
| TTEK | XLI | Industrials (XLI) | yes |
| ADUS | XLV | Health Care (XLV) | yes |
| PGNY | XLV | Health Care (XLV) | yes |
| BDX | XLV | Health Care (XLV) | yes |
| CTAS | XLI | Industrials (XLI) | yes |
| CACI | XLK | Industrials (XLI) | **NO** |
| SONO | XLK | Consumer Discretionary (XLY) | **NO** |
| USPH | XLV | Health Care (XLV) | yes |
| DHR | XLV | Health Care (XLV) | yes |
| ELF | XLP | Consumer Staples (XLP) | yes |

Repo mapping unchanged. Two mismatches: **CACI** is mapped to XLK but is GICS Industrials (Diversified Support Services), so XLI is the right comparison; **SONO** is mapped to XLK but is Consumer Discretionary (Consumer Electronics), so XLY. Their "vs sector" columns compare against the wrong ETF and should be ignored; the vs-SPY columns are unaffected. Sensitivity against the corrected ETFs was not run. GICS was looked up for CACI, TTEK, ELF and SONO; the rest are from general knowledge. CRM and WDAY map correctly, but XLK is dominated by semis and hardware, so it is a loose comparison for software.

## Caveats
- **Small samples:** OOS n is 4-19 per name (flag "anecdotal" under 10, "insufficient_sample" under 30 on every name). Shrinkage returns mostly the pool for n<10. Bootstrap CIs on n<10 are too narrow because few distinct weeks are resampled (DHR n=5, PGNY n=4, SONO n=4).
- **Multiple testing:** 12 names x 2 exits x 2 benchmarks x 2 periods = 96 cells. 11 have a 95% CI below zero and 3 above zero; about 4.8 of 96 would be flagged by chance at 5% (cells are correlated, so this is rough). The three positive cells are DHR T20 in-sample (vs SPY and sector) and ELF T20 OOS vs sector; none is the OOS-vs-SPY test. Primary family = OOS STATE vs SPY, 12 names, Bonferroni two-sided level 0.42% (99.58% CI): CRM, ADUS, USPH, DHR negative; none positive.
- **Survivorship:** per-name histories come from names that exist today, and the pool excludes delisted names, so every number is flattered. Sector map is today's, not point-in-time.
- **Selection:** these names are on the list because the signal is on now; their past events are not conditioned on that, but a winner like ELF is in the list partly because it kept running.
- **Entry timing:** a 10-23 weekly event was tested at the 10-26 open, which has passed. Entering now is 2+ sessions later than tested. "Fresh" means a new event on the 10-23 bar; "provisional" uses the partial 10-26/10-27 weekly bar and can flip by Friday.
- **Per trade, not annualized, and not beta-adjusted.** Trades are single-name, not a portfolio with slots and exposure limits.
- Pool not refreshed since 2026-10-22 (same pool as 10-27).

## Related Pages
- [[bt-2026-10-27-debate-candidates]] - previous run (10 names); same method
- [[bt-2026-10-27-both-lookahead-free]] - why "both signals" is not used here
- [[bt-2026-10-22-weekly-swing-six-names-breadth]] - earlier weekly_swing per-name relative runs
- [[horizon-strategies]] - weekly_swing rule definition
- [[scan-quality-2026-10-28]] - today's scan log
- [[sector-map-mismatches]] - CACI, SONO and PKG sector-map errors; vs-sector figures unreliable

---

*Last compiled: 2026-10-28*
