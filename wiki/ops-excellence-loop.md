---
source: n/a (operations governance)
created: 2026-04-30
updated: 2026-04-30
tags: [operations, incident, restore, drills, postmortem]
---

# Ops Excellence Loop

> Recurring incident/restore drills with objective pass/fail gates and tracked follow-through.

## Cadence Calendar

- **Weekly (Tue)**: incident tabletop drill (30 min).
- **Biweekly (Thu)**: restore drill (staging restore + validation).
- **Monthly (first business day)**: leadership review of drill outcomes, SLA closure, and recurring risks.

## Incident Drill Checklist

1. Start timer and assign roles (incident commander, comms, scribe).
2. Trigger scenario (API 5xx burst, queue stall, token outage, or webhook burst).
3. Execute runbook from [[incident-response-saas]].
4. Capture timestamps for detect, acknowledge, mitigate, recover.
5. Verify SLO impact estimate and customer comms draft.

### Pass Criteria

- Mitigation selected within 10 minutes.
- Recovery path identified and executed within 30 minutes.
- Postmortem doc created within 24h.

### Fail Criteria

- No clear owner within 5 minutes.
- Runbook ambiguity blocks response.
- Missing evidence for timeline or decisions.

## Restore Drill Checklist

1. Restore latest backup to non-prod environment.
2. Run integrity checks from [[backup-restore]].
3. Execute `python scripts/validate_all.py --profile local --strict`.
4. Verify critical APIs: `/api/status`, `/api/health/deep`, `/api/validation/status`.
5. Record restore duration and data-loss window (RPO).

### Pass Criteria

- Restore completed within declared RTO target.
- Data loss within declared RPO target.
- Validation run passes after restore.

### Fail Criteria

- Restore time exceeds target by >20%.
- Missing backup chain segment or inconsistent schema.
- Validation fails on restored environment.

## Evidence Artifacts

- `validation_artifacts/drills/incident_<YYYYMMDD>.md`
- `validation_artifacts/drills/restore_<YYYYMMDD>.md`
- Linked postmortem and action owners.

## Related Pages

- [[incident-response-saas]]
- [[backup-restore]]
- [[slo-alerting]]
- [[validation]]
