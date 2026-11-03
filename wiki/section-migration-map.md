---
source: schwab_skill/webapp/static/index.html, frontend redesign roadmap
created: 2026-06-10
updated: 2026-09-14
tags: [frontend, ux, migration, rollout, feature-flags, locked]
---

# Section Migration Map & Rollout Checkpoints

> Approved mapping of every dashboard section to its target screen, position,
> and disclosure level. **Locked 2026-06-26** after the four-tab UI redo
> (Today / Research / System / Settings).

## Locked layout (2026-06-26)

| Tab | Landing | Primary workflow | Collapsed by default |
|-----|---------|------------------|----------------------|
| **Today** | `#todaySummaryLanding` | Scan → detail → pending kanban | Scan diagnostics (Pro) |
| **Research** | `#researchSummaryLanding` (Portfolio tab only) | Portfolio book / Quick check ticker | Sectors, movers, recovery/learning (parked; `?section=` still works) |
| **Backtest** | `#backtestSummaryLanding` | Leaderboard, queue, selected result, related evidence | Live monitor (`#backtestLiveMonitor`); run-another form (`#backtestSection`) |
| **System** | `#systemSummaryLanding` | Health ribbon tiles | Status, decision dashboard, calibration bundle |
| **Settings** | `#settingsSummaryLanding` | Connect Schwab + live-order controls in summary | Account security (2FA); presets hidden in Simple |

Research cards stack full-width (`layout-2` / check panel are 1-col). `#backtestSection` lives on the Backtest screen inside `#researchBacktestWrap` as a collapsed “Run another backtest” form. `#backtestLeaderboard` ranks finished queue runs (min 35 trades, finite PF). Result summary shows net PF vs the mean ≥ 1.20 gate; last-N compare is per-window, not a multi-era verdict.

Cross-mode banners: `#platformKillSwitchBanner`, `#connectSchwabBanner` (SaaS onboarding incomplete; tabs stay usable).

Validation at lock: `pytest tests/test_static_router.py tests/test_scan_transparency_contract.py` (27 passed).

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
| `sectorsSection` | operations (collapsed disclosure) | **research → Quick check sub-tab** | moved off Today; lazy load on expand | — |
| `moversSection` | operations (collapsed disclosure) | **research → Quick check sub-tab** | moved off Today; lazy load on expand | — |
| `cockpitSection`, `cockpitMergedPanel` | operations (merged panel) | **research → Portfolio sub-tab** | market context is analysis, not daily execution | — |
| `systemStatusCompact` | operations + diagnostics | **diagnostics only (deprecated)** | replaced by `#systemSummaryLanding`; compact line kept for legacy writers | — |
| `systemSummaryLanding`, `#systemAlertBanner` | diagnostics | diagnostics | summary KPIs + critical alert banner (mirrors Today priority feed) | — |
| `healthRibbon`, `statusDetailsPanel` | diagnostics | diagnostics | primary health tiles + collapsed detailed status | — |
| `systemDecisionPanel`, `decisionDashboardCard` | diagnostics | diagnostics | collapsed decision dashboard + ablation controls | — |
| `systemQualityDiagnostics` | diagnostics | diagnostics | collapsed calibration, shadow scoreboard, review loop | — |
| `diagnosticsWorkflowStrip` | diagnostics | deprecated | replaced by summary landing shortcuts | — |
| `operationsWorkspaceIntro`, `workflowPrimary`, `scanSection`, `scanDetailPanel`, `pendingSection` | operations | operations | **Today is kanban-only** — scan → evaluate → approve | — |
| `researchSummaryLanding` | — | research | Portfolio-tab triage KPIs (hidden on Quick check) | — |
| `quickCheckSection`, `sectorsSection`, `moversSection` | research | research | **Quick check** shows ticker Brief/Deep only; sectors/movers parked, revealed by `?section=sectors` / `movers` | — |
| `backtestSection` | research (Validate tab) | **backtest** | Own top-level tab; form collapsed as Run another | — |
| `backtestSummaryLanding`, `backtestLeaderboard`, `backtestQueuePanel`, `backtestResultPanel`, `backtestRelatedPanel` | — | backtest | Results-first landing, ranked board, queue, next steps, related evidence | — |
| `reportSectionCard`, `secCompareSection` | research (Validate tab) | research | **Quick check → Deep** | — |
| `portfolioSection`, `performanceSection`, `cockpitMergedPanel`, `cockpitSection` | research | research | **Portfolio sub-tab** — positions → performance → cockpit (opportunities lane parked) | — |
| `recoverySection`, `learningSection` | research (Advanced tab) | research | Parked `#researchAdvancedTools` — not on the default Quick check workspace | — |
| `toolsSection` | research | removed | replaced by `researchSummaryLanding` shortcuts | — |
| `healthRibbon`, `decisionDashboardCard`, `statusDetailsPanel`, `calibrationSection`, `shadowScoreboardSection`, `reviewLoopSection` | diagnostics | diagnostics | nested under summary + disclosures; priority feed stays on Today | — |
| `onboardingSection` | settings | settings | Connect Schwab wizard; auto-scroll when link incomplete | — |
| `settingsSummaryLanding` | — | settings | overview with live-order controls in `#settingsSummaryGuardrails` | — |
| `settingsSection` | settings | settings | Risk presets (hidden in Simple mode) | — |
| `settingsAccountPanel` | settings | settings | collapsed account security (2FA); billing UI hidden | — |
| `settingsGuardrailsSection` | settings | deprecated | controls moved into summary | — |
| `connectSchwabBanner` | cross-mode | cross-mode | banner when SaaS onboarding incomplete; browsing allowed | — |
| `settingsWorkspaceIntro`, `settingsWorkflowStrip` | settings | deprecated | replaced by summary landing | — |

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
| **UI redo lock** | none (structural) | Four-tab layout + summary landings shipped | Static router + transparency tests green | revert frontend static bundle |

## Related Pages

- [[frontend-route-contract]] — route/alias stability rules this map obeys
- [[ux-kpi-baseline]] — KPIs and events gating each checkpoint
- [[usability-test-script]] — Checkpoint B validation script
- [[webapp-dashboard]] — dashboard overview

---

*Last compiled: 2026-08-20*
