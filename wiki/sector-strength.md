---
source: Brain/Strategies/Sector Strength.md
created: 2026-04-13
updated: 2026-10-28
tags: [strategy, sector]
---

# Sector Strength

> Relative sector performance filter preferring stocks in sectors outperforming SPY.

## How It Works

1. `get_sector_heatmap()` computes relative performance of sector ETFs vs SPY
2. Scanner checks if stock's sector is "winning"
3. If `SECTOR_FILTER_ENABLED=true`, underperforming sectors are filtered out

## Sector Map Overrides

Yahoo sector names are translated to Select Sector SPDRs in `SECTOR_TO_ETF`. `consumer defensive` (the current yfinance label) maps to XLP along with `consumer staples`.

Ticker-level disagreements with State Street membership are a separate map, `schwab_skill/data/sector_map_overrides.json` (holdings as of 2026-10-27). `SECTOR_MAP_OVERRIDE_MODE` follows [[plugin-modes]]:

- `off` (default) — the scan keeps the yfinance ETF
- `shadow` — the scan still keeps the yfinance ETF and records `sector_map_override_disagree`
- `live` — listed tickers use the SPDR ETF (for example UBER → XLI, not XLK)

The override is applied at read time and is not written into `.sector_map_cache.json`, so turning the mode back to `off` restores the yfinance mapping.

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

*Last compiled: 2026-10-28*
