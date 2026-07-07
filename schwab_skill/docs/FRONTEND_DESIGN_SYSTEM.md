# Frontend Design System

This guideline standardizes style tokens and component primitives for
`webapp/static/`.

## Token Source Of Truth

Single source: `webapp/static/readability.css` for canonical Old Logan readability
tokens, with `webapp/static/styles.css` `:root` providing base fallbacks.

Core tokens:

- Color: `--bg`, `--bg-elevated`, `--panel`, `--panel-border`, `--text`, `--muted`
- Accent: `--accent`, `--accent-2`, `--good`, `--bad`, `--warn`
- Rings: `--ring-good`, `--ring-warn`, `--ring-bad`, `--ring-neutral`
- Typography: `--font-sans`, `--font-mono`
- Motion/layout: `--ease-out-expo`, `--ease-spring`, `--sticky-offset`, `--sticky-max-height`

Rule: new UI styles must consume these tokens instead of introducing new
hard-coded colors/fonts.

## Theme Layering Contract

- Load order must remain:
  1) `styles.css` (base primitives + module defaults)
  2) `overhaul.css` (legacy compatibility stack)
  3) `readability.css` (canonical active readability contract)
- Any new readability work should be added to `readability.css` only.
- Do not add additional post-readability override files.
- 2026-06-10 dead-CSS sweep: ~600 lines of unreferenced selectors pruned from
  `styles.css` and `overhaul.css` (rules whose classes appear in no HTML/JS).
  Remaining audit "misses" are dynamically-built class names
  (`toast-*`, `severity-*`, `chat-bubble-*`, `task-card--risk-*`,
  `dossier-quality-badge--*`) — keep these even though no literal match exists.
- 2026-06-10 Tailwind CDN removed: the only real consumers were the four SEC
  compare cards in `index.html` plus the `bg-*-900` health badges. Those
  utilities are now self-hosted in `styles.css` (see the "Utility shim" block),
  and a Tailwind-v3-preflight-equivalent reset sits at the top of `styles.css`
  because the whole page always rendered with preflight active. Do not add new
  Tailwind classes — use tokens/components instead.

## Screen Selector Map

- Operations: `#workflowPrimary`, `#scanSection`, `#scanDetailPanel`, `#pendingSection`
- Research: `#quickCheckSection`, `#backtestSection`, `#reportSectionCard`, `#secCompareSection`
- Diagnostics: `#healthRibbon`, `#decisionDashboardCard`, `#statusDetailsPanel`
- Settings: `#onboardingSection`, `#settingsSection`

## Component Primitives

- Buttons: `.btn`, `.btn.primary`, `.btn.secondary`, `.btn.small`
- Cards/surfaces: `.card`, `.operations-surface`, `.diagnostics-surface`
- Status chips: `.pill`, `.chip`, severity classes from `modules/logger.js`
- Tables/panels: `.table-wrap`, `.panel-disclosure`, `.tool-summary-card`

## Readability Guardrails (must pass)

- Body text floor: `>= 16px` (or `1rem` equivalent) on desktop.
- Supporting/meta text floor: `>= 13px` (`~0.82rem` equivalent).
- Table body text floor: `>= 14px` (`~0.9rem` equivalent).
- Heading contrast: maintain strong contrast against panel background.
- Muted text must remain legible; avoid stacking low contrast + tiny size.
- Focus visibility: keep a high-contrast `:focus-visible` outline for keyboard users.
- Critical actions (`Run Scan`, approve/reject controls, primary CTA) must never rely on muted styling.

## Async Surface Contract

- Every fetch-driven panel must carry `data-async-state` (loading/empty/error/
  success/stale/signed_out) and render explicit loading + error markup.
- Prefer `setAsyncState` / `renderAsync` from `modules/asyncState.js`; the
  hand-rolled `async-state--*` markup in older panels is acceptable as long as
  it matches the same classes and includes a retry affordance for idempotent
  GETs.
- 2026-06-10 adoption sweep: `profile.js`, `quickCheck.js`, `report.js`
  retrofitted. Remaining intentional exceptions: `backtest.js` /
  `onboarding.js` (wizard- or run-status-driven flows with their own
  messaging — both now include retry buttons on failed status GETs).
