# Experiment Registry Schema

Canonical record file: `validation_artifacts/experiment_registry.jsonl`

## Record Fields

- `schema_version` (int) — currently `1`.
- `recorded_at` (ISO8601 UTC) — append timestamp.
- `event_type` (string) — e.g. `advisory_promotion_decision`, `strategy_promotion_decision`,
  `rank_model_experiment`, `feature_ablation`, `prob_rank_promotion_decision`.
- `target` (string) — governed target (`advisory_model`, `strategy_champion_params`,
  `PROB_RANK_MODE`, `prob_rank_portfolio`, etc.).
- `decision` (string) — `promote`, `reject`, `hold`, `promote_shadow`, `promote_live`,
  `recorded`, or other explicit lifecycle decision.
- `rationale` (array[string]) — machine/audit-readable reasons.
- `gates` (object) — threshold values and gate inputs used to decide.
- `metadata` (object) — additional context (artifacts, apply flag, validation status).

## Governance Rules

1. All promotion decision scripts append exactly one registry record per decision.
2. Registry writes are append-only JSONL.
3. Schema validation runs in `scripts/validate_experiment_registry.py`.
4. Promotion review must reference the latest registry record and rationale.
