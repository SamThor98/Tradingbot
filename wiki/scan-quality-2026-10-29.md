---
source: schwab_skill/webapp/webapp.db (scan_results job f60805d0a0, app_state last_scan); yfinance closes through 2026-10-28 plus a 10-29 premarket bar (about 08:50 CDT)
created: 2026-10-29
updated: 2026-10-29
tags: [scan-quality, ranking, tracking, shadow, trend-breakout, weekly-swing, data-quality]
---

# Scan Quality 2026-10-29

> Scan desk premarket readout for 10-29: Schwab primary data was down for the whole scan (every row on yfinance fallback), ranked table, "both" shadow log update, past-pick tracking.

## Today's scan (job f60805d0a0)

- Params: `universe_preset=sp1500`, `scan_timeframe=weekly`, `strategy_ids=[trend_breakout, weekly_swing]`. Ran 08:03-08:42 CDT (2393 s), 33 signals, 3 `executable=true` (RVTY, CRL, SWKS, all "both"), so Discord Approve/Reject prompts may have gone out. All three are stretched >8% over SMA50 and fail the ranking filters.
- Stage 2 ran: `stage2_fail=1161`, stage2 326, `watchlist_size=1503` (`sp1500_default`). Funnel 1503 > 326 > stage_a 130 > shortlist 69 > quality 58 > final 33.
- Triggered: trend_breakout 10, weekly_swing 39. Final 33 rows: 30 weekly_swing only, 3 both.
- **Data (flag): primary provider 0/1503.** `data_provider_fallback_count=1503`, every row `data_provider=yfinance`, `fallback_reason=HTTPError|yfinance_ok`, `data_quality=degraded` (`quote_unavailable`). The Schwab market-data feed returned HTTP errors for the whole run, likely an auth or token problem. The scan-desk did not touch Schwab. Other: `df_empty=14` (was 6), `bars_stale=1`, `too_few_candles=1`, `unresolved_sector_symbols=1`, 0 zero signal_scores, `business_as_of=2026-10-29` on all rows.
- Price basis is consistent today: 30 of 33 scan prices equal the 10-28 close (PRGS -3.6%, SBH +1.7%, USPH +1.6% off).
- Premarket gaps vs scan price (yfinance 10-29 bar, about 08:50 CDT): CRM -4.7% (230.79), WDAY -3.7% (196.76), DFIN +8.2% (57.46, would be about +16% over SMA50). Re-price live before acting.
- Flags among 33: 8 Altman distress; setup_confidence 45.0 on 28 rows (neutral). Buyout check on new names (UFPT, HRB, FIZZ, DFIN): no announced deal found (web search). UFPT closed a small bolt-on (CI Medical) as acquirer on 09-07.

## Ranked (rule: exclude >~8% over SMA50, Altman distress, buyout targets; sort by low stretch)

| # | tk | arm | price | sma50 | sma200 | ext50 | stop | exec | note |
|---|---|---|---|---|---|---|---|---|---|
| 1 | CRM | WS | 242.11 | 237.76 | 197.73 | +1.8% | 220.28 | no | premarket -4.7%; earnings ~12-02 |
| 2 | HRB | WS | 47.80 | 46.72 | 38.53 | +2.3% | 44.07 | no | new |
| 3 | TTEK | WS | 36.78 | 35.31 | 32.96 | +4.2% | 34.94 | no | earnings ~11-11 |
| 4 | CTAS | WS | 212.81 | 203.83 | 189.38 | +4.4% | 201.66 | no | |
| 5 | ADUS | WS | 122.18 | 116.82 | 106.20 | +4.6% | 116.07 | no | earnings ~11-03 |
| 6 | USPH | WS | 86.68 | 82.65 | 76.07 | +4.9% | 79.45 | no | |
| 7 | WDAY | WS | 204.36 | 194.12 | 155.61 | +5.3% | 189.03 | no | premarket -3.7% |
| 8 | SONO | WS | 17.73 | 16.63 | 15.27 | +6.6% | 16.45 | no | sector map wrong (XLK, should be XLY) |
| 9 | FIZZ | WS | 34.27 | 32.13 | 31.61 | +6.7% | 31.80 | no | new |
| 10 | CACI | WS | 666.26 | 623.95 | 568.02 | +6.8% | 620.89 | no | sector map wrong (XLK, should be XLI) |
| 11 | DFIN | WS | 53.12 | 49.44 | 47.22 | +7.4% | 49.34 | no | new; premarket +8.2% |
| 12 | DHR | WS | 234.07 | 217.80 | 200.48 | +7.5% | 217.81 | no | |

