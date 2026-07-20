"""Walk-forward LightGBM trainer for probabilistic ranking."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.dataset import ERA_BOUNDS
from research.leakage import walk_forward_splits
from research.paths import ensure_research_store_layout, models_dir

LOG = logging.getLogger(__name__)

DEFAULT_TARGET = "ret_40d_fwd"
DEFAULT_SEED = 42


@dataclass
class FoldResult:
    fold_id: str
    test_era: str
    n_train: int
    n_valid: int
    n_test: int
    metrics: dict[str, float]


def _require_lightgbm():
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise ImportError(
            "lightgbm is required for Phase C training. Install with: pip install lightgbm"
        ) from exc
    return lgb


def impute_matrix(
    df: pd.DataFrame,
    feature_cols: list[str],
    *,
    medians: dict[str, float] | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Median-impute features; fit medians on provided frame when not given."""
    fitted: dict[str, float] = dict(medians or {})
    cols = []
    for col in feature_cols:
        series = pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series([np.nan] * len(df))
        if col not in fitted:
            med = float(series.median()) if series.notna().any() else 0.0
            if med != med:
                med = 0.0
            fitted[col] = med
        cols.append(series.fillna(fitted[col]).to_numpy(dtype=np.float64))
    if not cols:
        return np.zeros((len(df), 0), dtype=np.float64), fitted
    return np.column_stack(cols), fitted


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    if len(y_true) == 0:
        return {"n": 0.0, "rmse": float("nan"), "mae": float("nan"), "ic": float("nan")}
    err = y_pred - y_true
    rmse = float(np.sqrt(np.mean(err**2)))
    mae = float(np.mean(np.abs(err)))
    if np.std(y_true) < 1e-12 or np.std(y_pred) < 1e-12:
        ic = 0.0
    else:
        ic = float(np.corrcoef(y_true, y_pred)[0, 1])
    # Decile lift: top decile mean return vs bottom
    order = np.argsort(y_pred)
    n = len(order)
    top = y_true[order[int(n * 0.9) :]] if n >= 10 else y_true
    bot = y_true[order[: max(1, int(n * 0.1))]] if n >= 10 else y_true
    return {
        "n": float(n),
        "rmse": rmse,
        "mae": mae,
        "ic": ic,
        "top_decile_mean_ret": float(np.mean(top)),
        "bottom_decile_mean_ret": float(np.mean(bot)),
        "decile_spread": float(np.mean(top) - np.mean(bot)),
    }


def _model_id(dataset_id: str, target: str, seed: int, fold_count: int) -> str:
    raw = f"{dataset_id}|{target}|{seed}|{fold_count}"
    return f"lgbm_{target}_{hashlib.sha256(raw.encode()).hexdigest()[:10]}"


def _sample_weights(df: pd.DataFrame, era_weights: dict[str, float] | None) -> np.ndarray | None:
    if not era_weights:
        return None
    if "era" not in df.columns:
        eras = df["asof_date"].map(
            lambda d: next(
                (
                    k
                    for k, (s, e) in ERA_BOUNDS.items()
                    if pd.Timestamp(d) >= pd.Timestamp(s)
                    and (e is None or pd.Timestamp(d) <= pd.Timestamp(e))
                ),
                "unknown",
            )
        )
    else:
        eras = df["era"].astype(str)
    return eras.map(lambda e: float(era_weights.get(str(e), 1.0))).fillna(1.0).to_numpy(dtype=np.float64)


