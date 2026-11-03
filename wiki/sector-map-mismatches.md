---
source: experiments/backtester/debate_2026_10_28.py
created: 2026-10-28
updated: 2026-10-28
tags: [backtest, sector-map, data-quality, relative-performance, proposed-change]
---

# Sector map mismatches (repo map vs GICS), found 2026-10-28

> Three names are mapped to the wrong sector ETF in the repo's sector map: CACI, SONO and PKG. Any "vs sector" excess for them is unreliable. "vs SPY" is unaffected. The repo mapping is NOT changed; a code fix is proposed below for Sam to decide.

Found by the backtester while running [[bt-2026-10-27-debate-candidates]] and [[bt-2026-10-28-ws-only-candidates]]. Offline and read-only. No app code, orders or Schwab endpoints were touched.

## Mismatches

| Name | Repo map | Actual GICS sector | Correct ETF | Seen in |
|---|---|---|---|---|
| CACI | XLK | Industrials (Diversified Support Services) | XLI | [[bt-2026-10-27-debate-candidates]], [[bt-2026-10-28-ws-only-candidates]] |
| SONO | XLK | Consumer Discretionary (Consumer Electronics) | XLY | [[bt-2026-10-28-ws-only-candidates]] |
| PKG | XLY | Materials (Containers & Packaging) | XLB | [[bt-2026-10-27-debate-candidates]] |

Source of the repo map: `.sector_map_cache.json`, as read by the backtester scripts (the same map the repo uses for sector comparisons; see [[sector-strength]]).

## Effect on backtest results

- **vs-sector excess is unreliable** for CACI, SONO and PKG. It compares each trade to the wrong ETF over the same window. Sensitivity against the corrected ETFs was not run, so the size and sign of the error are unknown.
- **vs-SPY excess is unaffected.** The SPY benchmark does not use the sector map. Rankings and veto calls in the two backtest notes rest on vs-SPY and are not changed by this.
- Shrinkage and the pooled setup numbers use vs-SPY and are not affected either.

## Looser comparisons (map is right, benchmark is weak)

- **CRM and WDAY** map correctly to XLK (Information Technology). XLK is dominated by semiconductors and hardware, so it is a loose comparison for software names. Treat their vs-sector figures as indicative only.
- **PAYX** is also mapped to XLK in the repo (noted in [[bt-2026-10-27-debate-candidates]]). It is probably Industrials today after the GICS 2023 reclassification, but this was not verified. Check it with the fix below.

## Verification status

GICS was looked up for CACI, TTEK, ELF and SONO. PKG and the rest of the listed names are from general knowledge. The map is today's, not point-in-time, so sector history for older trades may also differ.

## Proposed change (not made)

Proposal for Sam to decide, after the live-stack freeze ends 2026-11-07 or sooner if it is research-only:
1. Correct the three entries above (and check PAYX) in the sector map source, or add a small override table checked before the cached map.
2. Audit the whole cached map against a GICS source and log every disagreement.
3. For software names, add a software comparison (for example an IGV-style ETF) next to XLK.
4. Re-run the vs-sector columns for CACI, SONO and PKG against the corrected ETFs.

The repo mapping is unchanged. The backtester does not edit app code; a Cursor prompt can be written if Sam wants one.

## Fix prompt (ready, not applied)

A self-contained Cursor prompt is at `experiments/backtester/cursor_prompt_sector_map_fix.md`. Where the map lives: `schwab_skill/sector_strength.py` (`SECTOR_TO_ETF` :20-36, resolver `get_ticker_sector_etf` :245-287) reading the gitignored runtime cache `schwab_skill/.sector_map_cache.json` (1,527 tickers, Yahoo/Morningstar labels, not GICS, cached forever). The map feeds live decisions (Stage A sector gate `signal_scanner.py:1580`, concentration cap `execution.py:327-335`), so the prompt ships the fix as an override file behind `SECTOR_MAP_OVERRIDES_MODE=off|shadow|on` (default `off`), with `on` only after the 2026-11-07 freeze via Sam's signed promotion ledger entry. It also audits the whole map against GICS, adds an offline unit test, and reruns vs-sector for CACI, SONO, PKG and PAYX. PAYX stays unverified until Cursor confirms it against a GICS source. Nothing has been applied; the repo mapping is unchanged.

## Related Pages
- [[bt-2026-10-27-debate-candidates]] - run where CACI and PKG were mapped wrong
- [[bt-2026-10-28-ws-only-candidates]] - run that checked the map for 12 names and found CACI and SONO
- [[sector-strength]] - how the repo uses sector relative performance
- [[horizon-strategies]] - weekly_swing rule definition
- [[promotion-playbook]] - signed ledger entry needed before the override flag goes `on`

---

*Last compiled: 2026-10-28*
