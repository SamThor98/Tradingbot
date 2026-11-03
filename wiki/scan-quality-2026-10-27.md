---
source: schwab_skill/webapp/webapp.db (scan_results job 6089c1d034, app_state last_scan and prev_scan); yfinance closes through 2026-10-26 plus 10-27 intraday (about 08:50 CDT)
created: 2026-10-27
updated: 2026-10-27
tags: [scan-quality, ranking, tracking, shadow, trend-breakout, weekly-swing]
---

# Scan Quality 2026-10-27

> Scan desk premarket readout for 10-27: data health, ranked table, "both" shadow log update, past-pick tracking.

## Today's scan (job 6089c1d034)

- Params: `universe_preset=sp1500`, `scan_timeframe=weekly`, `strategy_ids=[trend_breakout, weekly_swing]`. Ran 08:11-08:49 CDT (2297 s), 37 signals, 5 `executable=true` (NTNX, PKG, FBNC, CART, MTN), so Discord Approve/Reject prompts may have gone out.
- Stage 2 ran: `stage2_fail=1176`, stage2_pass 316, `watchlist_size=1503` (`sp1500_default`).
- Triggered in the run: trend_breakout 21, weekly_swing 49. Final 37 rows: 32 weekly_swing only, 5 both, 0 trend_breakout only.
- Data: `data_quality=ok`, primary provider 1503/1503, `business_as_of=2026-10-27`. Minor: `df_empty=7`, `bars_stale=3`, `too_few_candles=1`, `silent_fallback_count=3`, `fallback_inconsistent_count=3`, `unresolved_sector_symbols=1` (cache 8). None of the final 37 used fallback data. 0 zero signal_scores. `company_name` is blank on 7 rows (MTN, SBH, SONO, DHR, USPH, CTAS, CART), cosmetic only.
- Flags among 37: 10 Altman distress; NTNX `sloan_high`; setup_confidence 45.0 on 33 rows (neutral, no information).
- Buyout check (web search): FBNC, ADUS, NTNX, PKG, PAYX, ELF are acquirers, not targets; nothing found for NHC, UFPT, TTEK, RLI, SONO. Large caps not checked one by one.

## 10-26 gap

- No premarket scan with the standard params is in the DB for 10-26 and no scan-quality page was written. The only 10-26 scan (job 806d20ac83, 10:46 CDT) requested all 16 strategy ids, had `data_quality=degraded` (SEC cache 72.6 h old), and 28 of its 40 rows fired neither trend_breakout nor weekly_swing. It is kept in past-pick tracking but not treated as a scan-desk run.

## Ranked (rule: exclude >~8% over SMA50, Altman distress, buyout targets; sort by low stretch)

| # | tk | arm | price | sma50 | sma200 | ext50 | stop | exec |
|---|---|---|---|---|---|---|---|---|
| 1 | PAYX | WS | 115.70 | 113.70 | 103.46 | +1.8% | 109.53 | no |
| 2 | ADUS | WS | 121.64 | 116.74 | 106.15 | +4.2% | 115.56 | no |
| 3 | FBNC | both | 65.62 | 62.82 | 60.40 | +4.5% | 62.09 | yes |
| 4 | CRM | WS | 247.61 | 236.01 | 198.60 | +4.9% | 228.35 | no |
| 5 | CACI | WS | 655.02 | 623.52 | 567.66 | +5.1% | 608.81 | no |
| 6 | TTEK | WS | 37.07 | 35.28 | 33.05 | +5.1% | 35.22 | no |
| 7 | NTNX | both | 72.71 | 69.11 | 52.08 | +5.2% | 66.69 | yes (sloan_high) |
| 8 | BDX | WS | 196.39 | 185.63 | 165.25 | +5.8% | 186.57 | no |
| 9 | RLI | WS | 64.22 | 60.66 | 57.56 | +5.9% | 60.04 | no |
| 10 | SONO | WS | 17.54 | 16.55 | 15.26 | +6.0% | 16.25 | no |
| 11 | WDAY | WS | 206.13 | 193.86 | 155.63 | +6.3% | 191.23 | no |
| 12 | PKG | both | 253.74 | 237.21 | 228.72 | +7.0% | 241.05 | yes |
| 13 | MRSH | WS | 194.72 | 181.73 | 176.96 | +7.1% | 184.33 | no |
| 14 | DHR | WS | 232.97 | 216.74 | 201.19 | +7.5% | 216.76 | no |
| 15 | ELF | WS | 112.72 | 104.53 | 80.90 | +7.8% | 104.73 | no |
| 16 | USPH | WS | 88.93 | 82.40 | 76.75 | +7.9% | 82.15 | no |

