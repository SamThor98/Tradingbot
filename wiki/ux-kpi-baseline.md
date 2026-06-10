---
source: schwab_skill/webapp/static/app.js (instrumentation), frontend redesign roadmap
created: 2026-06-10
updated: 2026-06-10
tags: [frontend, ux, kpi, analytics, instrumentation]
---

# UX KPI Baseline & Instrumentation Events

> Locked KPI definitions and the frontend event taxonomy used to measure the
> frontend redesign (Actions 1 and 9 of the redesign roadmap). This page is the
> baseline contract: event names and KPI formulas here must not be renamed
> without updating this page first.

## KPIs (locked)

| KPI | Definition | Computed from events | Target |
|-----|------------|----------------------|--------|
| Time-to-first-scan | Seconds from page load (`screen_view` with `initial: true`) to first `scan_started` in a session | `screen_view` → `scan_started` | < 30s for returning users |
| Scan → queue conversion | Sessions with `trade_staged` / sessions with `scan_started` (signals > 0) | `scan_started`, `trade_staged` | Establish baseline, then +10% |
| Queue → approve conversion | Sessions with `trade_approved` / sessions with `trade_staged` | `trade_staged`, `trade_approved` | Establish baseline |
| Clicks-to-execute | UI interactions from landing to approve dialog submit | manual count in usability sessions | <= 2 clicks from landing to main flow (skill acceptance criterion) |
| Status interaction rate | `priority_feed_action_clicked` / sessions where the feed showed >= 1 item | `priority_feed_action_clicked` | Higher than legacy action-center click-through |
| Settings detour rate | Sessions that visit `settings`/`diagnostics` screens before completing first `scan_started` | `screen_view` sequence | Decrease post-redesign |

## Existing instrumentation (do not rename)

- `trackProductEvent(eventName, properties)` in `schwab_skill/webapp/static/app.js`
  POSTs to `/api/analytics/event`. **SaaS-only**: gated by
  `state.publicConfig.saas_mode && state.accountMe.id`.
- `trackFunnelMilestoneOnce(...)` sends each funnel event at most once per session.
- `logEvent(...)` in `modules/logger.js` is the local activity log (all modes);
  it is the local-mode mirror for KPI events.

### Funnel milestones (existing, `FUNNEL_EVENTS` in `app.js`)

| Event | Fired from |
|-------|-----------|
| `signup` | Supabase email verification send |
| `auth_linked` | OAuth callback (market/account), status health check |
| `first_scan` | Scan completion (local, saas_inline, saas_celery, local_polling) |
| `first_pending_trade` | Pending queue refresh, queue-from-scan dialog, manual staging |
| `first_approved_trade` | Approve dialog success |
| `retained_session` | 60s after load (SaaS) |
| `billing_checkout_started` / `billing_portal_opened` / `billing_checkout_success` / `billing_checkout_canceled` | Billing panel and redirect query |

### UI events added by the redesign (new)

Emitted via `trackUiEvent(name, props)` (thin wrapper: `trackProductEvent` in
SaaS, plus a `console.debug("[ui-event]", ...)` trace locally for usability
sessions — no new backend route, no activity-log noise).

| Event | Properties | Fired when |
|-------|-----------|-----------|
| `screen_view` | `{ screen, initial }` | `applyScreenMode()` activates a screen |
| `scan_started` | `{ transport }` | User triggers a scan (any transport) |
| `candidate_opened` | `{ ticker, source }` | Scan detail panel opened for a ticker |
| `trade_staged` | `{ source }` | Trade queued (scan dialog or manual) |
| `trade_approved` | `{ trade_id }` | Approve succeeds |
| `priority_feed_action_clicked` | `{ item_key, severity }` | User clicks a priority feed item action |

## Baseline lock procedure

1. Ship instrumentation with all redesign flags OFF (rollout Checkpoint A, see
   [[section-migration-map]]).
2. Collect >= 1 week (or >= 20 sessions) of events with the legacy layout.
3. Record baseline values in this page's table before enabling any flag.

## Related Pages

- [[frontend-route-contract]] — screen/route contract the KPIs reference
- [[section-migration-map]] — section moves and rollout checkpoints
- [[usability-test-script]] — moderated test script tied to these KPIs
- [[webapp-dashboard]] — local dashboard the frontend serves
- [[feature-flags]] — backend feature toggles (distinct from frontend UI flags)

---

*Last compiled: 2026-06-10*
