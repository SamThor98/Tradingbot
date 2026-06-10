---
source: schwab_skill/webapp/static/modules/router.js, schwab_skill/webapp/static/app.js
created: 2026-06-10
updated: 2026-06-10
tags: [frontend, routing, deep-links, contract]
---

# Frontend Route Contract

> The frozen navigation contract for the dashboard (Action 2 of the redesign
> roadmap). Any section move or rename must keep this contract intact: inbound
> links from emails, docs, and bookmarks must never break.

## Contract

The dashboard is a single HTML page with five screen tabs. Three URL surfaces
exist, each with one job:

| Surface | Job | Owner |
|---------|-----|-------|
| `?screen=<mode>` | Selects the active screen tab. Written via `history.replaceState` by `writeScreenModeToUrl()` in `app.js`. | `applyScreenMode()` / `getScreenModeFromUrl()` |
| `#<elementId>` | Scroll target. Opens ancestor `<details>` and smooth-scrolls. | `handleRouteHash()` in `modules/router.js` |
| `?section=<alias>` | Human-friendly external deep link (emails/docs). Rewritten once to the canonical `#id` via `replaceState`. | `applyQuerySectionDeepLink()` in `modules/router.js` |

Rules:

1. `?section=` aliases are the **stable external surface**. Aliases in
   `SECTION_ALIASES` may be added, never removed or repointed to a different
   feature.
2. Section element `id`s referenced by aliases are stable. If a section moves
   screens, keep the `id`, update `SCREEN_SECTIONS`, and the alias keeps working.
3. If a section is placed inside a collapsed `<details>`, deep links still work:
   the router force-opens ancestor `<details>` before scrolling.
4. Screen inference: a bare `#id` (no `?screen=`) infers its screen via
   `SECTION_TO_SCREEN` and activates that tab.
5. Keyboard shortcuts Ctrl/Cmd+1..5 map to the screen order below and must not
   be reassigned.

## Screen map (final)

| Order / shortcut | Screen mode | Purpose | Default landing |
|------------------|-------------|---------|-----------------|
| 1 | `operations` | Scan → evaluate → queue → approve workflow | Yes (default) |
| 2 | `research` | Quick check, backtest, SEC compare, dossier, portfolio, performance |  |
| 3 | `kronos` | Kronos forecast workspace |  |
| 4 | `diagnostics` | Health ribbon, decision dashboard, status, calibration, scoreboard, review loop |  |
| 5 | `settings` | Schwab connect, presets, live trading, 2FA, billing |  |

## Alias table (frozen, from `SECTION_ALIASES`)

| Alias | Canonical id | Screen |
|-------|--------------|--------|
| `scan`, `candidates` | `scanSection` | operations |
| `pending`, `queue`, `approvals`, `trades` | `pendingSection` | operations |
| `workflow` | `workflowPrimary` | operations |
| `operations` | `operationsWorkspaceIntro` | operations |
| `sectors` | `sectorsSection` | operations |
| `movers` | `moversSection` | operations |
| `research` | `researchWorkspaceIntro` | research |
| `backtest`, `backtests` | `backtestSection` | research |
| `kronos` | `kronosWorkspaceIntro` | kronos |
| `forecast` | `kronosForecastSection` | kronos |
| `diagnostics` | `diagnosticsWorkspaceIntro` | diagnostics |
| `health` | `healthRibbon` | diagnostics |
| `connect`, `onboarding`, `setup` | `onboardingSection` | settings |
| `settings` | `settingsWorkspaceIntro` | settings |

(`sectors` / `movers` aliases are added by the redesign so the demoted cards
stay deep-linkable; the rest existed before.)

## Section-to-screen source of truth

- `SCREEN_SECTIONS` in `app.js` (~line 276) maps each screen to its section ids.
- `SECTION_TO_SCREEN` is derived from it; never hand-edit a duplicate mapping.
- CSS visibility (`body.ui-screen-*` rules in `styles.css`) must agree with
  `SCREEN_SECTIONS`; a section listed for a screen must be visible on that
  screen.

## Related Pages

- [[section-migration-map]] — which sections move where under this contract
- [[ux-kpi-baseline]] — KPI events keyed to screen views
- [[static-module-layout]] — frontend module map including `router.js`
- [[webapp-dashboard]] — dashboard overview

---

*Last compiled: 2026-06-10*
