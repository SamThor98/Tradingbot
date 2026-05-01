# Strategy Promotion Operator Checklist

> Locked-cadence runbook for the risk-adjusted strategy improvement loop.
> See [[promotion-playbook]] for cross-cutting governance.

## Goal Profile (Locked)

- Objective priority: **risk-adjusted quality > return > drawdown > cross-era consistency**
- Drawdown tolerance: up to ~16%
- Throughput: balanced (no zero-trade eras)
- Regime behaviour: adaptive (size-down + stricter quality, never full off)
- Alpha style: hybrid (quality floor + dynamic sizing)
- Promotion strictness: moderate
- Cadence: weekly tune, biweekly promote

## Enforced Gates (defaults)

| Gate | Default | Source flag |
| --- | --- | --- |
| `min_oos_pf` | 1.15 | `--min-oos-pf` |
| `min_oos_pf_delta` | +0.01 | `--min-oos-pf-delta` |
| `min_pf_delta` | +0.02 | `--min-pf-delta` |
| `min_expectancy_delta` | 0.00 | `--min-expectancy-delta` |
| `max_drawdown_degrade_cap` | +2.0% (abs) | `--max-drawdown-degrade-cap` |
| `min_trades_threshold` | 35 (aggregate min) | `--min-trades-threshold` |
| `min_trades_per_era` | 20 (per-era floor) | `--min-trades-per-era` |
| Zero-trade era rejection | always on | n/a |
| Regime participation floor | 30% per era | `--min-participation-floor-pct` |

All gates are surfaced as explicit `reasons` in the
`strategy_promotion_report_*.json` and `strategy_promotion_decision_*.json`
artifacts so a failure is never silent.

## Weekly Cadence — Tune + Diagnostics Refresh

Run on Mondays (or first business day of the week). Stays in dry-run; no
champion change is committed.

```bash
cd schwab_skill

# 1. Refresh walk-forward search and ranking. NO --apply.
python scripts/run_strategy_tune_cycle.py \
  --min-trades 35 \
  --min-trades-per-era 20 \
  --min-oos-pf 1.15 \
  --min-oos-pf-delta 0.01 \
  --min-pf-delta 0.02 \
  --min-expectancy-delta 0.0 \
  --max-drawdown-degrade-cap 2.0

# 2. Refresh diagnostics with sample-size discipline applied.
python scripts/analyze_guardrails.py

# 3. Sanity-check the regime counterfactual + hybrid alpha policy.
python scripts/validate_regime_counterfactual_guardrail.py \
  --min-participation-floor-pct 0.30
python scripts/validate_hybrid_alpha_policy.py
```

What to inspect:

- `validation_artifacts/optimization_candidate_ranking_*.json` —
  is there a robust-gate-pass candidate? If not, no biweekly promote.
- `validation_artifacts/strategy_tune_cycle_summary_*.json` — `passed=true`
  is the green signal to consider promotion at the next biweekly slot.
- `guardrail_analysis_summary.json` — note any `low_confidence: true`
  buckets; do **not** hand-tune off them.

## Biweekly Cadence — Promotion Decision

Run on alternating Wednesdays (after the latest weekly tune). This step
re-confirms gates against the latest window before applying.

```bash
cd schwab_skill

# 1. Append a signed approval entry to the ledger (operator action).
python scripts/promotion_ledger.py append \
  --target strategy_champion_params \
  --reason "Biweekly: <date> — challenger passed weekly cycle and fresh-window confirm"

# 2. Re-run gates against latest window AND apply on success.
python scripts/run_strategy_promotion_biweekly.py --apply

# 3. Verify the ledger and the new champion artifact.
python scripts/promotion_ledger.py verify
cat artifacts/strategy_champion_params.json
```

If `run_strategy_promotion_biweekly.py` exits non-zero:

1. Read `validation_artifacts/strategy_promotion_biweekly_*.json` and the
   referenced `strategy_promotion_report_*.json`.
2. Inspect `reasons[]` — every failed gate is explicit (e.g.
   `zero_trade_era_detected`, `drawdown_degraded_too_much`).
3. Do **not** lower gates to force a promotion. Either rerun the weekly
   tune to find a different candidate, or skip this biweekly slot.

## Rollback (if a promoted champion misbehaves)

1. Restore the previous champion params snapshot (Git history of
   `artifacts/strategy_champion_params.json`).
2. Append a `target=strategy_champion_params` ledger entry with a
   rollback reason.
3. Re-run `python scripts/validate_all.py --profile server --strict` to
   confirm the regression cleared.

## Cross-References

- [[promotion-playbook]] — Cross-cutting promotion governance
- [[plugin-modes]] — OFF→SHADOW→LIVE convention referenced by hybrid alpha
- [[hypothesis-ledger]] — Outcome ledger feeding diagnostic refresh
- `schwab_skill/scripts/_strategy_gates.py` — Pure gate logic shared with tests

---
*Owner: trading-bot ops; review every quarter.*
