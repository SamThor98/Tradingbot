---
source: schwab_skill/webapp/static/modules/router.js, schwab_skill/webapp/static/app.js
created: 2026-06-10
updated: 2026-07-27
tags: [frontend, routing, deep-links, contract]
---

# Frontend Route Contract

> The frozen navigation contract for the dashboard. Any section move or rename
> must keep this contract intact: inbound links from emails, docs, and bookmarks
> must never break.

## Contract

The dashboard is a single HTML page with **four top-level tabs** (Today /
Research / Intel / System). Settings merged into System on 2026-07-27; all
old `?screen=settings` links normalize to the System screen via
`SCREEN_ALIASES`. Three URL surfaces exist, each with one job:

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
5. Keyboard shortcuts Ctrl/Cmd+1..4 map to the screen order below and must not
   be reassigned.

Legacy screen modes (`cockpit`, `system`, `health`, `settings`) normalize to a
top-level tab via `SCREEN_ALIASES` in `app.js` (e.g. `cockpit` → `research`,
`settings` → `diagnostics`).

## Screen map (updated 2026-07-27)

| Order / shortcut | Screen mode | Tab label | Purpose |
|------------------|-------------|-----------|---------|
| 1 | `operations` | Today | Summary landing + scan → review → approve kanban |
| 2 | `research` | Research | Sub-tabs: Portfolio → Quick check → Backtest (default Portfolio; Diligence merged into Quick check Brief/Deep) |
| 3 | `intel` | Intel | Position Intelligence: conviction scorecard, GARCH volatility signals, long-call and covered-call tables (`/api/position-intel`) |
| 4 | `diagnostics` | System | Summary + health ribbon; collapsed status/decision/quality panels; settings sections re-homed below (overview, connect, presets, account security) |

Default landing: `operations` (Today).

## Alias table (frozen, from `SECTION_ALIASES`)

| Alias | Canonical id | Screen |
|-------|--------------|--------|
| `scan`, `candidates` | `scanSection` | operations |
| `peadcanary`, `canary`, `pead-canary` | `peadCanarySection` | operations |
| `pending`, `queue`, `approvals`, `trades` | `pendingSection` | operations |
| `workflow` | `workflowPrimary` | operations |
| `operations` | `operationsWorkspaceIntro` | operations |
| `sectors` | `sectorsSection` | research |
| `movers` | `moversSection` | research |
| `research` | `researchWorkspaceIntro` | research |
| `backtest`, `backtests` | `backtestSection` | research |
| `cockpit` | `cockpitWorkspaceIntro` | research (via alias) |
| `sec`, `seccompare` | `secCompareSection` | research (opens Quick check → Deep) |
| `portfolio` | `portfolioSection` | research |
| `risk`, `portfoliorisk` | `portfolioPanelRisk` | research (opens Portfolio → Risk sub-tab) |
| `book` | `portfolioPanelBook` | research (opens Portfolio → Book sub-tab) |
| `book-calendar`, `book-tax`, `book-journal` | matching deeplink anchors | research (Book → Calendar / Tax / Journal) |
| `calibration` | `calibrationSection` | diagnostics |
| `diagnostics`, `health` | `healthRibbon` | diagnostics |
| `intel`, `positionintel` | `positionIntelSection` | intel |
| `connect`, `onboarding`, `setup` | `onboardingSection` | diagnostics (settings merged) |
| `settings` | `settingsWorkspaceIntro` | diagnostics (settings merged) |

Deprecated intro sections (`*WorkspaceIntro`) remain in the DOM for alias
compatibility; they are hidden. Prefer canonical section ids in new links.

## Section-to-screen source of truth

- `SCREEN_SECTIONS` in `app.js` maps each screen to its section ids.
- `SECTION_TO_SCREEN` is derived from it; never hand-edit a duplicate mapping.
- CSS visibility (`body.ui-screen-*` rules in `styles.css`) must agree with
  `SCREEN_SECTIONS`; a section listed for a screen must be visible on that
  screen.
- Research sub-tab visibility is owned by `modules/researchTabs.js` (`research-tab-hidden`).

## Related Pages

- [[section-migration-map]] — locked section layout and rollout history
- [[ux-kpi-baseline]] — KPI events keyed to screen views
- [[static-module-layout]] — frontend module map including `router.js`
- [[webapp-dashboard]] — dashboard overview

---

*Last compiled: 2026-07-27*
