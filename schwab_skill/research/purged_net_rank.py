"""Purged rank model on realized net return.

The label is the trade's net return after the stop and trail. Raw forward
returns and unpurged model outputs are refused as inputs. Scores are
out-of-sample under combinatorial purged splits with an embargo. This module
does not write a scanner artifact and does not change PROB_RANK_MODE.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.labels import LABEL_COLUMNS
from research.leakage import assert_no_label_columns_in_features
from research.purged_cv import assert_train_labels_respect_embargo, combinatorial_purged_splits
from research.train import impute_matrix

TARGET = "net_return"
PF_MEAN_FLOOR = 1.20
WORST_FLOOR = 1.00
MIN_LIFT_VS_INV_PTS52W = 0.03

# In-sample on four of five eras when trained holding out only recent_current.
UNPURGED_MODEL_COLUMNS = frozenset(
    {
        "expected_return_40d",
        "expected_downside_40d",
        "expected_pf_proxy",
        "ml_expected_return_norm",
    }
)
IDENTIFIER_COLUMNS = frozenset(
    {
        "ticker",
        "era",
        "asof_date",
        "entry_date",
        "exit_date",
        "exit_reason",
        "hold_days",
        "dataset_id",
        "fold",
        "split",
    }
)


def assert_net_return_target(target: str) -> None:
    """Ranking studies fit on raw forward returns do not transfer to this book."""
    if target != TARGET:
        raise ValueError(
            f"purged rank target must be {TARGET}, got {target!r}. "
            "ret_*_fwd is a price path, not the stop-and-trail outcome."
        )


def legal_feature_columns(columns: list[str]) -> tuple[list[str], list[str]]:
    """Return (kept, rejected). Labels and unpurged model outputs stay out."""
    label_set = set(LABEL_COLUMNS)
    kept: list[str] = []
    rejected: list[str] = []
    for col in columns:
        base = col
        for suffix in ("_panel", "_chunk", "_x", "_y"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        if col in IDENTIFIER_COLUMNS or col == TARGET:
            rejected.append(f"{col} (identifier or target)")
            continue
        if col in label_set or base in label_set or "fwd" in col or col.startswith(("y_up", "y_win", "drawdown_", "barrier_")):
            rejected.append(f"{col} (label)")
            continue
        if col in UNPURGED_MODEL_COLUMNS or base in UNPURGED_MODEL_COLUMNS:
            rejected.append(f"{col} (unpurged model output)")
            continue
        kept.append(col)
    errors = assert_no_label_columns_in_features(kept)
    if errors:
        raise RuntimeError(f"leakage guard tripped: {errors}")
    return kept, rejected


def _ridge_coef(x: np.ndarray, y: np.ndarray, *, alpha: float) -> np.ndarray:
    if x.size == 0 or x.shape[0] == 0:
        return np.zeros(x.shape[1] if x.ndim == 2 else 0, dtype=np.float64)
    xtx = x.T @ x
    xtx.flat[:: x.shape[1] + 1] += float(alpha)
    xty = x.T @ y
    try:
        return np.linalg.solve(xtx, xty)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(xtx, xty, rcond=None)[0]


def _prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "asof_date" not in work.columns:
        if "entry_date" not in work.columns:
            raise ValueError("purged rank needs asof_date or entry_date")
        work["asof_date"] = work["entry_date"]
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    if TARGET not in work.columns:
        raise ValueError(f"purged rank needs a {TARGET} column")
    work[TARGET] = pd.to_numeric(work[TARGET], errors="coerce")
    work = work.dropna(subset=[TARGET, "asof_date"]).reset_index(drop=True)
    if "hold_days" not in work.columns:
        work["hold_days"] = 40
    return work


def fit_purged_oos_scores(
    df: pd.DataFrame,
    feature_cols: list[str],
    *,
    target: str = TARGET,
    embargo_days: int = 40,
    n_groups: int = 6,
    n_test_groups: int = 2,
    alpha: float = 1.0,
) -> dict[str, Any]:
    """Fit a ridge on each purged train fold and average overlapping test scores."""
    assert_net_return_target(target)
    kept, rejected = legal_feature_columns(list(feature_cols))
    if not kept:
        raise ValueError("no legal features left after the leakage screen")
    work = _prepare_frame(df)
    folds = combinatorial_purged_splits(
        work,
        n_groups=n_groups,
        n_test_groups=n_test_groups,
        embargo_days=embargo_days,
        holding_col="hold_days",
        default_holding_days=40,
        min_train_rows=20,
    )
    if not folds:
        raise ValueError("purged splits produced no folds")
    for fold in folds:
        embargo_errors = assert_train_labels_respect_embargo(work, fold, holding_col="hold_days")
        if embargo_errors:
            raise RuntimeError(f"embargo violated on {fold['fold_id']}: {embargo_errors[:3]}")

    score_sum = np.zeros(len(work), dtype=np.float64)
    score_n = np.zeros(len(work), dtype=np.int32)
    fold_rows: list[dict[str, Any]] = []
    y = work[TARGET].to_numpy(dtype=np.float64)
    for fold in folds:
        train_idx = list(fold["train_idx"]) + list(fold["valid_idx"])
        test_idx = list(fold["test_idx"])
        train = work.iloc[train_idx]
        x_train, medians = impute_matrix(train, kept)
        mu = x_train.mean(axis=0)
        sd = x_train.std(axis=0)
        sd = np.where(sd < 1e-8, 1.0, sd)
        coef = _ridge_coef((x_train - mu) / sd, y[train_idx], alpha=alpha)
        x_test, _ = impute_matrix(work.iloc[test_idx], kept, medians=medians)
        pred = ((x_test - mu) / sd) @ coef
        for local_i, row_i in enumerate(test_idx):
            score_sum[row_i] += float(pred[local_i])
            score_n[row_i] += 1
        fold_rows.append(
            {
                "fold_id": fold["fold_id"],
                "test_start": fold["test_start"],
                "test_end": fold["test_end"],
                "n_train": len(train_idx),
                "n_test": len(test_idx),
                "n_purged": fold["n_purged"],
                "n_embargoed": fold["n_embargoed"],
            }
        )
    scored = work.copy()
    scored["purged_net_score"] = np.where(score_n > 0, score_sum / np.maximum(score_n, 1), np.nan)
    return {
        "target": TARGET,
        "features": kept,
        "rejected_features": rejected,
        "n_rows": int(len(work)),
        "n_scored": int((score_n > 0).sum()),
        "folds": fold_rows,
        "scored": scored,
        "live_modes_unchanged": True,
    }


def purged_rank_verdict(model_arm: dict[str, Any], inv_pts_arm: dict[str, Any]) -> str:
    """Beat inverted pts_52w by 0.03 PF mean and clear both floors. Still not live."""
    if not model_arm.get("clears_floors"):
        return "do_not_promote_purged_rank"
    model_pf = model_arm.get("pf_mean")
    control_pf = inv_pts_arm.get("pf_mean")
    if model_pf is None or control_pf is None:
        return "do_not_promote_purged_rank"
    if float(model_pf) - float(control_pf) < MIN_LIFT_VS_INV_PTS52W:
        return "do_not_promote_purged_rank"
    return "shadow_ready_purged_rank_not_promoted"