def train_prob_rank_model(
    ds: pd.DataFrame,
    feature_cols: list[str],
    *,
    target_col: str = DEFAULT_TARGET,
    skill_dir: Path | None = None,
    seed: int = DEFAULT_SEED,
    num_boost_round: int = 200,
    early_stopping_rounds: int = 30,
    purge_days: int = 40,
    dataset_id: str | None = None,
    write: bool = True,
    era_weights: dict[str, float] | None = None,
    holdout_era: str | None = "recent_current",
) -> dict[str, Any]:
    """
    Expanding walk-forward LightGBM regressor.

    Fits a final model on all but the locked holdout era (default
    ``recent_current``) after fold evaluation, using train medians from that
    window. Pass ``holdout_era=None`` (or empty) to include every era in the
    final fit (still uses an 80/20 time split for early stopping).
    Optional ``era_weights`` up-weights rows (e.g. bear_rates) during fitting.
    """
    lgb = _require_lightgbm()
    work = ds.copy()
    work = work.dropna(subset=[target_col])
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    work = work.sort_values(["asof_date", "ticker"]).reset_index(drop=True)
    if "era" not in work.columns:
        work["era"] = work["asof_date"].map(
            lambda d: next(
                (
                    k
                    for k, (s, e) in ERA_BOUNDS.items()
                    if d >= pd.Timestamp(s) and (e is None or d <= pd.Timestamp(e))
                ),
                "unknown",
            )
        )

    folds = walk_forward_splits(work, ERA_BOUNDS, purge_days=purge_days)
    fold_results: list[dict[str, Any]] = []
    oof_pred = np.full(len(work), np.nan, dtype=np.float64)

    params = {
        "objective": "regression",
        "metric": "rmse",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "min_data_in_leaf": 20,
        "verbosity": -1,
        "seed": seed,
    }

    for fold in folds:
        tr = work.loc[fold["train_idx"]]
        va = work.loc[fold["valid_idx"]]
        te = work.loc[fold["test_idx"]]
        if tr.empty or te.empty:
            continue
        x_tr, medians = impute_matrix(tr, feature_cols)
        y_tr = pd.to_numeric(tr[target_col], errors="coerce").to_numpy(dtype=np.float64)
        w_tr = _sample_weights(tr, era_weights)
        x_va, _ = impute_matrix(va, feature_cols, medians=medians)
        y_va = pd.to_numeric(va[target_col], errors="coerce").to_numpy(dtype=np.float64)
        x_te, _ = impute_matrix(te, feature_cols, medians=medians)
        y_te = pd.to_numeric(te[target_col], errors="coerce").to_numpy(dtype=np.float64)

        dtrain = lgb.Dataset(
            x_tr, label=y_tr, weight=w_tr, feature_name=feature_cols, free_raw_data=False
        )
        valid_sets = [dtrain]
        valid_names = ["train"]
        callbacks = [lgb.log_evaluation(period=0)]
        if len(va) >= 10:
            dvalid = lgb.Dataset(x_va, label=y_va, feature_name=feature_cols, free_raw_data=False)
            valid_sets.append(dvalid)
            valid_names.append("valid")
            callbacks.append(lgb.early_stopping(early_stopping_rounds, verbose=False))

        booster = lgb.train(
            params,
            dtrain,
            num_boost_round=num_boost_round,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=callbacks,
        )
        pred = booster.predict(x_te, num_iteration=booster.best_iteration)
        oof_pred[te.index.to_numpy()] = pred
        metrics = regression_metrics(y_te, np.asarray(pred, dtype=np.float64))
        fold_results.append(
            {
                "fold_id": fold["fold_id"],
                "test_era": fold["test_era"],
                "n_train": int(len(tr)),
                "n_valid": int(len(va)),
                "n_test": int(len(te)),
                "best_iteration": int(booster.best_iteration or num_boost_round),
                "metrics": metrics,
            }
        )

    # Final model: optionally exclude holdout era, then validate on last 20%
    holdout = (str(holdout_era).strip() if holdout_era else "") or None
    work["era"] = work["asof_date"].map(
        lambda d: next((k for k, (s, e) in ERA_BOUNDS.items() if d >= pd.Timestamp(s) and (e is None or d <= pd.Timestamp(e))), None)
    )
    if holdout:
        train_final = work[work["era"] != holdout].copy()
    else:
        train_final = work.copy()
    if train_final.empty:
        train_final = work.copy()
    train_final = train_final.sort_values("asof_date")
    split_i = max(1, int(len(train_final) * 0.8))
    tr_f = train_final.iloc[:split_i]
    va_f = train_final.iloc[split_i:]
    x_tr, medians = impute_matrix(tr_f, feature_cols)
    y_tr = pd.to_numeric(tr_f[target_col], errors="coerce").to_numpy(dtype=np.float64)
    w_tr = _sample_weights(tr_f, era_weights)
    dtrain = lgb.Dataset(
        x_tr, label=y_tr, weight=w_tr, feature_name=feature_cols, free_raw_data=False
    )
    callbacks = [lgb.log_evaluation(period=0)]
    valid_sets = [dtrain]
    if len(va_f) >= 10:
        x_va, _ = impute_matrix(va_f, feature_cols, medians=medians)
        y_va = pd.to_numeric(va_f[target_col], errors="coerce").to_numpy(dtype=np.float64)
        dvalid = lgb.Dataset(x_va, label=y_va, feature_name=feature_cols, free_raw_data=False)
        valid_sets.append(dvalid)
        callbacks.append(lgb.early_stopping(early_stopping_rounds, verbose=False))
    final_booster = lgb.train(
        params,
        dtrain,
        num_boost_round=num_boost_round,
        valid_sets=valid_sets,
        callbacks=callbacks,
    )

    # Score→PF proxy calibration on OOF predictions
    calib = _score_pf_bins(work, oof_pred, target_col=target_col)

    ds_id = dataset_id or str(work.get("dataset_id", pd.Series(["adhoc"])).iloc[0])
    mid = _model_id(ds_id, target_col, seed, len(fold_results))
    importance = {
        feature_cols[i]: float(v)
        for i, v in enumerate(final_booster.feature_importance(importance_type="gain"))
    }

    artifact: dict[str, Any] = {
        "model_id": mid,
        "model_family": "lightgbm",
        "target_col": target_col,
        "dataset_id": ds_id,
        "feature_columns": feature_cols,
        "feature_medians": medians,
        "params": params,
        "seed": seed,
        "best_iteration": int(final_booster.best_iteration or num_boost_round),
        "walk_forward": {"folds": fold_results, "purge_days": purge_days},
        "feature_importance_gain": importance,
        "score_pf_calibration": calib,
        "era_weights": dict(era_weights or {}),
        "holdout_era": holdout,
    }

    if write:
        ensure_research_store_layout(skill_dir)
        out_dir = models_dir(skill_dir) / mid
        out_dir.mkdir(parents=True, exist_ok=True)
        model_path = out_dir / "model.txt"
        final_booster.save_model(str(model_path))
        artifact["model_path"] = str(model_path)
        (out_dir / "artifact.json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        # OOF predictions sidecar
        pred_df = work[["asof_date", "ticker"]].copy()
        pred_df["asof_date"] = pred_df["asof_date"].dt.strftime("%Y-%m-%d")
        pred_df["oof_pred"] = oof_pred
        pred_df.to_parquet(out_dir / "oof_predictions.parquet", index=False)
        LOG.info("Wrote model artifact %s", out_dir)

    artifact["_booster"] = final_booster
    artifact["_oof_pred"] = oof_pred
    return artifact


def _score_pf_bins(df: pd.DataFrame, preds: np.ndarray, *, target_col: str, n_bins: int = 10) -> list[dict[str, Any]]:
    mask = np.isfinite(preds)
    if "net_return" in df.columns:
        rets = pd.to_numeric(df["net_return"], errors="coerce").to_numpy(dtype=np.float64)
        use_ret = np.isfinite(rets)
    else:
        rets = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=np.float64)
        use_ret = np.isfinite(rets)
    ok = mask & use_ret
    if ok.sum() < n_bins * 3:
        return []
    p = preds[ok]
    r = rets[ok]
    qs = np.quantile(p, np.linspace(0, 1, n_bins + 1))
    bins: list[dict[str, Any]] = []
    for i in range(n_bins):
        lo, hi = float(qs[i]), float(qs[i + 1])
        if i == n_bins - 1:
            sel = (p >= lo) & (p <= hi)
        else:
            sel = (p >= lo) & (p < hi)
        rr = r[sel]
        if len(rr) == 0:
            continue
        wins = float(rr[rr > 0].sum())
        losses = float(-rr[rr <= 0].sum())
        pf = (wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)
        bins.append(
            {
                "bin": i,
                "score_lo": lo,
                "score_hi": hi,
                "n": int(len(rr)),
                "mean_ret": float(np.mean(rr)),
                "pf": float(pf),
            }
        )
    return bins


def load_model_artifact(model_dir: Path) -> dict[str, Any]:
    lgb = _require_lightgbm()
    artifact = json.loads((model_dir / "artifact.json").read_text(encoding="utf-8"))
    booster = lgb.Booster(model_file=str(model_dir / "model.txt"))
    artifact["_booster"] = booster
    return artifact
