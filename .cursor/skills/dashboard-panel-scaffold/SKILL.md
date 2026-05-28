---
name: dashboard-panel-scaffold
description: >-
  Scaffold a cockpit dashboard panel end to end: a DTO-backed read endpoint
  (shared in core/ for local + SaaS parity), a frontend panel module with
  explicit freshness/error states, and provenance rendering. Use when adding
  or editing a cockpit lane/panel, an /api/cockpit/* route, core/cockpit_service
  builders, or any static/ panel that consumes ApiResponse data.
---

# Dashboard Panel Scaffold

## Canonical Contract

Cockpit panels follow one shape so the dashboard stays coherent as lanes grow.
Every panel: (1) consumes a normalized DTO (never raw vendor JSON), (2) renders
`source / as_of / confidence` from the DTO's `provenance`, and (3) shows an
explicit loading / empty / error / success state.

Pairs with: [`schwab-endpoint-catalog`] (endpoint + DTO source of truth),
[`decision-card-builder`] (symbol DTOs), [`front-end-design`] (IA + UX bar).

## Architecture rules (do not violate)

1. **Builders live in `core/`** (`core/cockpit_service.py`) and accept
   already-fetched inputs — no Schwab I/O inside the builder. Routes fetch
   (mirroring `/api/portfolio`, `/api/sectors`) then call the builder. This
   keeps local + SaaS at parity and the builder unit-testable offline.
2. **One normalized object per domain.** Reuse the contracts in
   `core/contracts/`; extend a DTO with new optional fields rather than
   inventing a panel-specific dict.
3. **Provenance is mandatory.** Every payload carries a `provenance` block; the
   panel must render it (badge with `source · confidence`, stale marker).
4. **Additive + flag-gated.** New panels ship without disturbing existing
   screens. Behaviors roll out OFF → SHADOW → LIVE via a `*_MODE` config getter.

## Backend recipe

```python
# core/cockpit_service.py
def build_<lane>(raw_input: dict | None, *, skill_dir: Path | None = None) -> dict:
    dto = <Provider>.normalize(raw_input or {})   # returns a pydantic contract
    return dto.model_dump(mode="json")            # plain dict for ApiResponse
```

```python
# webapp/main.py (local)   — mirror in main_saas.py / tenant_dashboard.py
@app.get("/api/cockpit/<lane>", response_model=ApiResponse)
def cockpit_<lane>(...) -> ApiResponse:
    try:
        from core import cockpit_service
        data = fetch_inputs(...)            # the only place that calls Schwab
        return _ok(cockpit_service.build_<lane>(data, skill_dir=SKILL_DIR))
    except Exception as e:
        return _err("cockpit_<lane>", e)
```

- Read-only lanes need no auth dependency. Mutating actions use
  `Depends(require_api_key_if_set)` / `require_trade_api_key` and must still go
  through the existing approval flow (`/api/pending-trades` → approve).

## Frontend recipe

Panel module under `static/` (page) or `static/panels/` (main dashboard):

```js
import { api } from "../modules/api.js";          // never call fetch() directly
import { safeText, formatDecimal } from "../modules/format.js";
import { setAsyncState, ASYNC_LOADING, ASYNC_EMPTY, ASYNC_ERROR, ASYNC_SUCCESS } from "../modules/asyncState.js";

export async function refresh<Lane>() {
  const body = document.getElementById("<lane>Body");
  setAsyncState(body, ASYNC_LOADING, { message: "Loading…" });
  const out = await api.get("/api/cockpit/<lane>");
  if (!out.ok) {
    setAsyncState(body, ASYNC_ERROR, { html: errorHtml(out), onRetry: () => void refresh<Lane>() });
    return;
  }
  const data = out.data || {};
  if (isEmpty(data)) { setAsyncState(body, ASYNC_EMPTY, { message: "No data." }); return; }
  body.setAttribute("data-async-state", ASYNC_SUCCESS);
  body.innerHTML = render(data);                  // include provenance badge
}
```

- `out` is always a resolved object (`{ ok, data, error, user_message, status }`)
  — no try/catch needed around `api.get`.
- Render the provenance badge from `data.provenance` (`source`, `confidence`,
  `is_stale`, `as_of`).

## Freshness & error states (required)

| State | When | Constant |
|-------|------|----------|
| loading | before first/again fetch | `ASYNC_LOADING` |
| empty | `ok` but no rows | `ASYNC_EMPTY` |
| error | `!ok` (show retry; handle 401 → signed-out) | `ASYNC_ERROR` |
| success | `ok` with content | `ASYNC_SUCCESS` |

## Checklist for a new panel

1. Add/extend the DTO in `core/contracts/` (optional fields only).
2. Add a `build_<lane>` in `core/cockpit_service.py` (pure; offline-testable).
3. Wire the `/api/cockpit/<lane>` route in `webapp/main.py` (and SaaS surfaces).
4. Add the frontend panel module with the four async states + provenance badge.
5. Gate any new behavior behind a `*_MODE` getter in `config.py` (default off/shadow).
6. Add a unit test in `tests/test_cockpit_*.py` for the builder.
7. Run `python -m pytest -q` and `python -m ruff check .`.

## Key Files

- `schwab_skill/core/cockpit_service.py` — lane builders + order-intent preview
- `schwab_skill/core/contracts/` — DTOs (incl. `Provenance`)
- `schwab_skill/core/providers/` — raw → DTO normalization
- `schwab_skill/core/pretrade_gates.py` — pre-trade quality gates (shadow)
- `schwab_skill/webapp/main.py` — `/api/cockpit/*` routes + `/cockpit` page
- `schwab_skill/webapp/static/cockpit.html` / `cockpit.js` — the four-lane shell
- `schwab_skill/webapp/static/modules/api.js`, `asyncState.js`, `format.js` — panel plumbing
