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
| Reliability state | Platform owner | `healthy` only when validation pass, SLO gate pass, and ablation `best.pass` when an ablation artifact exists | `continuous_validation_status.json`, `latest_slo_gate_status.json`, latest ablation artifact |
| Validation status | Platform owner | Latest validation run status and pass/fail | `validation_artifacts/latest_validation_report.json` family |
| SLO gate status | Platform owner | Latest observability gate pass/fail with failure reasons | `validation_artifacts/latest_slo_gate_status.json` |
| Signals found | Strategy owner | Latest completed scan signal count | `AppState(last_scan)` |
| Dominant strategy | Strategy owner | Strategy attribution leader from latest scan | `last_scan.strategy_summary` |
| Data quality | Strategy owner | Data quality state from diagnostics summary | `last_scan.diagnostics_summary` |
| Promotion readiness | Platform + Strategy | Gate-ready when validation and SLO both pass. If an ablation artifact exists, `best.pass` is required too. The tile subtitle says `validation + SLO + ablation` in that case | Computed at API layer |
| Mean PF | Strategy owner | Headline row `exit_grace_breakout_buffer_0.010` in `validation_artifacts/signal_stack_counterfactual_control_legacy_aug.json`. If that mean is missing, the tile may show `hold_21_40d_pf` only with an explicit fallback label. This is not phase-1 `control_legacy` and not ledger book `bare_pts52w_cap37` | `signal_stack_counterfactual_{run_id}.json` |
| Book width | Strategy owner | Live top 5 versus p76 top 10 and the full p76 book. `apply_live` stays false. Labeled `validation_artifacts/cap37_unsorted_vs_live.json`, separate from the signal-stack PF | `validation_artifacts/cap37_unsorted_vs_live.json` |
| Latest promotion decision | Strategy owner | Most recent experiment/promotion decision and timestamp | `validation_artifacts/experiment_registry.jsonl` |

## Refresh Cadence

- API/UI refresh: every `refreshAll()` execution (manual and startup).
- Freshness chip uses `signal_stack_counterfactual.generated_at` from the artifact the Mean PF tile cites. A missing artifact date is labeled missing. Page-fetch time is not the as-of.
- Weekly leadership ritual: review Monday, compare with drill outcomes and error budget posture.
- Monthly recalibration: update KPI thresholds and ownership if SLO policy or promotion policy changes.
