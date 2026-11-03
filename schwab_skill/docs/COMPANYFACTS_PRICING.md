# Companyfacts Pricing (Research)

SEC XBRL companyfacts is the statement source for Research pricing models.
One document per CIK is cached on disk; slider recomputes never hit EDGAR,
Yahoo, or Finnhub. This layer does not change scan ranking, sizing, or
advisory.

## Why this exists

The report DCF used to live-fetch Yahoo `info` + three statements on every
click (and comps added up to six peer `.info` calls). Rate limits made a
real workbench impossible. Companyfacts inverts that: ingest once, price many
times.

## Pipeline

```
EDGAR company_tickers.json  -> CIK
data.sec.gov companyfacts   -> cache  (.companyfacts/facts/CIK*.json)
extract annual FCF/debt/shares -> frozen ValuationInputs
5-year FCF + perpetuity DCF    -> Research panel / report DCF / event-prob
```

Recompute (`POST /api/research/pricing/{ticker}/recompute`) is CPU-only. It
refuses to run without a warm cache rather than falling back to Yahoo.

## API

| Route | Auth | Purpose |
| --- | --- | --- |
| `GET /api/research/pricing/{ticker}` | local API key | Cache-first payload; ingests once on miss |
| `POST /api/research/pricing/{ticker}/ingest` | local API key | Force-refresh the XBRL document |
| `POST /api/research/pricing/{ticker}/recompute` | local API key | Overlay growth / WACC / terminal locally |

SaaS parity: `webapp/routes/tenant_pricing.py` (Supabase JWT). Cache is
global — companyfacts are issuer facts, not tenant-scoped.

Dashboard: Research -> Quick check -> Pricing model (`#pricingModelSection`).
Deep links: `?section=pricing` / `pricing-model` / `companyfacts`.

## Config

| Variable | Default | Notes |
| --- | --- | --- |
| `COMPANYFACTS_ENABLED` | `true` | Master switch |
| `COMPANYFACTS_CACHE_HOURS` | `72` | Successful documents stay warm |
| `COMPANYFACTS_TIMEOUT_SEC` | `20` | Per EDGAR request |
| `COMPANYFACTS_YAHOO_FALLBACK` | `true` | Legacy `_build_dcf_yfinance` when cache/ingest fails. Forced off when `SCHWAB_ONLY_DATA=true`. |

Requires a real `EDGAR_USER_AGENT`. Failures cache for 15 minutes so a 429
cannot stampede EDGAR.

## Batch ingest

```bash
python scripts/ingest_companyfacts.py --tickers AAPL,MSFT
python scripts/ingest_companyfacts.py --watchlist --limit 40 --delay 0.25
```

## Validation

From `schwab_skill/`:

```bash
python -m ruff check core/companyfacts webapp/routes/pricing.py webapp/routes/tenant_pricing.py
python -m pytest -q tests/test_companyfacts.py tests/test_static_router.py tests/test_research_tabs_contract.py
```

Browser: `https://127.0.0.1:8182/` -> Research -> Quick check -> Pricing model.
