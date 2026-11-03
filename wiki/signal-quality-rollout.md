---
source: Brain/Runbooks/Signal Quality Rollout.md
created: 2026-04-13
updated: 2026-10-08
tags: [runbook, quality]
---

# Signal Quality Rollout

> Plan for rolling out quality gates from shadow to enforced mode.

## Sequence

1. `QUALITY_GATES_MODE=shadow` — track would-filter counts
2. Review diagnostics — analyze which signals would have been filtered
3. `QUALITY_GATES_MODE=soft` — filter when 2+ weak reasons
4. Monitor signal quality and false positive rate
5. `QUALITY_GATES_MODE=hard` — filter on any single weak reason
6. Evaluate hit rates pre/post

## Related Pages

- [[vcp-invalidation-exit]] — Stage 2l grace replay; VCP skip is not live
- [[purged-net-rank]] — Stage 2l embargoed rank; not promoted
- [[quality-gates]] — modes and thresholds
- [[signal-scanner]] — where gates are applied
- [[validation]] — validation pipeline
- [[multi-sleeve-trading-system-constitution]] — S0 live stack as core sleeve in target multi-sleeve system
- [[thirty-day-signal-cut-plan]] — active cycle through 2026-11-07; parked stages stay parked

---

*Last compiled: 2026-09-26*
