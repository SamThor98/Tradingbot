---
source: Brain/Strategies/Sector Strength.md
created: 2026-04-13
updated: 2026-10-29
tags: [strategy, sector]
---

# Sector Strength

> Relative sector performance filter preferring stocks in sectors outperforming SPY.

## How It Works

1. `get_sector_heatmap()` computes relative performance of sector ETFs vs SPY
2. Scanner checks if stock's sector is "winning"
3. If `SECTOR_FILTER_ENABLED=true`, underperforming sectors are filtered out

## Sector Map Overrides

Yahoo sector names are translated to Select Sector SPDRs in `SECTOR_TO_ETF`. That table matches `main`: yfinance's singular `Consumer Defensive` label stays unresolved while the mode is `off`. `consumer staples` and `consumer defensives` still map to XLP.

Ticker-level sponsor membership is `schwab_skill/data/sector_map_overrides.json`. It is a point-in-time classification as of 2026-10-27 (Select Sector SPDRs, plus iShares IJH and IJR), not a historical sector map. `SECTOR_MAP_OVERRIDE_MODE` follows [[plugin-modes]]:

- `off` (default) — the scan keeps the yfinance ETF, including unresolved `Consumer Defensive`
- `shadow` — the scan still keeps the yfinance ETF and records `sector_map_override_disagree` (holdings mismatch, or `Consumer Defensive` → XLP)
- `live` — listed tickers use the sponsor ETF (UBER → XLI, CACI → XLI, SONO → XLY). Unlisted `Consumer Defensive` names use XLP

The override is applied at read time and is not written into `.sector_map_cache.json` as the resolved ETF, so turning the mode back to `off` restores the yfinance mapping. An audit of tracked watchlist names is `schwab_skill/data/sector_map_override_audit.md`. The operator's local `.sector_map_cache.json` is not in this repo and still needs the same check.

## Sector Exposure Guard

`MAX_SECTOR_ACCOUNT_FRACTION` in [[guardrails]] caps portfolio allocation to a single sector.

## Dashboard

`GET /api/sectors` returns the sector heatmap data.

## Related Pages

- [[signal-scanner]] — sector filter in Stage B
- [[guardrails]] — sector fraction cap
- [[scanner-tunables]] — `SECTOR_FILTER_ENABLED`
- [[plugin-modes]] — `SECTOR_MAP_OVERRIDE_MODE`
- [[plugin-modes-config]] — env var default for the override mode

---

*Last compiled: 2026-10-29*
