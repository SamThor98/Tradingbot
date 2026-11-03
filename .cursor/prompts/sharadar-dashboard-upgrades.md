# Sharadar Bundle — implement dashboard upgrades

Paste this into **Agent mode** to ship the P1 research overlays for the paid Sharadar Bundle. Companion wiki: `wiki/sharadar-bundle.md`, `wiki/pit-research-platform.md`, `wiki/frontend-route-contract.md`. Companion canvas: [Sharadar dashboard upgrades](C:\Users\Sam Thoresen\.cursor\projects\d-TradingBot\canvases\sharadar-dashboard-upgrades.canvas.tsx).

---

## 1. Goal (one sentence)

Put already-fetched Sharadar Bundle numbers on the surfaces operators actually use (dossier, decision card, pricing panel, Quick check) as **labeled PIT research context** — without changing live scan ranking, sizing, or the Wikipedia watchlist.

## 2. Priority and north star

| Item | Value |
|------|--------|
| Priority | **P1** dashboard UX. Do **not** treat this as P0 signal work. |
| North star | Bare-signal PF still gates promotion (`signal_edge_ledger.json`, plugin LIVE blocked). This wave does not move PF. |
| Dashboard | `python scripts/start_local_dashboard.py` → **https://127.0.0.1:8182/** (never port 8000) |
| Live universe | `PIT_UNIVERSE_PROVIDER` stays **`stub`**. Do not set `sharadar`. |
| Ranking | Do not write Bundle fields into `signal_score`, `core/scoring_rank_v2.py`, or Stage A/B filters. |
| Missing values | Stay empty / `None`. **Never invent zeros.** |

## 3. Already done (do not redo)

Inspect first. Extend these; do not replace the snapshot client:

- `schwab_skill/sharadar_data.py` — `SharadarClient`, `get_sharadar_research_snapshot`, `sharadar_preflight_item`, cache `.sharadar_cache.json`
- `GET /api/research/sharadar/{ticker}` — `webapp/routes/sharadar.py` + SaaS `webapp/routes/tenant_sharadar.py`
- Research panel `#sharadarBundleSection` — `webapp/static/panels/sharadarFundamentals.js`
- Dossier already embeds `sections.sharadar_bundle` + `source_metadata` id `sharadar` (`webapp/routes/research.py`, `webapp/tenant_dashboard.py`)
- Config: `get_sharadar_api_key` / `get_sharadar_enabled` (on when a key is set) / cache hours / timeout
- Deep links: `sharadar`, `sharadar-bundle`, `pit` → `sharadarBundleSection`
- Tests: `tests/test_sharadar_dashboard.py`, route-contract + research-tabs coverage
- Wiki: `wiki/sharadar-bundle.md`

Snapshot tables already fetched per ticker: `tickers`, `daily`, ART `fundamentals`, `insiders`, `holdings_ticker`, `actions`, `events`, `sp500`.

Fail-closed without `SHARADAR_API_KEY` (`sharadar_disabled` / `missing_sharadar_api_key`). Direct API is `https://api.sharadar.com/v1.0` with `x-api-key` — **not** Nasdaq Data Link / Quandl.

## 4. Deliverable — this wave only (P1 display)

Implement **all of A–H**. Do not start the replay-gated or “do not ship” items in §6.

Reuse `get_sharadar_research_snapshot(ticker)` everywhere. Do not add a second HTTP client.

### A. Dossier first source (required)

Today `merge_fundamental_metrics` in `webapp/dossier_sources.py` prefers Finnhub, then `report.health` / `report.comps` (Yahoo-ish) for ROE, P/E, D/E, current ratio, etc.

Change: when Sharadar `status` is ok/degraded **and** the field is present and finite, **Sharadar ART/daily wins**. If Sharadar is disabled, missing, or the cell is null, keep the existing cascade. Label `source_metadata` / per-metric sources as `sharadar` vs `finnhub` vs `report.*`.

Mirror local `research.py` and SaaS `tenant_dashboard.py`. Extend `tests/test_research_dossier_exports.py`.

### B. Decision-card valuation chips (required)

On Today + Research scan brief / decision card (`webapp/decision_card_service.py` + `webapp/static` brief renderers), show display-only chips when the snapshot is available:

- Market cap (`daily.marketcap`)
- EV / EBITDA (`daily.evebitda`)
- P/E (`daily.pe`)
- P/S (`daily.ps`)

Provenance one-liner: `Sharadar daily · as-of {date} · data_quality=…`. Hide a chip if the value is null. Do **not** fetch inside Stage A. Cache-first; one snapshot per ticker per check/card open is enough.

### C. 13F + Form 4 on the card (required)

From the same payload:

- Top 5 `holders` (name, units or %, value)
- Summed institutional % only when Sharadar supplied `percent` (already in `pit_fields.institutional_ownership`)
- Insider cluster: last ~8 Form 4 rows (filing date, owner, code, shares). Optional: net buy vs sell count. Do not invent transactions.

