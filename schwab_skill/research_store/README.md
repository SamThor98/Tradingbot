# Research feature warehouse

Point-in-time Parquet panels for probabilistic ranking experiments.

| Path | Contents | Git |
|---|---|---|
| `../research/feature_registry.json` | Schema v1 feature registry (canonical) | Tracked |
| `feature_registry.json` | Optional local override of the registry | Optional |
| `panels/schema_v{N}/features/` | Feature rows by `year=YYYY/{TICKER}.parquet` | Ignored |
| `panels/schema_v{N}/labels/` | Forward / strategy labels (Phase C) | Ignored |
| `datasets/` | Frozen train matrices (Phase C) | Ignored |
| `models/` | Model artifacts (Phase C) | Ignored |

Orchestrated ops (preferred):

```bash
# Offline smoke (no market data / chunks)
python scripts/run_prob_rank_ops_pipeline.py --smoke

# Pragmatic 5-era dual-run sample (50 liquid names; not full SP1500)
python scripts/refresh_prob_rank_dual_run_sample.py
python scripts/run_prob_rank_ops_pipeline.py \
  --tickers-file validation_artifacts/prob_rank_dual_run_sample_tickers.txt \
  --start 2015-01-01 --end 2024-12-31 \
  --run-id prob_rank_dual_run_sample
```

Step-by-step (same pipeline, manual):

```bash
python scripts/materialize_research_features.py --ticker AAPL --start 2019-01-01 --end 2024-06-01
python scripts/build_rank_dataset.py --ticker AAPL --start 2019-01-01 --end 2024-06-01
python scripts/train_prob_rank_model.py --dataset research_store/datasets/<dataset_id>.parquet
python scripts/analyze_prob_rank_counterfactual.py --run-id control_legacy_aug --model-dir research_store/models/<model_id> --features research_store/datasets/<dataset_id>.parquet
python scripts/run_prob_rank_portfolio_research.py --run-id control_legacy_aug --model-dir research_store/models/<model_id> --features research_store/datasets/<dataset_id>.parquet --sizing equal --register
python scripts/decide_prob_rank_promotion.py --artifact validation_artifacts/prob_rank_portfolio_control_legacy_aug_equal.json --requested shadow --apply
python scripts/validate_prob_rank_promotion.py
```

See `docs/PROBABILISTIC_RANKING_RESEARCH_ARCHITECTURE.md` (Phase F).

Point-in-time research (does **not** replace the Schwab-only runner):

```bash
python scripts/replay_pit_universe_era.py --asof 2016-06-01 --era late_bull
python scripts/run_factor_analytics.py --run-id stage2_only_aug --cap 37
python scripts/run_param_sweep.py --trades frozen_trades.parquet
# *** NOT PROMOTION-ELIGIBLE *** re-run survivors via run_multi_era_backtest_schwab_only.py
python scripts/retrain_barrier_label_challenger.py --dataset research_store/datasets/<dataset_id>.parquet
python scripts/build_research_warehouse.py
```

Deferred PIT fundamentals stay disabled until coverage ≥ `PIT_FUNDAMENTAL_COVERAGE_MIN` (never invent zeros).
