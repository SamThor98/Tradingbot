---
source: schwab_skill/webapp/webapp.db (scan_results job f488db0e6c, app_state last_scan); yfinance closes through 2026-10-27 plus a 10-28 premarket bar (about 08:55 CDT)
created: 2026-10-28
updated: 2026-10-28
tags: [scan-quality, ranking, tracking, shadow, trend-breakout, weekly-swing]
---

# Scan Quality 2026-10-28

> Scan desk premarket readout for 10-28: data health, ranked table, "both" shadow log update, past-pick tracking.

## Today's scan (job f488db0e6c)

- Params: `universe_preset=sp1500`, `scan_timeframe=weekly`, `strategy_ids=[trend_breakout, weekly_swing]`. Ran 08:12-08:50 CDT (2300 s), 32 signals, 4 `executable=true` (OKTA, NTAP, MTUS, AVNT), so Discord Approve/Reject prompts may have gone out. All four fail the ranking filters.
- Stage 2 ran: `stage2_fail=1178`, stage2 314, `watchlist_size=1503` (`sp1500_default`). Funnel 1503 > 314 > stage_a 112 > shortlist 71 > quality 60 > final 32.
- Triggered: trend_breakout 15, weekly_swing 36. Final 32 rows: 28 weekly_swing only, 3 both, 1 trend_breakout only.
- Data: `data_quality=ok`, primary provider 1503/1503, `business_as_of=2026-10-28` on all rows. Minor: `df_empty=6`, `bars_stale=3`, `too_few_candles=2`, `silent_fallback_count=3`, `fallback_inconsistent_count=3`, `unresolved_sector_symbols=1`. No final row used fallback data. 0 zero signal_scores. `company_name` blank on SBH, CTAS, SONO, USPH, DHR, MTUS (cosmetic).
- **Mixed price basis (flag).** 17 of 32 scan prices equal the 10-27 close exactly; the rest differ by 0.1% to 2.1% (BILL -2.06%, KSS -1.35%, SONO -1.45%, CLF -1.51%, CACI -1.05%, WDAY -0.98%, NHC +1.29%, VLO +1.22%). Same partial-bar issue quant found on 10-27 (CACI, RLI). Re-price live before acting.
- Flags among 32: 9 Altman distress; setup_confidence 45.0 on 26 rows (neutral). Buyout check: no deal found for PGNY (web search; Q3 report 11-04). Other qualifiers were checked on 10-27 or are large caps.

## Ranked (rule: exclude >~8% over SMA50, Altman distress, buyout targets; sort by low stretch)

| # | tk | arm | price | sma50 | sma200 | ext50 | stop | exec | note |
|---|---|---|---|---|---|---|---|---|---|
| 1 | CRM | WS | 240.89 | 237.01 | 198.50 | +1.6% | 221.24 | no | TB OOS negative (quant 10-27); earnings ~12-02 |
| 2 | TTEK | WS | 36.77 | 35.31 | 33.05 | +4.1% | 34.93 | no | earnings ~11-11; TB OOS negative |
| 3 | ADUS | WS | 121.83 | 116.81 | 106.20 | +4.3% | 115.74 | no | earnings ~11-03; WS OOS negative |
| 4 | PGNY | WS | 27.96 | 26.76 | 24.33 | +4.5% | 26.07 | no | new; earnings 11-04 |
| 5 | WDAY | WS | 203.44 | 194.10 | 155.61 | +4.8% | 188.53 | no | |
| 6 | BDX | WS | 195.03 | 185.89 | 165.43 | +4.9% | 185.28 | no | earnings ~11-05 |
| 7 | CTAS | WS | 214.80 | 203.56 | 189.90 | +5.5% | 203.65 | no | was stretched on 10-27 |
| 8 | CACI | WS | 659.25 | 623.81 | 567.99 | +5.7% | 614.48 | no | partial-bar price |
| 9 | SONO | WS | 17.61 | 16.60 | 15.26 | +6.1% | 16.30 | no | partial-bar price |
| 10 | USPH | WS | 87.75 | 82.56 | 76.75 | +6.3% | 80.72 | no | |
| 11 | DHR | WS | 232.00 | 217.33 | 201.16 | +6.8% | 216.04 | no | |
| 12 | ELF | WS | 113.02 | 104.95 | 81.04 | +7.7% | 104.75 | no | |

