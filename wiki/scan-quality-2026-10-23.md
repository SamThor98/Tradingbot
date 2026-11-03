---
source: schwab_skill/webapp/webapp.db (scan_results job 0a12065e50, app_state last_scan); yfinance closes through 2026-10-22
created: 2026-10-23
updated: 2026-10-23
tags: [scan-quality, ranking, tracking, shadow, trend-breakout, weekly-swing]
---

# Scan Quality 2026-10-23

> Scan desk premarket readout for 10-23: data health, ranked table, "both" shadow log, past-pick tracking.

## Today's scan (job 0a12065e50)

- Params: `universe_preset=sp1500`, `scan_timeframe=weekly`, `strategy_ids=[trend_breakout, weekly_swing]`. Ran 08:12-08:49 CDT, 45 signals, 10 `executable=true` (JNJ, VRTX, LTC, LQDT, CENTA, TREX, UTL, DBX, WLY, SJM), so Discord Approve/Reject prompts may have gone out.
- Stage 2 ran: `stage2_fail=1198`, stage2_pass 296, `watchlist_size=1503` (`sp1500_default`). Passing both strategy ids fixes the 10-22 `stage2_fail=0` issue.
- Triggered: trend_breakout 39, weekly_swing 51; 8 of 45 final rows fire both.
- Data: `data_quality=ok`, provider schwab 1503/1503, `business_as_of=2026-10-23`. Minor: `df_empty=6`, `bars_stale=2`, `too_few_candles=1`, `silent_fallback_count=2`, `fallback_inconsistent_count=2`; none in the final 45. 0 unresolved sectors. 2 zero signal_scores (MRNA, GPC). Quality gates soft (35 would-filter).
- Flags among 45: 13 Altman distress, 44 high SEC risk (not usable as exclusion), setup_confidence 45.0 on 42 rows (neutral, no information).
- Buyout check: web search found no announced deal where LQDT, LTC, USPH, SONO, MMSI or CENTA is the target (all are acquirers). Large caps were not checked one by one.

## Ranked (rule: exclude >~8% over SMA50, Altman distress, buyout targets; sort by low stretch)

| # | tk | arm | price | ext50 | stop | exec |
|---|---|---|---|---|---|---|
| 1 | JNJ | TB | 267.61 | +0.9% | 250.30 | yes |
| 2 | VRTX | TB | 535.17 | +2.4% | 500.96 | yes |
| 3 | EMR | WS | 160.29 | +2.5% | 149.70 | no |
| 4 | LTC | both | 43.33 | +3.1% | 41.05 | yes |
| 5 | REGN | WS | 816.33 | +3.1% | 764.37 | no |
| 6 | LQDT | both | 44.30 | +3.7% | 41.83 | yes |
| 7 | DIS | WS | 112.42 | +5.2% | 106.80 | no |
| 8 | SONO | WS | 17.39 | +5.5% | 15.72 | no |
| 9 | CRM | WS | 248.45 | +5.7% | 229.63 | no |
| 10 | MMSI | WS | 93.59 | +5.8% | 87.99 | no |
| 11 | GILD | WS | 157.19 | +5.9% | 146.82 | no |
| 12 | USPH | WS | 87.73 | +6.9% | 80.38 | no |
| 13 | DLTR | WS | 131.04 | +7.4% | 122.37 | no |
| 14 | WRB | WS | 75.55 | +7.6% | 71.77 | no |
| 15 | BDX | WS | 199.93 | +7.8% | 189.93 | no |
| 16 | CENTA | both | 38.81 | +8.1% (borderline) | 36.87 | yes |

Below SMA50 (weekly_swing pullbacks, not Stage 2 continuation): SXT -1.1%, NDSN -1.1%, IEX -1.9%, PARR -4.1%, CHEF -4.8%.
Excluded: stretched VLTO, MORN, PLMR, CTAS, TREX (both), DFIN, MTD, EXLS, FDS, SIG, MRNA; Altman BLK, UTL (both), SBH, AMPH, ENR, DBX (both), WLY (both), SJM (both), AJG, EVTC, KSS, GPC, CRGY.

## "Both" shadow log (start; shadow only, no live change)

Per [[bt-2026-10-22-trend-breakout-vs-weekly-swing]]: TB entries filtered by same-week WS. Reference entry = 10-23 scan price; SPY reference = 10-22 close 763.96. Track T+5/T+10/T+20 excess vs SPY separately from other picks.

| tk | ref price | passes filters | note |
|---|---|---|---|
| LTC | 43.33 | yes | |
| LQDT | 44.30 | yes | |
| CENTA | 38.81 | borderline (+8.1%) | |
| TREX | 51.07 | no (+12.5%) | |
| UTL | 55.06 | no (Altman) | |
| DBX | 37.48 | no (Altman) | |
| WLY | 54.05 | no (Altman) | |
| SJM | 133.39 | no (Altman) | |

Log all 8 raw; report filtered (LTC, LQDT, CENTA) and unfiltered subsets.

## Past picks (866 ticker-date picks 9/23 to 10/22, entry = prior close, excess vs SPY)

| Horizon | n | Mean ret | Excess | Hit vs SPY |
|---|---|---|---|---|
| T+5 | 524 | -0.44% | +0.14% | 54% |
| T+10 | 369 | -0.46% | -0.29% | 55% |
| T+20 | 80 | +2.48% | +2.97% | 69% |

- Extension terciles: T+5 low +0.37%, mid +2.35%, high -0.56% (n=88 each); T+10 low +1.15%, mid +4.36%, high -4.81% (n~36). Spearman +0.02 (T+5), -0.18 (T+10).
- Read: same as 10-22. High extension loses at T+10; mid tercile still leads low. "Exclude high" stays firm; "lowest first" remains unproven versus mid. Windows overlap heavily.

## Related Pages

- [[scan-quality-2026-10-22]]
- [[bt-2026-10-22-trend-breakout-vs-weekly-swing]]
- [[quant-debate-2026-10-22]]
- [[signal-ranking]]

---

*Last compiled: 2026-10-23*
