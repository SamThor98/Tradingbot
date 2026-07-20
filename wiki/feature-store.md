---
source: schwab_skill/feature_store.py
created: 2026-04-17
updated: 2026-07-17
tags: [intelligence, persistence, learning, research]
---

# Feature Store

> Two-tier feature persistence: an **operational SQL** store for live scan
> events, and a planned **research Parquet** warehouse for probabilistic
> ranking experiments. See [[probabilistic-ranking-research-architecture]].

## Tier overview

| Tier | Technology | Path / table | Authority |
|---|---|---|---|
| **Operational** | SQLAlchemy (`feature_store` table) | SQLite/Postgres via `feature_store.py` | Live scan logging (implemented today) |
| **Research** | Versioned Parquet panels | `schwab_skill/research_store/` (gitignored, design) | Experiment source of truth (post-design implementation) |

Both tiers must share the same **feature names** from `feature_registry.json`
once the research architecture is implemented. Do not invent a second naming
scheme for live vs offline.

## Operational SQL store (current code)

`schwab_skill/feature_store.py` appends one row per ticker evaluated during a
scan into the `feature_store` table.

### Schema highlights

- Keys: `scan_id`, `scan_ts`, `ticker`
- Stage 2 / VCP: SMAs, `pct_from_52w_high`, volume ratio, pass flags
- Scores: `signal_score`, advisory / MiroFish fields
- Risk: forensic ratios, PEAD, SEC risk, quality JSON
- Decision: `decision` (`pass` / fail reasons), `regime_bucket`
- Overflow: `raw_features_json`

### Write path

`feature_store.record_event(...)` is called from the [[signal-scanner]] Stage B
path so every scan builds a labelled ops dataset for [[evolve-logic]] and
related tools.

### Read paths

- `get_feature_dataframe()` for offline analysis
- [[evolve-logic]] joins features to outcomes
- [[advisory-model]] uses its own training matrices; may consume overlapping fields
- Counterfactual scorer (`scripts/score_counterfactual_outcomes.py`) reads the
  suppressed-event log; it does not query this table as its primary source

## Research Parquet warehouse (design)

Planned under `schwab_skill/research_store/` (see canonical design doc):

- `panels/schema_v{N}/features/` — PIT feature rows keyed by
  `(asof_date, ticker, candidate_set_version, feature_schema_version)`
- `panels/.../labels/` — forward returns and strategy joins
- `datasets/` — frozen train matrices
- `models/` — LightGBM artifacts + SHAP metadata
- `feature_registry.json` — enabled flags, ablation groups, descriptions

v1 materializes **Stage-2 candidate days**, not a full SP1500×day panel.

## Retention

Operational rows are not pruned by `scripts/prune_validation_artifacts.py`.
Research Parquet should be treated as an append-only scientific archive;
use cold storage for multi-year panels if disk pressure appears.

## Related Pages

- [[probabilistic-ranking-research-architecture]] — Full research architecture
- [[agent-intelligence]] — Persona reliability (separate index files)
- [[mirofish-engine]] — Persona votes that may land in `raw_features_json`
- [[advisory-model]] — Baseline logistic features / labels
- [[self-study]] — Outcome attribution loop
- [[backtest]] — Multi-era trades joined as strategy labels

---

*Last compiled: 2026-07-17*
