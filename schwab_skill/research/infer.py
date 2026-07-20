"""Inference helpers: score candidates and attach prob_rank blocks."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.explain import local_shap_top
from research.train import impute_matrix


def _lookup_pf_proxy(score: float, bins: list[dict[str, Any]]) -> float | None:
    if not bins:
        return None
    for b in bins:
        lo = float(b.get("score_lo", 0.0))
        hi = float(b.get("score_hi", 0.0))
        if lo <= score <= hi or (score >= lo and b is bins[-1]):
            return float(b.get("pf")) if b.get("pf") is not None else None
    # nearest bin center
    centers = [0.5 * (float(b["score_lo"]) + float(b["score_hi"])) for b in bins]
    idx = int(np.argmin([abs(score - c) for c in centers]))
    return float(bins[idx].get("pf"))


def predict_frame(artifact: dict[str, Any], df: pd.DataFrame) -> pd.DataFrame:
    """Add expected_return_40d and related columns to a feature frame."""
    booster = artifact.get("_booster")
    if booster is None:
        raise ValueError("artifact missing _booster; load via load_model_artifact")
    feature_cols = list(artifact.get("feature_columns") or [])
    medians = artifact.get("feature_medians") or {}
    x, _ = impute_matrix(df, feature_cols, medians=medians)
    pred = np.asarray(
        booster.predict(x, num_iteration=artifact.get("best_iteration")),
        dtype=np.float64,
    )
    out = df.copy()
    out["expected_return_40d"] = pred
    # Downside proxy: predicted return minus a simple uncertainty from fold RMSE mean
    fold_rmses = [
        float(f.get("metrics", {}).get("rmse") or 0.0)
        for f in (artifact.get("walk_forward") or {}).get("folds") or []
    ]
    rmse = float(np.mean(fold_rmses)) if fold_rmses else float(np.std(pred) or 0.05)
    out["expected_downside_40d"] = np.maximum(0.0, rmse - pred)
    # Confidence: inverse of relative uncertainty, clipped
    out["confidence"] = 1.0 / (1.0 + (rmse / (np.abs(pred) + 1e-3)))
    calib = artifact.get("score_pf_calibration") or []
    out["expected_pf_proxy"] = [_lookup_pf_proxy(float(s), calib) for s in pred]
    return out


def attach_prob_rank_block(
    artifact: dict[str, Any],
    feature_row: dict[str, Any],
    *,
    cross_section_rank: int | None = None,
    cross_section_n: int | None = None,
    include_shap: bool = True,
) -> dict[str, Any]:
    """Build the ``prob_rank`` payload for one candidate."""
    scored = predict_frame(artifact, pd.DataFrame([feature_row])).iloc[0]
    block: dict[str, Any] = {
        "model_id": artifact.get("model_id"),
        "schema_version": feature_row.get("feature_schema_version", 1),
        "expected_return_40d": float(scored["expected_return_40d"]),
        "expected_downside_40d": float(scored["expected_downside_40d"]),
        "confidence": float(scored["confidence"]),
        "expected_pf_proxy": scored["expected_pf_proxy"],
        "cross_section_rank": cross_section_rank,
        "cross_section_n": cross_section_n,
    }
    if include_shap:
        shap_local = local_shap_top(artifact, feature_row)
        block.update(shap_local)
    return block


# Joined onto trades for post-score chop/regime calibration (see research.calibrate).
_SCORE_ATTACH_EXTRA_DEFAULT: tuple[str, ...] = (
    "compression_score",
    "breakout_velocity",
    "ret_20d_prev",
    "atr_pct",
    "regime_risk_off",
    "regime_chop_score",
    "spy_above_sma200",
    "spy_trend_efficiency_20d",
    "spy_dist_sma200_pct",
    "trend_efficiency_20d",
    "breakout_hot_raw",
)


def attach_scores_to_trades(
    trades: pd.DataFrame,
    scored_features: pd.DataFrame,
    *,
    extra_cols: tuple[str, ...] | list[str] | None = None,
) -> pd.DataFrame:
    """
    Join model scores onto trade rows by (ticker, entry_date≈asof_date).

    ``scored_features`` must include ticker, asof_date, expected_return_40d.
    By default also joins calibration helpers (compression / SPY regime) when present.
    Pass ``extra_cols=()`` to keep score columns only.
    """
    tr = trades.copy()
    tr["ticker"] = tr["ticker"].astype(str).str.upper()
    tr["entry_iso"] = pd.to_datetime(tr["entry_date"]).dt.strftime("%Y-%m-%d")
    sc = scored_features.copy()
    sc["ticker"] = sc["ticker"].astype(str).str.upper()
    sc["asof_date"] = pd.to_datetime(sc["asof_date"]).dt.strftime("%Y-%m-%d")
    extras = _SCORE_ATTACH_EXTRA_DEFAULT if extra_cols is None else tuple(extra_cols)
    keep = [
        c
        for c in (
            "ticker",
            "asof_date",
            "expected_return_40d",
            "expected_downside_40d",
            "confidence",
            "expected_pf_proxy",
            *extras,
        )
        if c in sc.columns
    ]
    sc = sc[keep].drop_duplicates(subset=["ticker", "asof_date"], keep="last")
    merged = tr.merge(sc, left_on=["ticker", "entry_iso"], right_on=["ticker", "asof_date"], how="left")
    return merged
