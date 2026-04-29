---
name: front-end-design
description: Redesign TradingBot frontend UX without backend changes. Use when user requests better navigation, dashboard simplification, feature-preserving UI refactor, scan-results-trade workflow emphasis, diagnostics separation, or settings consolidation.
---

# Front-End Design (TradingBot)

## Core constraints
- Frontend-only changes under `schwab_skill/webapp/static/**`.
- Do not modify backend routes, models, or API contracts.
- Preserve all existing user-facing capabilities unless explicitly deprecated by user.
- Keep vanilla JS module architecture and shared `api` client usage.

## Product goal
Prioritize the primary workflow for Charles Schwab users:
1) run scans,
2) evaluate candidates with chart + scoring context,
3) stage, approve, and execute trades.

Move setup/connectivity/API management to Settings.
Move health/debug/status surfaces to Diagnostics.
Keep research tools available, not removed.

## Information architecture target
- `Operations` (default): Scan controls, scan results, ticker detail panel, trade queue, approvals.
- `Research`: Single-ticker research, backtest, SEC compare, full report, strategy chat.
- `Diagnostics`: Validation, API error rates, token/quote health, lifecycle/debug outputs.
- `Settings`: Schwab connect/onboarding, profile presets, live trading controls, 2FA, billing.

## Mandatory preservation checklist
- Scan run + diagnostics/funnel data visibility
- Pending queue, manual staging, approve/reject/delete actions
- Trade drawer decision/recovery flows
- Backtest hub + chat assistant
- SEC compare + full report
- Quick ticker check and chart rendering
- Portfolio/sectors/performance views
- Onboarding/connectivity + token/session auth controls
- Existing keyboard shortcuts and deep-link behavior

## UX quality bar
- Reduce cognitive load: one primary CTA cluster per page.
- Keep critical actions above the fold on Operations.
- Use consistent card hierarchy: primary actions > result context > advanced disclosures.
- Prefer progressive disclosure for advanced controls.
- Every scan ticker should expose immediate "view chart + score rationale".

## Implementation sequence
1. Inventory all existing sections and endpoints consumed.
2. Re-map sections into the 4-page IA while preserving IDs or alias routes.
3. Implement shared shell navigation and active-state context.
4. Build Operations-first layout with split pane (results list + ticker detail).
5. Rehome settings and diagnostics panels to dedicated pages.
6. Validate all existing API actions from UI still function.

## Acceptance criteria
- No backend file edits.
- All prior features accessible.
- Main flow (scan -> review -> queue -> execute) reachable in <= 2 clicks from landing.
- Research page still includes backtest + SEC compare + report + single ticker tools.
- Diagnostics and settings no longer distract from Operations landing.