Below SMA50 (weekly_swing pullbacks, not Stage 2 continuation): UFPT -0.8%, NHC -1.5%.
Excluded: stretched HQY (+8.6%), EL, NDAQ, CTAS, MTD, ROP, SIG, IT, PTC, CART (both, +25.8%); Altman MTN (both), AMPH, SBH, BILL, GO, PRGS, CLF, CXT, KSS.

## "Both" shadow log (shadow only, no live change)

Per [[bt-2026-10-22-trend-breakout-vs-weekly-swing]]. Returns are to 10-27 intraday (about 08:50 CDT), so about 2 sessions; anecdotal until T+5.

10-23 cohort (SPY ref 763.96, now 767.52, +0.47%):

| tk | ref | now | ret | filters |
|---|---|---|---|---|
| LTC | 43.33 | 43.76 | +1.00% | pass |
| LQDT | 44.30 | 45.92 | +3.66% | pass |
| CENTA | 38.81 | 38.86 | +0.13% | borderline |
| TREX | 51.07 | 49.14 | -3.78% | fail |
| UTL | 55.06 | 55.17 | +0.20% | fail |
| DBX | 37.48 | 37.55 | +0.19% | fail |
| WLY | 54.05 | 53.26 | -1.47% | fail |
| SJM | 133.39 | 127.73 | -4.24% | fail |

Filtered 3: mean +1.60%, excess +1.13%. All 8: mean -0.54%, excess -1.01%. Filter-failed 5: mean -1.82%.

10-26 manual scan "both" (not a scan-desk run; ref = scan price, SPY ref 10-23 close 759.97, now +0.99%): NTCT 40.80 to 39.23 (-3.85%), NTAP 220.22 to 225.45 (+2.37%), VSXY 95.59 to 93.70 (-1.98%). Kept separate, anecdotal.

10-27 cohort added (SPY ref = 10-26 close 765.37): FBNC 65.62, NTNX 72.71, PKG 253.74 (pass filters); MTN 149.40 (Altman), CART 60.88 (+25.8%) unfiltered.

## Past picks (872 ticker-date picks 9/26 to 10/26, entry = prior close, excess vs SPY)

| Horizon | n | Mean ret | Excess | Hit vs SPY |
|---|---|---|---|---|
| T+5 | 564 | -0.01% | +0.48% | 59% |
| T+10 | 367 | -0.42% | +0.35% | 61% |
| T+20 | 84 | +1.65% | +3.12% | 70% |

- Extension terciles: T+5 low -0.03%, mid +1.14%, high +0.35% (n=188 each); T+10 low +1.21%, mid +2.94%, high -3.09% (n~122). Spearman +0.07 (T+5), -0.11 (T+10).
- The 8% cut: T+10 excess +0.99% for ext <= 8% (n=334) vs -6.05% for ext > 8% (n=33). T+5: +0.67% (n=508) vs -1.23% (n=56).
- Read: same as 10-23. Excluding >8% stretch keeps paying at T+10. The mid tercile still beats the lowest, so "lowest stretch first" is still unproven as a sort order. Windows overlap heavily, and the last bar is 10-27 intraday.

## Related Pages

- [[scan-quality-2026-10-23]]
- [[bt-2026-10-22-trend-breakout-vs-weekly-swing]]
- [[quant-debate-2026-10-26]]
- [[signal-ranking]]

---

*Last compiled: 2026-10-27*