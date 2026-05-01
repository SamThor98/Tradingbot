# Decision Dashboard KPI Spec

Single executive view: reliability, strategy quality, and promotion readiness.

## Data Contract

Primary API: `GET /api/decision-dashboard`

Sections:

1. `reliability`
2. `strategy_quality`
3. `promotion_readiness`

## KPIs, Owners, Definitions

| KPI | Owner | Definition | Source |
| --- | --- | --- | --- |
| Reliability state | Platform owner | `healthy` only when validation pass + SLO gate pass | `continuous_validation_status.json`, `latest_slo_gate_status.json` |
| Validation status | Platform owner | Latest validation run status and pass/fail | `validation_artifacts/latest_validation_report.json` family |
| SLO gate status | Platform owner | Latest observability gate pass/fail with failure reasons | `validation_artifacts/latest_slo_gate_status.json` |
| Signals found | Strategy owner | Latest completed scan signal count | `AppState(last_scan)` |
| Dominant strategy | Strategy owner | Strategy attribution leader from latest scan | `last_scan.strategy_summary` |
| Data quality | Strategy owner | Data quality state from diagnostics summary | `last_scan.diagnostics_summary` |
| Promotion readiness | Platform + Strategy | Gate-ready only when validation and SLO gate both pass | Computed at API layer |
| Latest promotion decision | Strategy owner | Most recent experiment/promotion decision and timestamp | `validation_artifacts/experiment_registry.jsonl` |

## Refresh Cadence

- API/UI refresh: every `refreshAll()` execution (manual and startup).
- Weekly leadership ritual: review Monday, compare with drill outcomes and error budget posture.
- Monthly recalibration: update KPI thresholds and ownership if SLO policy or promotion policy changes.