Keep the full tables in `#sharadarBundleSection`. The card gets a compact strip, not a second spreadsheet.

### D. Pricing-model cross-check (required)

Research → Pricing model (`#pricingModelSection`, `panels/pricingModel.js`, `webapp/routes/pricing.py`).

Companyfacts DCF stays the priced model (SEC XBRL). **Beside it**, not blended: Sharadar EV, EV/EBITDA, ART FCF, shares. Two labeled columns: `SEC companyfacts` vs `Sharadar Bundle`. If Sharadar is off, show the existing fail-closed notice; do not fall back to Yahoo for this column.

Do **not** require fixing unrelated `ImportError` for `get_companyfacts_cache_hours` unless it blocks this panel in the same session — if you touch `config.py`, add only the getters those modules already import (see §7 adjacent).

### E. ART history sparkline (required)

In `sharadarFundamentals.js`, plot revenue / FCF / ROE vs **`datekey`** (filing date), not `calendardate`. Keep a small table under the sparkline. Empty history → muted empty copy, not a zero line.

Match existing editorial tokens (`readability.css` / `styles.css`). No new npm charts if a tiny inline SVG or existing chart helper will do.

### F. Corporate-action strip (required)

From `actions` + `events`: splits, dividends, spinoffs that can invalidate a multiple. Hide the entire strip when both lists are empty. Never fabricate events.

### G. Forensic from ART on research surfaces only (required, shadow)

Sloan / Beneish / Altman on the **research** dossier + decision-card forensic notes should prefer ART statement fields (`netinc`, `ncfo`, `assets`, `equity`, `debt`, …) when coverage is enough.

**Do not** swap the live scanner path in `forensic_accounting.py` to Sharadar in this wave. Do not change `FORENSIC_FILTER_MODE`. Do not make forensic a live hard gate.

Preferred shape: helpers that map an ART row → the same ratio/score dict the card already renders, called from the research snapshot/dossier merge, with `data_lineage` like `sharadar_art`. If ART cannot compute a score, leave the existing yfinance forensic result (and its lineage) in place.

### H. Ticker vs ticker Bundle compare (required)

Sibling of SEC Filing Compare on Research → Quick check → Deep. Two tickers, Sharadar daily + latest ART (ROE, revenue, FCF, EV/EBITDA, P/E). Research only.

- New DOM id, e.g. `sharadarCompareSection`
- Additive `?section=` aliases only (`sharadar-compare`, maybe `bundle-compare`). Never repoint existing aliases.
- Register in `SCREEN_SECTIONS`, `researchTabs.js`, `router.js`, e2e `tests/e2e/test_route_contract.py`, `tests/test_static_router.py`, `tests/test_research_tabs_contract.py`
- Update `wiki/frontend-route-contract.md` + `wiki/index.md` + `wiki/sharadar-bundle.md`

SaaS: JWT mirror of any new GET, same `ApiResponse(ok, data, error)` contract.

## 5. Implementation constraints

- Smallest coherent diff; match `project-conventions.mdc` (`from __future__ import annotations`, `LOG.info("… %s", var)` never f-strings in logs)
- Config tunables via `config.py` / `.env`; process env overrides `.env`
- Frozen frontend route contract: aliases additive only; keep `#sharadarBundleSection` id
- Local + SaaS parity for any new research route
- `ApiResponse` from `schemas.py`; surface `data_quality` verbatim
- Do not start or verify UI on port 8000
- Do not commit unless asked. Do not commit `.env` or `.sharadar_cache.json`
- Rate: existing client paces ~0.35s/table — do not fire a snapshot per scan row in this wave

## 6. Out of scope / hard no

**Replay-gated (do not build now):**

- Backtest lab UI for `scripts/replay_pit_universe_era.py`
- Post-scan shortlist enrichment with daily multiples (would hit Stage B latency)
- Enabling `market_cap` / `roe` in the PIT feature registry or `run_factor_analytics.py`

**Do not ship:**

- `PIT_UNIVERSE_PROVIDER=sharadar` on the live watchlist (S&P 500 ≠ SP1500; current ≠ 2016 membership)
- Injecting ROE / market cap into `rank_v2` or Stage 2 filters
- Estimate revisions (not a Bundle table — Finnhub Estimate-1)
- Using Sharadar prices for orders, quotes, or Schwab-only promotion backtests
- Mixing Sharadar EV into the companyfacts DCF formula
- Treating `sp500.is_member` as historical universe membership
- Filling missing fundamentals with `0`

**Also do not:** live orders; flip `REGIME_V2_MODE` / `EXIT_MANAGER_MODE` / `CORRELATION_GUARD_MODE` / `PROB_RANK_MODE` to LIVE; `FORENSIC_FILTER_MODE=hard`.

