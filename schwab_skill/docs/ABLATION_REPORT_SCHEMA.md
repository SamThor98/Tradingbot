# Ablation Report Schema

`scripts/score_ablation_report.py` emits `validation_artifacts/ablation_report_<run_id>.json`
with the following structure.

## Top-level Fields

- `schema_version` (`string`) - currently `"1.0"`.
- `run_id` (`string`) - copied from raw artifact run ID.
- `generated_at` (`string`) - UTC ISO timestamp.
- `raw_artifact` (`string`) - source `ablation_raw_*.json` path.
- `objective` (`object`) - scoring and guardrail metadata.
- `summary` (`object`) - pass/fail counts and best variant.
- `leaderboard` (`array<object>`) - one scored row per non-baseline variant.

## `objective`

- `primary_metric` (`string`) - e.g. `expectancy_per_trade`.
- `guardrails` (`array<string>`) - e.g. `max_drawdown`, `trade_count`.
- `promotion_rules` (`object`) - threshold contract copied from manifest.
- `bootstrap_samples` (`integer`) - CI bootstrap iterations.
- `confidence_level` (`number`) - CI level, usually `0.95`.

## `summary`

- `variant_count` (`integer`)
- `pass_count` (`integer`)
- `fail_count` (`integer`)
- `best_variant` (`string | null`)

## `leaderboard[]`

- `variant_id` (`string`)
- `experiment_id` (`string`)
- `variant_type` (`string`) - `single_param` / `interaction`.
- `description` (`string`)
- `env_overrides` (`object<string,string>`)
- `primary_metric_key` (`string`) - resolved metric key used for scoring.
- `primary_baseline_mean` (`number | null`)
- `primary_variant_mean` (`number | null`)
- `relative_lift_vs_baseline` (`number | null`)
- `ci_relative_lift_lower` (`number | null`)
- `ci_relative_lift_upper` (`number | null`)
- `paired_primary_count` (`integer`)
- `guardrails` (`object`)
  - per-guardrail objects with:
    - `metric_key` (`string`)
    - `baseline_mean` (`number | null`)
    - `variant_mean` (`number | null`)
    - `paired_count` (`integer`)
    - optional derived fields like `relative_worsening` or `ratio_vs_baseline`
- `regression_flags` (`array<string>`)
- `pass` (`boolean`)

## Regression Flags

Current scorer emits:

- `missing_primary_metric`
- `primary_lift_below_threshold`
- `missing_drawdown_guardrail`
- `drawdown_worsening_exceeds_limit`
- `missing_trade_count_guardrail`
- `trade_count_ratio_below_minimum`
- `variant_contains_failed_splits`

## Compatibility Notes

- New fields may be added without bumping `schema_version`.
- Existing keys are stable for dashboard ingestion and CI parsing.
