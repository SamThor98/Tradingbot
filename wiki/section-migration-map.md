---
source: schwab_skill/webapp/static/index.html, frontend redesign roadmap
created: 2026-06-10
updated: 2026-06-10
tags: [frontend, ux, migration, rollout, feature-flags]
---

# Section Migration Map & Rollout Checkpoints

> Approved mapping of every dashboard section to its target screen, position,
> and disclosure level (Action 3), plus the phased rollout checkpoints with
> feature flags and rollback (Action 10).

## Migration principles

- No section is removed; everything stays reachable (preservation checklist in
  the front-end-design skill).
- Section `id`s and `?section=` aliases stay stable per [[frontend-route-contract]].
- Changes ship behind frontend UI flags (`modules/featureFlags.js`):
  `priority_feed`, `ops_slim_default`, `unified_auth_block`, `screen_controllers`.
  All default OFF; rollback = flip the flag (localStorage / `?ff=` override).

## Section map

| Section id | Current screen | Target screen | Change | Flag |
|------------|----------------|---------------|--------|------|
| `dashboardToday` (`pendingSummaryStrip`, `actionCenter`) | operations + diagnostics | same | `actionCenter` replaced by priority feed surface | `priority_feed` |
| `operationsWorkspaceIntro`, `workflowPrimary`, `scanSection`, `scanDetailPanel`, `pendingSection` | operations | operations | unchanged (primary workflow, above the fold) | — |
| `sectorsSection` | operations (always-open card) | operations (collapsed `<details>` below workflow) | demoted to disclosure; lazy load on expand | `ops_slim_default` |
| `moversSection` | operations (always-open card) | operations (collapsed `<details>` below workflow) | demoted to disclosure; lazy load on expand | `ops_slim_default` |
| `quickCheckSection`, `toolsSection`, `recoverySection`, `learningSection`, `backtestSection`, `reportSectionCard`, `secCompareSection`, `portfolioSection`, `performanceSection` | research | research | unchanged | — |
| `kronosWorkspaceIntro`, `kronosForecastSection`, `kronosAboutSection` | kronos | kronos | advisory copy deduplicated (one line per screen) | — |
| `healthRibbon`, `decisionDashboardCard`, `statusDetailsPanel`, `calibrationSection`, `shadowScoreboardSection`, `reviewLoopSection` | diagnostics | diagnostics | unchanged; priority feed links into these as the deep view | — |
| `onboardingSection`, `settingsSection` | settings | settings | settings logic guide collapsed to summary + link; inline auth block unified | `unified_auth_block` (auth part) |

## Status surface consolidation

The same health/token state was shown in >= 4 places. Target model:

| Surface | Disposition |
|---------|-------------|
| `#actionCenter` | Replaced by **priority feed** (`modules/priorityFeed.js`) when `priority_feed` is on; legacy writer API kept as the feed's intake |
| Token-expiry escalations (`renderSchwabTokenHealth`) | Feed items (severity `warn`/`error`), deep-link to `#statusDetailsPanel` |
| Health-ribbon escalations (`prioritizeActionCenterFromHealth`) | Feed items, deep-link to `#healthRibbon` |
| Kill-switch banner | Stays as its own cross-mode banner (regulatory prominence) and mirrors into the feed |
| Notifications bell | Unchanged (history); feed shows only *actionable* current items |
| `#healthRibbon` / `#statusDetailsPanel` | Stay on Diagnostics as the deep view |

## Rollout checkpoints

| Checkpoint | Flags ON | Visible change | Validation gate | Rollback |
|------------|----------|----------------|-----------------|----------|
| A | none | Copy/disclosure cleanup only (unflagged) | KPI events fire (`screen_view`, `scan_started`, ...); baseline recorded in [[ux-kpi-baseline]] | git revert of copy commit |
| B | `ops_slim_default`, `priority_feed` | Slim Operations landing + unified priority feed | Usability script ([[usability-test-script]]) with 3-5 users; main flow <= 2 clicks | flags off |
| C | `unified_auth_block`, then `screen_controllers` | Unified auth presentation; controller-based boot | All 5 screens smoke-tested in simple/standard/pro; deep links + shortcuts unchanged | flags off |
| Cleanup | flags removed | none | After >= 1 stable week at C, delete legacy code paths and flags | n/a |

## Related Pages

- [[frontend-route-contract]] — route/alias stability rules this map obeys
- [[ux-kpi-baseline]] — KPIs and events gating each checkpoint
- [[usability-test-script]] — Checkpoint B validation script
- [[webapp-dashboard]] — dashboard overview

---

*Last compiled: 2026-06-10*
