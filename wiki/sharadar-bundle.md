---
source: schwab_skill/sharadar_data.py
created: 2026-09-21
updated: 2026-09-21
tags: [research, sharadar, pit, dashboard, fundamentals]
---

# Sharadar Bundle dashboard

> Point-in-time Sharadar Bundle snapshot on Research → Quick check. Does not change scan ranking, sizing, or advisory.

## What it shows

Paid Bundle tables (fundamentals, daily multiples, tickers, insiders, `holdings_ticker`, actions, events, `sp500`) are fetched once per ticker via `get_sharadar_research_snapshot` and reused on:

- `#sharadarBundleSection` (ART sparkline on **datekey**, 13F, Form 4, corporate-action strip)
- Dossier metrics (`source_metadata` / per-metric `sharadar` vs `finnhub` vs `report.*`)
- Decision-card chips (market cap, EV/EBITDA, P/E, P/S — nulls omitted)
- Pricing model **Sharadar Bundle** column beside SEC companyfacts DCF (not blended)
- Research forensic notes when ART can compute Sloan / Beneish / Altman (`data_lineage` includes `sharadar_art`)
- `#sharadarCompareSection` ticker vs ticker compare

Missing fields stay empty — never filled with zeros. Live `PIT_UNIVERSE_PROVIDER` stays `stub`. `core/scoring_rank_v2.py` and `FORENSIC_FILTER_MODE` are untouched. Scanner `forensic_accounting.py` still uses yfinance.

```
SHARADAR_API_KEY → /api/research/sharadar/{ticker} → Quick check + dossier + decision card + pricing column
```

S&P 500 membership is a **current** flag for the ticker, not a replacement for the Wikipedia live watchlist. Live `PIT_UNIVERSE_PROVIDER` stays `stub` until `scripts/replay_pit_universe_era.py --provider sharadar` is reviewed (see [[pit-research-platform]]).

## Config

| Variable | Default | Notes |
| --- | --- | --- |
| `SHARADAR_API_KEY` | empty | Direct API key (`https://api.sharadar.com/v1.0`) |
| `SHARADAR_ENABLED` | on when a key is set | Master switch |
| `SHARADAR_CACHE_HOURS` | `12` | Successful snapshots |
| `PIT_UNIVERSE_PROVIDER` | `stub` | Do not auto-promote to `sharadar` |

`GET /api/research/sharadar/{ticker}?refresh=1` bypasses cache. `GET /api/research/sharadar-compare/{a}/{b}` compares two snapshots. Fail-closed on missing key or HTTP 401/403.

## Related Pages

- [[pit-research-platform]] — PIT universe adapter and coverage gates
- [[companyfacts-pricing]] — SEC XBRL DCF (separate warehouse; not mixed with Sharadar EV)
- [[frontend-route-contract]] — `sharadar` / `pit` / `sharadar-compare` aliases
- [[local-dashboard-endpoints]] — `/api/research/sharadar/{ticker}`
- [[forensic-accounting]] — Scanner path stays yfinance; research overlay may use ART
- [[signal-scanner]] — Explicitly untouched

---

*Last compiled: 2026-09-21*