No qualifier has a live trend_breakout trigger; all 12 are weekly_swing-only (shadow strategy). Below SMA50: NHC -5.5%, VLO -0.9% (weekly_swing pullbacks).
Excluded: stretched EL (+9.0%), NDAQ, NTAP (both, +11.5%), ROP, IT, MTD, SIG, OKTA (both, +20.2%), PTC; Altman AVNT (TB, exec), MTUS (both, exec), BILL, AMPH, SBH, PRGS, CLF, CXT, KSS.
Dropped from 10-27 list: PAYX, FBNC, NTNX, PKG, RLI, MRSH (no longer firing).

## "Both" shadow log (shadow only, no live change)

Context: [[bt-2026-10-27-both-lookahead-free]] found no lookahead-free "both" edge. The log continues as a live check only. Returns are to the 10-28 premarket bar (about 08:55 CDT), so treat as approximate.

10-23 cohort (SPY ref 10-22 close 763.96, now 769.61, +0.74%):

| tk | ref | now | ret | excess | filters |
|---|---|---|---|---|---|
| LTC | 43.33 | 41.92 | -3.24% | -3.98% | pass |
| LQDT | 44.30 | 45.06 | +1.73% | +0.99% | pass |
| CENTA | 38.81 | 38.47 | -0.86% | -1.60% | borderline |
| TREX | 51.07 | 50.17 | -1.77% | -2.51% | fail |
| UTL | 55.06 | 54.93 | -0.24% | -0.98% | fail |
| DBX | 37.48 | 36.99 | -1.31% | -2.05% | fail |
| WLY | 54.05 | 52.24 | -3.35% | -4.09% | fail |
| SJM | 133.39 | 126.31 | -5.31% | -6.05% | fail |

Filtered 3: mean -0.79%, excess -1.53% (was +1.13% on 10-27). Filter-failed 5: mean -2.40%, excess -3.14%. All 8: mean -1.79%, excess -2.53%. Filtered still beats failed, but both are now below SPY.

10-27 cohort (SPY ref 10-26 close 765.37, +0.55%): FBNC -3.18% (excess -3.74%), NTNX +0.63% (+0.08%), PKG -1.45% (-2.00%) pass filters, mean excess -1.89%; MTN +2.03% (+1.47%, Altman), CART -2.08% (-2.63%, stretched).

10-26 manual scan (not a scan-desk run, SPY ref 10-23 close, +1.27%): NTCT -1.94%, NTAP +2.69%, VSXY -1.51%.

10-28 cohort added (SPY ref 10-27 close 769.66), all unfiltered: OKTA 226.07 (+20.2% stretched), NTAP 227.58 (+11.5% stretched, also "both" on 10-26), MTUS 21.13 (Altman).

## Past picks (901 ticker-date picks 9/28 to 10/28, entry = prior close, excess vs SPY)

| Horizon | n | Mean ret | Excess | Hit vs SPY |
|---|---|---|---|---|
| T+5 | 744 | +0.16% | +0.63% | 60% |
| T+10 | 365 | -0.50% | +0.37% | 62% |
| T+20 | 44 | +1.32% | +2.80% | 68% |

- The DB now starts at 9/28 (was 9/26 on 10-27), so older picks roll off and T+20 n fell from 84 to 44.
- Extension terciles: T+5 low +0.42%, mid +1.22%, high +0.26% (n=248 each), Spearman +0.01; T+10 low +1.03%, mid +3.41%, high -3.30% (n~122), Spearman -0.10.
- The 8% cut: T+10 excess +0.99% for ext <= 8% (n=334) vs -6.25% for ext > 8% (n=31). T+5: +0.78% (n=622) vs -0.10% (n=122).
- Read: unchanged. The >8% exclusion keeps paying at T+10; "lowest stretch first" is still not supported (mid tercile leads). Windows overlap and the last bar is premarket.

## Related Pages

- [[scan-quality-2026-10-27]]
- [[bt-2026-10-27-both-lookahead-free]]
- [[bt-2026-10-22-trend-breakout-vs-weekly-swing]]
- [[quant-debate-2026-10-27]]
- [[signal-ranking]]

---

*Last compiled: 2026-10-28*
