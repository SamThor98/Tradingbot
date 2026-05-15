# Parameter Ablation Workflow

This workflow operationalizes parameter-importance validation, conviction-lift
validation, and promotion guardrails for scanner tuning.

## Files

- Manifest template: `scripts/ablation_manifest_v1.json`
- Raw runner: `scripts/run_param_ablation.py`
- Report scorer: `scripts/score_ablation_report.py`
- Report schema docs: `docs/ABLATION_REPORT_SCHEMA.md`
- Machine-readable schema: `docs/ablation_report.schema.json`

## 1) Configure Manifest

Edit:

- `objective.primary_metric`
- `objective.guardrails`
- `objective.promotion_rules`
- `data_splits.train_windows` / `test_windows`
- `experiments` and `interaction_followups`
- `execution.per_variant_backtest_command`

Command placeholders:

- `{python}` -> active Python executable
- `{start_date}` -> split start date
- `{end_date}` -> split end date
- `{split_name}` -> generated split identifier
- `{variant_id}` -> generated variant identifier

## 2) Run Raw Ablation

Baseline + single-parameter sweeps:

```bash
python scripts/run_param_ablation.py --manifest scripts/ablation_manifest_v1.json
```

Include interaction grids:

```bash
python scripts/run_param_ablation.py --manifest scripts/ablation_manifest_v1.json --include-interactions
```

Optional preflight baseline validation:

```bash
python scripts/run_param_ablation.py --manifest scripts/ablation_manifest_v1.json --run-baseline-command
```

Output:

- `validation_artifacts/ablation_raw_<run_id>.json`

## 3) Score Report + Confidence Intervals

```bash
python scripts/score_ablation_report.py --raw-artifact validation_artifacts/ablation_raw_<run_id>.json
```

Outputs:

- `validation_artifacts/ablation_report_<run_id>.json`
- `validation_artifacts/ablation_report_<run_id>.md`

The scorer computes:

- paired split-level lift vs baseline
- bootstrap CI for relative lift
- regression flags (`drawdown`, `trade_count`, split failures)
- pass/fail by manifest promotion rules

## 4) Conviction Validation Loop

Run conviction checks after scoring:

```bash
python scripts/analyze_cohorts.py
python scripts/score_hypothesis_outcomes.py
python scripts/score_counterfactual_outcomes.py --horizon-days 5
```

Use these outputs to confirm:

- monotonic conviction buckets
- incremental lift of conviction-aware variants
- stability across regime slices

## 5) Promotion Guidance

Promote only variants with:

- `pass=true` in ablation report
- positive conviction-lift evidence
- no drawdown/trade-count regressions

Keep rollout path: `off -> shadow -> live`.
