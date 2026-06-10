---
source: frontend redesign roadmap (Action 9)
created: 2026-06-10
updated: 2026-06-10
tags: [frontend, ux, usability, testing]
---

# Usability Test Script (Frontend Redesign)

> Moderated usability script for 3-5 representative users, run at rollout
> Checkpoint B (see [[section-migration-map]]). Success criteria map to the
> KPIs in [[ux-kpi-baseline]].

## Protocol

- **Participants:** 3-5 Charles Schwab users who actively trade equities;
  at least one first-time user of this dashboard, at least one returning user.
- **Setup:** Dashboard at Checkpoint B (`ops_slim_default` + `priority_feed`
  ON via `?ff=ops_slim_default,priority_feed`), Schwab connected, test data
  available. Screen + audio recording with consent. Think-aloud prompt.
- **Duration:** ~30 minutes per session.
- **Moderator rule:** Do not hint at navigation. If stuck > 2 minutes, note the
  failure and unblock with the smallest possible hint.

## Tasks

| # | Task prompt (read verbatim) | Success criteria | KPI |
|---|------------------------------|------------------|-----|
| 1 | "Find today's trade candidates." | Runs a scan from the landing screen without navigating elsewhere; <= 1 click to start | Time-to-first-scan; clicks-to-execute |
| 2 | "Pick a candidate and decide whether you'd trade it." | Opens the candidate detail (chart + score rationale) directly from the results table | `candidate_opened`; <= 2 clicks |
| 3 | "Stage that trade and approve it." | Queues from scan, finds the pending queue, completes the approve dialog | Scan→queue, queue→approve conversion |
| 4 | "Check whether your Schwab connection is healthy and when your token expires." | Locates token/health status (priority feed item or Diagnostics) in <= 3 clicks without using search | Status interaction rate |
| 5 | "Your Schwab connection dropped. Reconnect it." | Reaches the Settings connect flow and identifies the reconnect action in <= 2 clicks | Settings detour rate |

## Per-task measurements

- Time on task (start = prompt finished; stop = success criteria met or abandon).
- Click count and wrong-turn count (navigations away from the optimal path).
- Single Ease Question (SEQ, 1-7) after each task.
- Quotes/observations: confusion points, ignored surfaces, copy misreads.

## Post-session questions

1. "What would you do first the next time you open this?"
2. "Was there anything on the main screen you never used or didn't understand?"
3. "Where would you expect to see warnings about your account or data?"
4. System Usability Scale (SUS) short form.

## Pass/fail gate for Checkpoint B

- >= 4/5 participants complete Tasks 1-3 unaided.
- Median Task 1+2+3 combined path is <= 2 clicks from landing to staging.
- No participant reports missing a critical warning that the priority feed
  displayed during their session.

If the gate fails: flags off (instant rollback), file findings here, iterate.

## Related Pages

- [[ux-kpi-baseline]] — KPI formulas behind the success criteria
- [[section-migration-map]] — rollout checkpoint this script gates
- [[frontend-route-contract]] — navigation contract under test

---

*Last compiled: 2026-06-10*