## 7. Adjacent config holes (optional, only if you already have `config.py` open)

Research Quick check currently 500s Event risk / Pricing if these getters are missing (logged as `ImportError`). Add them with the same pattern as `get_sharadar_*` **only if** needed to verify D:

- `get_event_prob_cache_ttl_sec` (imported by `core/event_probability/settings.py`)
- `get_companyfacts_cache_hours` (imported by `core/companyfacts/settings.py`)

Do not drive this wave from those panels.

## 8. Validation

From `schwab_skill/` with `.venv\Scripts\python.exe` (system Python may lack FastAPI):

```text
python -m ruff check sharadar_data.py forensic_accounting.py webapp/dossier_sources.py webapp/decision_card_service.py webapp/routes/research.py webapp/routes/sharadar.py webapp/routes/pricing.py webapp/tenant_dashboard.py tests/test_sharadar_dashboard.py tests/test_research_dossier_exports.py
python -m pytest -q tests/test_sharadar_dashboard.py tests/test_research_dossier_exports.py tests/test_static_router.py tests/test_research_tabs_contract.py tests/e2e/test_route_contract.py
```

If you touch scanner-adjacent forensic helpers, also run the existing forensic unit tests — they must still reflect the **yfinance/scanner** path.

Behavior: `python scripts/validate_all.py --profile local --strict --skip-backtest` if routes or dossier shape change.

UI on **https://127.0.0.1:8182/** (`--no-pull --no-sync-env` if the working tree must not be overwritten):

1. Fail-closed: no key → Sharadar panel + new chips/strips hidden or “set SHARADAR_API_KEY”, not zeros
2. With key (operator `.env`): Check **AAPL** Brief → dossier metrics cite `sharadar` when present; decision card shows chips; pricing shows two columns; compare two names; actions strip hidden on a name with no events
3. Confirm a scan row score is **unchanged** vs pre-change (same fixture / no rank_v2 edit)

## 9. Success criteria

- [ ] Dossier metrics prefer Sharadar ART/daily when finite; otherwise previous cascade
- [ ] Decision card shows valuation chips + compact 13F/insider strip; nulls omitted
- [ ] Pricing panel shows Sharadar EV/FCF **beside** companyfacts DCF, not mixed
- [ ] ART sparkline uses `datekey`; action strip hidden when empty
- [ ] Research forensic notes can show `sharadar_art` lineage; live `FORENSIC_FILTER_MODE` unchanged
- [ ] Bundle compare exists with additive `?section=` aliases; e2e route contract green
- [ ] SaaS JWT routes mirror new GETs
- [ ] `PIT_UNIVERSE_PROVIDER` still defaults to stub; `scoring_rank_v2.py` untouched
- [ ] Wiki pages updated; `wiki/index.md` linked
- [ ] Summary: changed files, command outcomes, residual risk (key missing, rate limits), next validation step

## 10. Session start (agent)

1. Priority **P1**. Files: `dossier_sources.py`, `decision_card_service.py`, `sharadarFundamentals.js`, `pricingModel.js`, research + tenant dossier routes, compare panel + router aliases, tests, wiki.
2. Read `sharadar_data.py`, `wiki/sharadar-bundle.md`, `wiki/frontend-route-contract.md` before adding abstractions.
3. State that live ranking is frozen. Implement A–H. Do not start §6.

## 11. Copy-paste kickoff

```text
Implement Sharadar Bundle dashboard upgrades per .cursor/prompts/sharadar-dashboard-upgrades.md

Priority P1. Dashboard: https://127.0.0.1:8182/ — never port 8000.
Reuse get_sharadar_research_snapshot. Do not add a second client.

Ship:
A) Dossier: Sharadar ART/daily wins when finite; else Finnhub/report cascade. Label sources.
B) Decision-card chips: market cap, EV/EBITDA, P/E, P/S (hide nulls). Display only.
C) Compact 13F + Form 4 on the card from the same snapshot.
D) Pricing model: Sharadar EV / EV/EBITDA / FCF beside companyfacts DCF — do not blend.
E) ART sparkline on datekey (not calendardate).
F) Corporate-action strip; hide when empty.
G) Research forensic from ART when possible; do NOT swap scanner forensic_accounting.py or FORENSIC_FILTER_MODE.
H) Ticker vs ticker Bundle compare; additive ?section= aliases only.

Never invent zeros. Do not set PIT_UNIVERSE_PROVIDER=sharadar. Do not touch scoring_rank_v2.py.
Do not enrich the live scan shortlist. Do not use Sharadar prices for orders or promotion backtests.
Mirror SaaS JWT routes. Tests + ruff + 8182 Check AAPL. Do not commit unless asked.
```

---

*Created 2026-09-21 for TradingBot Sharadar Bundle P1 overlays.*