No qualifier has a trend_breakout trigger; all 12 are weekly_swing-only. Below SMA50 (weekly_swing pullbacks): UFPT -2.0%, VLO -1.8%.
Excluded: stretched EL (+9.5%), SWKS (both, exec, +9.5%), ELF, IT, ROP, MTD, ALLE, RVTY (both, exec, +15.7%), PTC, HAS, CRL (both, exec, +25.1%); Altman ZD, BILL, PRGS, GO, AMPH, SBH, CLF, CXT.
Dropped from 10-28 list: PGNY, BDX (no longer firing).

## "Both" shadow log (shadow only, no live change)

Context: [[bt-2026-10-27-both-lookahead-free]] found no lookahead-free "both" edge. Returns to the 10-28 close.

10-23 cohort (SPY ref 10-22 close 763.96, now 772.31, +1.09%):

| tk | ref | now | ret | excess | filters |
|---|---|---|---|---|---|
| LTC | 43.33 | 42.73 | -1.38% | -2.48% | pass |
| LQDT | 44.30 | 43.84 | -1.04% | -2.13% | pass |
| CENTA | 38.81 | 38.31 | -1.29% | -2.38% | borderline |
| TREX | 51.07 | 49.54 | -3.00% | -4.09% | fail |
| UTL | 55.06 | 54.75 | -0.56% | -1.66% | fail |
| DBX | 37.48 | 36.51 | -2.59% | -3.68% | fail |
| WLY | 54.05 | 50.58 | -6.42% | -7.51% | fail |
| SJM | 133.39 | 125.46 | -5.94% | -7.04% | fail |

Filtered 3: mean -1.24%, excess -2.33%. Filter-failed 5: mean -3.70%, excess -4.79%. All 8: mean -2.78%, excess -3.87%. Filtered still beats failed; both are below SPY.

10-27 cohort (SPY ref 10-26 close 765.37, +0.91%): FBNC -3.44% (excess -4.35%), NTNX -0.06% (-0.96%), PKG -2.09% (-3.00%) pass filters, mean excess -2.77%; MTN +1.71% (+0.80%, Altman), CART -4.04% (-4.95%, stretched).

10-28 cohort (SPY ref 10-27 close 769.66, +0.34%): OKTA -1.51% (-1.85%, stretched), NTAP -1.72% (-2.07%, stretched), MTUS +0.19% (-0.16%, Altman).

10-26 manual scan (SPY ref 10-23 close, +1.62%): NTCT -4.74%, NTAP -0.06%, VSXY -1.82% excess.

10-29 cohort added (SPY ref 10-28 close 772.31), all unfiltered (stretched): SWKS 87.23 (+9.5%), RVTY 165.33 (+15.7%), CRL 373.98 (+25.1%).

Running read: every "both" cohort so far trails SPY. Filtered names trail less than filter-failed names.

## Past picks (901 ticker-date picks 9/28 to 10/28, entry = prior close, excess vs SPY)

| Horizon | n | Mean ret | Excess | Hit vs SPY |
|---|---|---|---|---|
| T+5 | 744 | +0.08% | +0.45% | 59% |
| T+10 | 365 | -0.48% | +0.36% | 62% |
| T+20 | 44 | +1.32% | +2.80% | 68% |

- Extension terciles: T+5 low +0.40%, mid +1.06%, high -0.12% (n=248 each), Spearman -0.02; T+10 low +1.01%, mid +3.40%, high -3.31% (n~122), Spearman -0.10.
- The 8% cut: T+10 excess +0.97% for ext <= 8% (n=334) vs -6.25% for ext > 8% (n=31). T+5: +0.62% (n=622) vs -0.46% (n=122).
- Read: unchanged. The >8% exclusion keeps paying; "lowest stretch first" is still not supported (mid tercile leads). Windows overlap.

## Related Pages

- [[scan-quality-2026-10-28]]
- [[bt-2026-10-27-both-lookahead-free]]
- [[bt-2026-10-22-trend-breakout-vs-weekly-swing]]
- [[sector-map-mismatches]]
- [[signal-ranking]]

---

*Last compiled: 2026-10-29*