---
source: schwab_skill/webapp/static/modules/router.js, schwab_skill/webapp/static/app.js
created: 2026-06-10
updated: 2026-10-15
tags: [frontend, routing, deep-links, contract]
---

# Frontend Route Contract

> The frozen navigation contract for the dashboard. Any section move or rename
> must keep this contract intact: inbound links from emails, docs, and bookmarks
> must never break.

## Contract

The dashboard is a single HTML page with **five top-level tabs** (Today /
Research / Intel / System / Backtest). Settings merged into System on 2026-07-27; all
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
5. Keyboard shortcuts Ctrl/Cmd+1..4 map to Today / Research / Intel / System
   and must not be reassigned. Ctrl/Cmd+5 opens Backtest.

Legacy screen modes (`cockpit`, `system`, `health`, `settings`) normalize to a
top-level tab via `SCREEN_ALIASES` in `app.js` (e.g. `cockpit` → `research`,
`settings` → `diagnostics`).

## Screen map (updated 2026-09-11)

| Order / shortcut | Screen mode | Tab label | Purpose |
|------------------|-------------|-----------|---------|
| 1 | `operations` | Today | Summary landing + scan → review → approve kanban |
| 2 | `research` | Research | Sub-tabs: Portfolio → Quick check. Default Portfolio. Brief = one ticker; Deep = dossier + SEC. Sectors/movers/recovery stay reachable via `?section=` aliases but are parked off the default workspace. |
| 3 | `intel` | Intel | Position Intelligence: conviction scorecard, GARCH volatility signals, long-call and covered-call tables (`/api/position-intel`) |
| 4 | `diagnostics` | System | Summary + health ribbon; collapsed status/decision/quality panels; settings sections re-homed below (overview, connect, presets, account security) |
| 5 | `backtest` | Backtest | Leaderboard + compact worker + queue + briefing (`#backtestLeaderboard`, `#backtestLiveMonitor`, `#backtestWorkspace`). Edit settings reuses Scan studio indexes/strategies/variables. Optional `?run=<id>` loads a job. |

Default landing: `operations` (Today).

## Alias table (frozen, from `SECTION_ALIASES`)

| Alias | Canonical id | Screen |
|-------|--------------|--------|
| `scan`, `candidates` | `scanSection` | operations |
| `scanstudio`, `scan-studio` | `scanStudioPanel` | operations |
| `desk`, `operator-desk` | `operatorAgentsSection` | operations |
| `grok`, `grok-review` | `grokReviewSection` | operations |
| `peadcanary`, `canary`, `pead-canary` | `peadCanarySection` | operations |
| `lessons`, `settled-lessons` | `settledLessonsSection` | operations |
| `pending`, `queue`, `approvals`, `trades` | `pendingSection` | operations |
| `workflow` | `workflowPrimary` | operations |
| `operations` | `operationsWorkspaceIntro` | operations |
| `sectors` | `sectorsSection` | research |
| `movers` | `moversSection` | research |
| `research` | `researchWorkspaceIntro` | research |
| `backtest`, `backtests` | `backtestSection` | backtest |
| `lab` | `backtestSummaryLanding` | backtest |
| `leaderboard` | `backtestLeaderboard` | backtest |
| `btmonitor`, `backtest-monitor` | `backtestLiveMonitor` | backtest |
| `cockpit` | `cockpitWorkspaceIntro` | research (via alias) |
| `sec`, `seccompare` | `secCompareSection` | research (opens Quick check → Deep) |
| `portfolio` | `portfolioSection` | research |
| `risk`, `portfoliorisk` | `portfolioPanelRisk` | research (opens Portfolio → Risk sub-tab) |
| `book` | `portfolioPanelBook` | research (opens Portfolio → Book sub-tab) |
| `book-calendar`, `book-tax`, `book-journal` | matching deeplink anchors | research (Book → Calendar / Tax / Journal) |
| `event-risk`, `eventrisk`, `event-probability` | `eventProbabilitySection` | research (opens Quick check → Event risk, see [[event-probability-layer]]) |
| `liquid`, `liquid-positioning`, `positioning` | `liquidPositioningSection` | research (opens Quick check → Liquid positioning, shadow perp funding/OI) |
| `pricing`, `pricing-model`, `companyfacts` | `pricingModelSection` | research (opens Quick check → Pricing model, see [[companyfacts-pricing]]) |
| `sharadar`, `sharadar-bundle`, `pit` | `sharadarBundleSection` | research (opens Quick check → Sharadar Bundle, see [[sharadar-bundle]]) |
| `sharadar-compare`, `bundle-compare` | `sharadarCompareSection` | research (opens Quick check → Deep → Bundle compare, see [[sharadar-bundle]]) |
| `calibration` | `calibrationSection` | diagnostics |
| `bookwidth`, `book-width` | `decisionBookWidth` | diagnostics (Decide lane, inside the decision dashboard) |
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
- Research sub-tab visibility is owned by `modules/researchTabs.js` (`research-tab-hidden`). Parked sections (`#sectorsSection`, `#moversSection`, `#researchAdvancedTools`) stay in the DOM for frozen aliases and are revealed only by `activateResearchTabForSection()`.
- Layout wrappers (`#researchCheckPanel`, `#researchPortfolioPrimary`, `#researchPortfolioSecondary`) hide with their Research tab. `#backtestWorkspace`, `#researchBacktestWrap` and `#backtestSection` live on the Backtest screen. Research grids are one full-width column so hidden siblings cannot leave a vacant second track.

## Related Pages

- [[section-migration-map]] — locked section layout and rollout history
- [[ux-kpi-baseline]] — KPI events keyed to screen views
- [[static-module-layout]] — frontend module map including `router.js`
- [[webapp-dashboard]] — dashboard overview
- [[backtest]] — Backtest tab leaderboard, queue, briefing, and frozen aliases
- [[scan-catalog]] — `?section=scanstudio` Scan studio
- [[operator-desk]] — `?section=desk` operator desk
- [[grok-agent-orchestration]] — `?section=grok` review on Today
- [[event-probability-layer]] — `event-risk` alias target
- [[companyfacts-pricing]] — `pricing` alias target
- [[sharadar-bundle]] — `sharadar` / `pit` / `sharadar-compare` alias targets

---

*Last compiled: 2026-10-07*
