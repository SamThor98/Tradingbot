---
source: schwab_skill/webapp/webapp.db (scan_results job 174805e7cc, app_state last_scan); yfinance daily closes through 2026-11-02 intraday
created: 2026-11-02
updated: 2026-11-02
tags: [scan-quality, ranking, tracking, shadow, trend-breakout, weekly-swing, data-quality]
---

# Scan Quality 2026-11-02

> Scan desk on-demand intraday rerun for 11-02 (the 8:00 routine was skipped because DESKTOP-B7O4H9H was offline). Schwab data is healthy again. Ranked table, "both" tracking, past-pick catch-up 10-23 to 10-29.

## Today's scan (job 174805e7cc)

- Params: `universe_preset=sp1500`, `scan_timeframe=weekly`, `strategy_ids=[trend_breakout, weekly_swing]`. Ran 12:03-12:43 CST (2439 s), intraday, so prices are live, not premarket. 40 signals, 9 `executable=true` (HQY, STBA, WOR, TILE, FULT, SEIC, WSBC, HWC, KFY), so Discord Approve/Reject prompts may have gone out.
- Stage 2 ran: `stage2_fail=1179`, `watchlist_size=1503` (`sp1500_default`), stage_a 148 > shortlist 104 > final 40. Triggered: trend_breakout 36, weekly_swing 56. Final 40: 31 WS only, 4 TB only, 5 both.
- **Data: Schwab recovered.** `data_provider_primary_count=1503`, fallback 0, `data_quality=ok`, regime history from schwab. The Oct 29 outage has cleared. Minor: `df_empty=9`, `bars_stale=1`, `too_few_candles=1`, `unresolved_sector_symbols=1`, `silent_fallback_count=1`, `fallback_inconsistent_count=1`. 0 zero scores. `business_as_of=2026-11-02`.
- Other scans found in scan_results: job e13ee1236a at 11:28 CST today (41 rows) and f4d81b41ed on 10-30 at 09:46 CDT (39 rows). Scan desk did not start these.
- Regime: SPY 758.18 vs SMA200 729.18, regime_v2 66.45 (medium).
- Sector map still wrong for CACI (XLK).
- Buyout check (web search): no announced deal found for the qualifiers. HWC is an acquirer (One Florida, all cash, announced 05-15).

## Ranked (exclude >~8% over SMA50, Altman distress, buyout targets; sort by low stretch)

| # | tk | arm | price | sma50 | ext50 | stop | ATR14 | earnings | exec | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | INSW | WS | 106.05 | 108.63 | -2.4% | 93.32 | 6.04 | 11-05 | no | below SMA50 (pullback); earnings this week |
| 2 | RGEN | WS | 173.95 | 177.91 | -2.2% | 153.08 | 9.02 | ? (feed shows 10-27, stale) | no | below SMA50; Altman n/a |
| 3 | HWC | TB | 73.90 | 73.60 | +0.4% | 68.91 | 2.00 | 01-19 | yes | Altman n/a (bank) |
| 4 | SEIC | TB | 106.42 | 105.93 | +0.5% | 100.34 | 2.44 | 01-27 | yes | |
| 5 | OKTA | WS | 197.51 | 194.03 | +1.8% | 173.81 | 14.24 | 12-01 | no | was "both" on 10-28 |
| 6 | WSBC | TB | 39.65 | 38.80 | +2.2% | 37.58 | 0.83 | 01-26 | yes | Altman n/a (bank) |
| 7 | KFY | TB | 79.16 | 76.74 | +3.2% | 74.07 | 2.25 | 12-08 | yes | |
| 8 | FULT | both | 24.16 | 23.12 | +4.5% | 22.95 | 0.52 | 01-20 | yes | Altman n/a (bank) |
| 9 | DHR | WS | 229.32 | 219.06 | +4.7% | 211.92 | 6.91 | 01-27 | no | |
| 10 | STBA | both | 52.21 | 49.61 | +5.2% | 49.60 | 1.15 | 01-21 | yes | Altman n/a (bank) |
| 11 | BDX | WS | 199.12 | 186.74 | +6.6% | 189.16 | 3.74 | 11-05 | no | Altman 1.84 (grey); earnings this week |
| 12 | WOR | both | 65.05 | 60.69 | +7.2% | 61.64 | 1.52 | 12-15 | yes | Altman n/a |

"both" is a hit type only, no edge claim. Stop = scan stop_pct applied to scan price. Earnings from the yfinance calendar.
Borderline (excluded at +8.3%): TILE (both, exec), earnings 11-06.
Excluded, stretched: CACI, AGYS, HQY (both, exec, +10.0%), J, TTEK, AJG, GILD, NDAQ, EXLS, MORN, GDDY, QTWO, VLTO, MHK, CERT, DXC, ALLE, EXPO, REGN, ROP, HAS, ACN, DFIN (+36.8%). Altman distress: IQV, AMPH, PRGS, SBH.

## "Both" and past-pick catch-up (to the 11-02 intraday price, entry = prior close, excess vs SPY)

SPY fell over the window, so excess is flattered for everything.

| cohort | SPY | n | <=8% & no distress: mean excess | failed: mean excess | "both" names (excess) |
|---|---|---|---|---|---|
| 10-23 | -0.86% | 45 | +0.77% (20) | +3.39% (25) | LTC +5.17, LQDT +3.70, CENTA +4.00, UTL +2.94, TREX +0.07, SJM -0.54, DBX -2.81, WLY -3.18; mean +1.17 |
| 10-27 | -1.04% | 37 | -0.14% (18) | +3.42% (19) | NTNX -8.30, PKG +2.41, FBNC +1.16, CART -4.48, MTN +2.77; mean -1.29 |
| 10-28 | -1.59% | 32 | +1.64% (14) | +3.06% (18) | OKTA -11.97, NTAP -14.90, MTUS +3.10; mean -7.92 |
| 10-29 | -1.93% | 33 | +5.11% (14) | +3.44% (19) | RVTY -5.81, CRL -6.32, SWKS +0.45; mean -3.89 |

Read:
- The 10-23 "both" cohort now beats SPY (+1.17% excess). This is the first one to do so, so "every both cohort trails SPY" no longer holds. Three of four cohorts still trail. Short, overlapping windows; no edge claim either way.
- In 3 of 4 short cohorts the filter-failed (stretched or distressed) names beat the filtered names during the SPY pullback. This conflicts with the earlier T+10 result (>8% trailing by 6.25%). Small n; keep the rule and re-check at T+10/T+20.
- Not done: [[bt-2026-10-27-both-lookahead-free]] not re-verified this run.

## Related Pages

- [[scan-quality-2026-10-29]]
- [[bt-2026-10-27-both-lookahead-free]]
- [[sector-map-mismatches]]
- [[signal-ranking]]

---

*Last compiled: 2026-11-02*
