# Frontend Design System

This guideline standardizes style tokens and component primitives for
`webapp/static/`.

## Token Source Of Truth

Single source: `webapp/static/styles.css` `:root` block.

Core tokens:

- Color: `--bg`, `--bg-elevated`, `--panel`, `--panel-border`, `--text`, `--muted`
- Accent: `--accent`, `--accent-2`, `--good`, `--bad`, `--warn`
- Rings: `--ring-good`, `--ring-warn`, `--ring-bad`, `--ring-neutral`
- Typography: `--font-sans`, `--font-mono`
- Motion/layout: `--ease-out-expo`, `--ease-spring`, `--sticky-offset`, `--sticky-max-height`

Rule: new UI styles must consume these tokens instead of introducing new
hard-coded colors/fonts.

## Component Primitives

- Buttons: `.btn`, `.btn.primary`, `.btn.secondary`, `.btn.small`
- Cards/surfaces: `.card`, `.operations-surface`, `.diagnostics-surface`
- Status chips: `.pill`, `.chip`, severity classes from `modules/logger.js`
- Tables/panels: `.table-wrap`, `.panel-disclosure`, `.tool-summary-card`

## Module Decomposition Policy

- Keep `app.js` as orchestrator only (event wiring + cross-panel coordination).
- Move render logic into focused modules under:
  - `static/modules/` for shared utilities/view helpers
  - `static/panels/` for feature-specific rendering and API flow

## Current Split Progress

- Added `static/modules/validationView.js` and moved validation-step rendering
  out of `app.js`.
- Existing panel modules (`panels/*.js`) remain the preferred target for new UI
  functionality.

## Next Planned Splits

1. Move scan diagnostics rendering (`buildScanMeta`, diagnostics blocker/funnel)
   into `panels/scanDiagnostics.js`.
2. Move pending board rendering into `panels/pendingBoard.js`.
3. Move health ribbon rendering into `panels/healthRibbon.js`.
