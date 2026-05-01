# Postmortem Action Closure SLA

This policy defines action-item closure speed and escalation for incidents and restore drills.

## Severity Classes

- **P0**: production outage, critical execution or auth failure
- **P1**: major degradation, partial outage, elevated error budget burn
- **P2**: localized degradation, tooling/runbook gap, drill failure without prod impact

## Closure Targets

| Severity | Create postmortem | Assign owners | First remediation PR | Action closure target |
| --- | --- | --- | --- | --- |
| P0 | within 24h | within 24h | within 72h | 14 calendar days |
| P1 | within 48h | within 48h | within 7 days | 21 calendar days |
| P2 | within 5 days | within 5 days | within 14 days | 30 calendar days |

## Escalation

- Breach by 3 days: escalate to engineering lead.
- Breach by 7 days: escalate in weekly leadership dashboard review.
- Repeat breach (same owner, 2 cycles): require explicit risk acceptance note.

## Evidence Requirements

Every action item includes:

1. Owner and due date.
2. Verification artifact (PR, runbook update, validation artifact).
3. Linked incident or drill document.
