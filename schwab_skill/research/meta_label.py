"""Meta-label P(win | Stage-2 setup). SHADOW dataset only — never LIVE."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)

META_TARGET = "y_win_barrier"


def attach_meta_target(df: pd.DataFrame) -> pd.DataFrame:
    """Secondary label: 1 if barrier_label == profit (win given the setup)."""
    work = df.copy()
    if "barrier_label" in work.columns:
        work[META_TARGET] = (pd.to_numeric(work["barrier_label"], errors="coerce") > 0).astype(int)
    elif "barrier_hit" in work.columns:
        work[META_TARGET] = (work["barrier_hit"].astype(str) == "profit").astype(int)
    else:
        work[META_TARGET] = pd.NA
    return work


def train_meta_label_model(
    ds: pd.DataFrame,
    feature_cols: list[str],
    *,
    skill_dir: Path | None = None,
    seed: int = 42,
    write: bool = True,
    splits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Binary LightGBM on SHADOW rows only. Does not change PROB_RANK_MODE."""
    from config import get_meta_label_mode
    from research.explain import shap_summary
    from research.paths import ensure_research_store_layout, models_dir
    from research.purged_cv import combinatorial_purged_splits
    from research.train import impute_matrix

    mode = get_meta_label_mode(skill_dir)
    if mode != "shadow":
        LOG.info("META_LABEL_MODE=%s — skip meta-label train (shadow only)", mode)
        return {"skipped": True, "reason": f"meta_label_mode={mode}"}

    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise ImportError("lightgbm is required for meta-label training") from exc

    work = attach_meta_target(ds)
    work = work.dropna(subset=[META_TARGET])
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    work = work.sort_values(["asof_date", "ticker"]).reset_index(drop=True)
    folds = splits if splits is not None else combinatorial_purged_splits(work)
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_data_in_leaf": 20,
        "verbosity": -1,
        "seed": seed,
    }
    x_all, medians = impute_matrix(work, feature_cols)
    y_all = pd.to_numeric(work[META_TARGET], errors="coerce").to_numpy(dtype=np.float64)
    dtrain = lgb.Dataset(x_all, label=y_all, feature_name=feature_cols, free_raw_data=False)
    booster = lgb.train(params, dtrain, num_boost_round=80, callbacks=[lgb.log_evaluation(period=0)])
    artifact: dict[str, Any] = {
        "model_id": "lgbm_meta_y_win_barrier_shadow",
        "model_family": "lightgbm_binary",
        "target_col": META_TARGET,
        "primary_signal": "stage2_candidate",
        "mode": "shadow",
        "feature_columns": feature_cols,
        "feature_medians": medians,
        "n_folds": len(folds),
        "seed": seed,
    }
    artifact["_booster"] = booster
    artifact["shap_summary"] = shap_summary(artifact, work)
    if write:
        import json

        ensure_research_store_layout(skill_dir)
        out_dir = models_dir(skill_dir) / artifact["model_id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        booster.save_model(str(out_dir / "model.txt"))
        slim = {k: v for k, v in artifact.items() if not str(k).startswith("_")}
        (out_dir / "artifact.json").write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
        LOG.info("Wrote meta-label SHADOW artifact %s", out_dir)
        artifact["model_path"] = str(out_dir / "model.txt")
    return artifact
