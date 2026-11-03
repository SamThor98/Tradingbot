#!/usr/bin/env python3
"""Retrain lgbm_ret_40d_fwd_* control and a barrier-label challenger.

Writes to research_store/models/ with SHAP metadata.
Does not switch PROB_RANK_MODE off shadow.

Example:
  python scripts/retrain_barrier_label_challenger.py --dataset research_store/datasets/rank_....parquet
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
from research.explain import shap_summary  # noqa: E402
from research.meta_label import attach_meta_target, train_meta_label_model  # noqa: E402
from research.paths import models_dir  # noqa: E402
from research.purged_cv import combinatorial_purged_splits  # noqa: E402
from research.train import DEFAULT_TARGET, train_prob_rank_model  # noqa: E402

LOG = get_logger("retrain_barrier_label_challenger")


def _write_shap(artifact: dict, ds: pd.DataFrame, out_dir: Path) -> None:
    payload = shap_summary(artifact, ds)
    (out_dir / "shap_summary.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    artifact["shap_summary"] = payload
    slim = {k: v for k, v in artifact.items() if not str(k).startswith("_")}
    (out_dir / "artifact.json").write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--embargo-days", type=int, default=None)
    parser.add_argument("--skip-meta", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    setup_logging()

    from config import get_barrier_embargo_days, get_prob_rank_mode

    mode = get_prob_rank_mode(SKILL_DIR)
    if mode == "live":
        LOG.error("PROB_RANK_MODE=live — refuse to retrain; keep shadow")
        return 2
    LOG.info("PROB_RANK_MODE remains %s (not switched)", mode)

    ds_path = Path(args.dataset)
    if not ds_path.is_file():
        LOG.error("Dataset not found: %s", ds_path)
        return 2
    ds = pd.read_parquet(ds_path)
    feature_cols = resolve_feature_columns(ds)
    if not feature_cols:
        LOG.error("No feature columns resolved")
        return 2
    embargo = int(args.embargo_days or get_barrier_embargo_days(SKILL_DIR))
    splits = combinatorial_purged_splits(ds, embargo_days=embargo)
    dataset_id = str(ds["dataset_id"].iloc[0]) if "dataset_id" in ds.columns else ds_path.stem
    write = not args.dry_run

    if DEFAULT_TARGET not in ds.columns:
        LOG.error("Control target %s missing", DEFAULT_TARGET)
        return 2
    control = train_prob_rank_model(
        ds,
        feature_cols,
        target_col=DEFAULT_TARGET,
        skill_dir=SKILL_DIR,
        seed=args.seed,
        dataset_id=dataset_id,
        write=write,
        splits=splits or None,
        purge_days=embargo,
    )
    if write and control.get("model_id"):
        _write_shap(control, ds, models_dir(SKILL_DIR) / str(control["model_id"]))
        LOG.info("Control model %s", control.get("model_id"))

    barrier_target = "barrier_ret_net" if "barrier_ret_net" in ds.columns else None
    if barrier_target is None:
        LOG.info("No barrier_ret_net on dataset — skip barrier challenger")
    else:
        challenger = train_prob_rank_model(
            ds,
            feature_cols,
            target_col=barrier_target,
            skill_dir=SKILL_DIR,
            seed=args.seed,
            dataset_id=f"{dataset_id}_barrier",
            write=write,
            splits=splits or None,
            purge_days=embargo,
        )
        if write and challenger.get("model_id"):
            _write_shap(challenger, ds, models_dir(SKILL_DIR) / str(challenger["model_id"]))
            LOG.info("Barrier challenger %s", challenger.get("model_id"))

    if not args.skip_meta and "barrier_label" in ds.columns:
        meta_ds = attach_meta_target(ds)
        try:
            train_meta_label_model(
                meta_ds,
                feature_cols,
                skill_dir=SKILL_DIR,
                seed=args.seed,
                write=write,
                splits=splits,
            )
        except Exception as exc:
            LOG.warning("Meta-label train skipped: %s", exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
