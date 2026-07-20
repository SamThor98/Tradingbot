#!/usr/bin/env python3
"""Train walk-forward LightGBM prob-rank model and write experiment report.

Example:
  python scripts/train_prob_rank_model.py --dataset research_store/datasets/rank_....parquet
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.dataset import resolve_feature_columns  # noqa: E402
from research.infer import predict_frame  # noqa: E402
from research.report import write_experiment_report  # noqa: E402
from research.train import DEFAULT_TARGET, train_prob_rank_model  # noqa: E402

LOG = get_logger("train_prob_rank_model")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=str, required=True, help="Path to dataset parquet")
    parser.add_argument("--target", type=str, default=DEFAULT_TARGET)
    parser.add_argument("--run-id", type=str, default=None, help="Report run id (default=model_id)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-boost-round", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true", help="Train but skip writing model/report")
    args = parser.parse_args(argv)
    setup_logging()

    ds_path = Path(args.dataset)
    if not ds_path.is_file():
        LOG.error("Dataset not found: %s", ds_path)
        return 2
    ds = pd.read_parquet(ds_path)
    if args.target not in ds.columns:
        LOG.error("Target %s missing from dataset columns", args.target)
        return 2

    feature_cols = resolve_feature_columns(ds)
    if not feature_cols:
        LOG.error("No feature columns resolved")
        return 2

    dataset_id = str(ds["dataset_id"].iloc[0]) if "dataset_id" in ds.columns else ds_path.stem
    try:
        artifact = train_prob_rank_model(
            ds,
            feature_cols,
            target_col=args.target,
            skill_dir=SKILL_DIR,
            seed=args.seed,
            num_boost_round=args.num_boost_round,
            dataset_id=dataset_id,
            write=not args.dry_run,
        )
    except Exception as exc:
        LOG.exception("Training failed: %s", exc)
        return 1

    scored = predict_frame(artifact, ds)
    run_id = args.run_id or str(artifact.get("model_id"))
    if not args.dry_run:
        out = write_experiment_report(
            run_id=run_id,
            artifact=artifact,
            scored_df=scored,
            skill_dir=SKILL_DIR,
        )
        LOG.info("Report written: %s", out)
    else:
        summary = {
            "model_id": artifact.get("model_id"),
            "folds": len((artifact.get("walk_forward") or {}).get("folds") or []),
            "top_features": list((artifact.get("feature_importance_gain") or {}).items())[:10],
        }
        print(json.dumps(summary, indent=2, default=str))

    LOG.info("model_id=%s folds=%s", artifact.get("model_id"), len((artifact.get("walk_forward") or {}).get("folds") or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
