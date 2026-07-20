# `schwab_skill/docs` Documentation Index

Code and validators remain executable truth. This folder holds operator-facing
specs, checklists, and runbooks that are too detailed for top-level READMEs.

## Canonical Ownership

- Architecture and concept overviews: `wiki/` (start at `wiki/index.md`)
- Step-by-step operator procedures: `schwab_skill/*.md` and `schwab_skill/docs/*.md`
- Human planning and journaling: `Brain/` (secondary to wiki/procedural docs)

## Contents

| File | Primary use |
|---|---|
| `BACKTEST_CATALOG.md` | Polished catalog of multi-era backtests, stacks, sweeps, and promotion decisions |
| `Backtest_Catalog.docx` | Plain-English Word summary of the same results (regenerate via `scripts/generate_backtest_catalog_docx.py`) |
| `PROBABILISTIC_RANKING_RESEARCH_ARCHITECTURE.md` | Probabilistic ranking research platform (Phases B–F; ops via `run_prob_rank_ops_pipeline.py`) |
| `SAAS_DEPLOYMENT.md` | Production deployment checklist and environment setup |
| `CONNECT_SCHWAB_END_USERS.md` | End-user Schwab connect flow and implementation guide |
| `LEGAL_DISCLOSURES.md` | Legal/disclaimer language for UI and docs |
| `FRONTEND_DESIGN_SYSTEM.md` | Dashboard UI design conventions |
| `AGENT_INTELLIGENCE_IMPLEMENTATION_PLAN.md` | Agent-intelligence rollout and implementation plan |
| `STRATEGY_PROMOTION_OPERATOR_CHECKLIST.md` | Promotion gate and operator checklist |
| `RELEASE_NOTES_PLUGIN_PROMOTIONS.md` | Historical plugin promotion notes |
| `GUARDRAIL_VALIDATION_MATRIX.md` | Guardrail-specific validation cases and severity |
| `PREDICTION_MARKET_EXPERIMENT.md` | Experiment design and constraints |
| `PREDICTION_MARKET_ROLLOUT.md` | Rollout sequence and safeguards |
| `EXPERIMENT_REGISTRY_SCHEMA.md` | Registry schema for experiment tracking |
| `DECISION_DASHBOARD_KPI_SPEC.md` | KPI contract for the decision dashboard |
| `SLO_METRIC_MAPPING.md` | SLI/SLO metric mapping and alert contract |
| `POSTMORTEM_TEMPLATE.md` | Incident postmortem template |
| `POSTMORTEM_SLA.md` | Incident response and postmortem expectations |
| `TYPE_TEST_RATCHET.md` | Type/test ratchet process and guardrails |
| `CORE_BOUNDARY_PLAN.md` | Codebase boundary and refactor plan |
| `PARAM_ABLATION_WORKFLOW.md` | Manifest-driven parameter ablation and scoring workflow |
| `ABLATION_REPORT_SCHEMA.md` | Field-level schema for ablation leaderboard artifacts |

## Related Entrypoints

- Main operator guide: `../README.md`
- Validation workflow: `../VALIDATION_RUNBOOK.md`
- Promotion workflow: `../CANARY_RUNBOOK.md` and
  `STRATEGY_PROMOTION_OPERATOR_CHECKLIST.md`
- Wiki catalog: `../../wiki/index.md`
