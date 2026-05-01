# SLO To Metric Mapping

This document maps each production SLI/SLO to concrete metric sources and
validation gates used by TradingBot automation.

## SLI/SLO Matrix

| SLI | Target | Source | Validation Gate |
| --- | --- | --- | --- |
| API availability | 99.5% monthly | `GET /api/health/ready` uptime checks + `http_requests_total` / `http_5xx_total` from `webapp/prometheus_metrics.py` | `scripts/validate_observability_gates.py` + `scripts/validate_all.py` |
| API 5xx rate | `<1%` per 15m (page at `>2%` for 10m) | `http_5xx_total / http_requests_total` counters | `scripts/validate_observability_gates.py --web-base-url ...` |
| Scan success ratio | `>=97%` daily | `scan_tasks_total`, `scan_tasks_failed_total` counters in Celery task execution | `scripts/validate_observability_gates.py` (execution events + task failures) |
| Order execution success ratio | `>=99%` daily | `order_tasks_total`, `order_tasks_failed_total` counters in Celery task execution | `scripts/validate_observability_gates.py` |
| Queue latency (scan/order) | p95 scan `<=120s`, order `<=30s` | `scan_task_duration_seconds_*`, `order_task_duration_seconds_*` histograms | `/metrics` dashboard + `scripts/validate_observability_gates.py` thresholds |

## Release/Promotion Gate Contract

Promotion-time apply paths must satisfy all of:

1. Latest validation report exists at `validation_artifacts/latest_validation_report.json`.
2. Latest validation report has `passed: true`.
3. `validate_observability_gates` step is present and passing.
4. No baseline regressions in latest report (`baseline_delta.regressed` empty when present).
5. If `validation_artifacts/latest_slo_gate_status.json` exists, it must be `passed: true`.
6. If `validation_artifacts/error_budget_status.json` exists and sets `release_freeze: true`,
   promotion apply is blocked.

These checks are enforced in apply scripts through `scripts/release_gate.py`.