- 2026-07-07 audit sweep (Waves A–E): retry affordances added to
  `decisionDashboard.js`, `performance.js`, `sec.js` (headline card),
  `report.js` dossier, `tradeDrawer.js` (plus real loading states), and
  `onboarding.js`. `twoFa.js` now renders an explicit error state (security
  surface must fail visibly). `quickCheck.js` renders a degraded/partial
  state whenever the payload carries `data_quality` (e.g. `NO_PRICE_DATA`).
  Skeleton loading (`.ol-skeleton`) used by pending board + backtest run
  list; reuse it for new panels instead of text-only loading.

## JS Unit Tests

- In-process unit tests for pure modules (`format.js`, `humanize.js`,
  `signalScores.js`, `asyncState.js`, `router.js`, `glossary.js`, `api.js`
  dedup) live in `tests/js/*.test.mjs` and run on Node's built-in runner — no
  bundler or npm install (`webapp/static/package.json` only marks the tree as
  ESM).
- Run directly with `node --test "tests/js/*.test.mjs"` from `schwab_skill/`,
  or via pytest (`tests/test_js_unit.py` wraps the suite and skips without Node).
- New pure modules should ship with a `tests/js/<module>.test.mjs` file.

## Refresh Contract

- `refreshAll()` (Refresh button / `R`) is scoped: it always re-fetches the
  cheap global segments (status, account, pending), then only the visible
  screen's panels plus lazy panels the user has already loaded. Never-opened
  panels stay deferred to `setupLazySectionLoading`.
- Concurrent identical GETs are deduplicated inside `modules/api.js`
  (in-flight promise sharing, not a response cache).

## Module Decomposition Policy

- Keep `app.js` as orchestrator only (event wiring + cross-panel coordination).
- Move render logic into focused modules under:
  - `static/modules/` for shared utilities/view helpers
  - `static/panels/` for feature-specific rendering and API flow

## Current Split Progress

- Added `static/modules/validationView.js` and moved validation-step rendering
  out of `app.js`.
- 2026-06-10: completed the three planned splits —
  - `panels/scanDiagnostics.js`: `buildScanMeta`, `diagnosticsHeadline`,
    blocker/funnel builders, and `renderDiagnostics` (DI: `updateHeroInfographic`,
    `getDisplayMode`).
  - `panels/pendingBoard.js`: `refreshPendingBoard` plus the task-card render
    helpers (DI: `openApproveDialog`, `updateHeroInfographic`,
    `trackFunnelMilestoneOnce`, `FUNNEL_EVENTS`).
  - `panels/healthRibbon.js`: ribbon badges/tiles, the plain-language summary,
    and `prioritizeActionCenterFromHealth` (no DI; imports modules directly).
  - `modules/signalScores.js`: shared pure score accessors
    (`getCompositeScore`, `getReliabilityScore`, `getCalibratedPUp`, etc.)
    used by both the scan table and the pending board.
- Existing panel modules (`panels/*.js`) remain the preferred target for new UI
  functionality.
- 2026-07-07: completed the two planned splits —
  - `panels/scanTable.js`: `renderScanRows`, sortable-header machinery
    (`sortScanSignalsForRender`, `compareScanSignals`, `bindScanSortHandlers`),
    the funnel-filter banner, rank "why" cells, and the rank-explain mode
    control. Cross-panel callbacks are injected once at boot via
    `configureScanTable(deps)`.
  - `panels/approveDialog.js`: `openApproveDialog`, preflight checklist
    rendering, `syncApproveDialogGuardrails`, `approveTradeById`. Deps via
    `configureApproveDialog(deps)`; button wiring stays in
    `screens/operations.js`.
  - `modules/scanSignals.js`: shared `normalizeScanSignal` /
    `signalFromScanResultRow` payload-shape helpers.

## Next Planned Splits

1. Move the scan detail panel (chart + decision brief) into
   `panels/scanDetail.js`.
2. Move the SSE / scan-status polling loop into `modules/scanLifecycle.js`.
