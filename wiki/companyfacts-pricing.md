---
source: schwab_skill/core/companyfacts/, schwab_skill/docs/COMPANYFACTS_PRICING.md
created: 2026-09-02
updated: 2026-09-21
tags: [research, valuation, dcf, edgar, companyfacts]
---

# Companyfacts pricing

> Research pricing models built from cached SEC XBRL companyfacts. Ingest once
> per CIK; slider recomputes never hit EDGAR or Yahoo.

## What it does

SEC `companyfacts` is the statement source for the Research **Pricing model**
panel. One XBRL JSON document per CIK is cached under `.companyfacts/`. The
same 5-year FCF + perpetuity DCF used by the report stack and
[[event-probability-layer]] prices from those frozen inputs.

```
CIK lookup → companyfacts JSON → FCF / debt / shares → ValuationInputs → DCF
```

An event-adjusted value and a slider-tweaked value are therefore comparable to
the DCF the operator already sees. This layer never touches scan ranking,
sizing, or advisory (see [[signal-scanner]]).

## Cache and rate limits

Successful documents honor `COMPANYFACTS_CACHE_HOURS` (default 72). Failed
fetches (including HTTP 429) cache for 15 minutes. Placeholder
`EDGAR_USER_AGENT` values are refused — the same rule as EDGAR filing
fetches. Yahoo remains a **fallback for the report DCF only** when
ingest fails and `COMPANYFACTS_YAHOO_FALLBACK=true`; it is forced off under
`SCHWAB_ONLY_DATA`.

Batch: `python scripts/ingest_companyfacts.py --watchlist --limit 40`.

## UI and API

Research → Quick check → Pricing model (`#pricingModelSection`). Two labeled
columns: **SEC companyfacts** (the priced DCF) beside **Sharadar Bundle**
(EV, EV/EBITDA, ART FCF, shares). Sharadar numbers are display-only and are
never mixed into the DCF formula (see [[sharadar-bundle]]). Deep-link
aliases: `pricing`, `pricing-model`, `companyfacts` (see
[[frontend-route-contract]]).

| Route | Purpose |
| --- | --- |
| `GET /api/research/pricing/{ticker}` | Cache-first; one ingest on miss |
| `POST .../ingest` | Force-refresh the XBRL document |
| `POST .../recompute` | Local growth / WACC / terminal overlay |

## Related Pages

- [[event-probability-layer]] — Scenario DCF on the same `ValuationInputs`
- [[frontend-route-contract]] — `pricing` deep-link alias
- [[local-dashboard-endpoints]] — `/api/research/pricing/*`
- [[pit-research-platform]] — Separate PIT Sharadar path; dashboard snapshot is [[sharadar-bundle]]
- [[sharadar-bundle]] — Labeled Sharadar EV/FCF column beside this DCF
- [[signal-scanner]] — Explicitly untouched

---

*Last compiled: 2026-09-21*
