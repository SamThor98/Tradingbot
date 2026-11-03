---
source: schwab_skill/webapp/webapp.db (scan_results job 23576331d4, app_state last_scan); yfinance closes through 2026-10-21
created: 2026-10-22
updated: 2026-10-22
tags: [scan-quality, ranking, tracking]
---

# Scan Quality 2026-10-22

> Scan desk premarket readout for 10-22: data health and past-pick tracking.

## Today's scan (job 23576331d4)

- Params: `universe_preset=sp1500`, `scan_timeframe=weekly` (Weekly Stage 2). Ran 08:04-08:35 CDT, 221 signals, all `executable=false`.
- Universe fixed: `watchlist_size=1503`, `watchlist_source=sp1500_default` (was 100 names on 10-19 to 10-21 premarket).
- Data: `data_quality=ok`, provider schwab on all rows, `business_as_of=2026-10-22`, 0 empty/stale/short bars, 0 unresolved sectors, 0 zero scores.
- Suspect: `stage2_fail=0` and `quality_gates_filtered=0` (gates in soft mode, 72 would-filter) versus `stage2_fail=1196`, 37 signals on yesterday's full-universe run (a8a4f38585). Signal count jumped 6x; check whether the Stage 2 gate is counting/applied on weekly.
- Flags: 142 Altman distress, 211 of 221 high SEC risk (too common to use as an exclusion), 152 low breakout volume, 16 rows with setup_confidence > 45.

## Past picks (645 ticker-date picks 9/23 to 10/21, entry = prior close, excess vs SPY)

| Horizon | n | Mean ret | Excess | Hit vs SPY |
|---|---|---|---|---|
| T+5 | 485 | -0.47% | +0.08% | 53% |
| T+10 | 325 | -0.53% | -0.51% | 53% |
| T+20 | 40 | +1.37% | +1.93% | 68% |

- Extension terciles (rows with `extension_pct_sma50`): T+5 low +0.87%, mid +2.81%, high -1.63% (n=75 each); T+10 low +0.72%, mid +4.33%, high -6.23% (n~22 each). Spearman -0.09 (T+5), -0.21 (T+10).
- Read: high extension keeps losing. Lowest extension is not clearly best; mid tercile led both horizons. Thin, overlapping windows. Keep "exclude high extension" as the firm rule; "rank strictly by lowest extension" is unproven against mid.

## Related Pages

- [[scan-quality-2026-10-21]]
- [[quant-debate-2026-10-21]]
- [[scan-confidence]]
- [[signal-ranking]]

---

*Last compiled: 2026-10-22*
