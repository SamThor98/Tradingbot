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
