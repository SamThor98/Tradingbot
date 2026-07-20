"""Feature importance and SHAP explainability for prob-rank models."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from research.train import impute_matrix

LOG = logging.getLogger(__name__)


def global_importance(artifact: dict[str, Any], top_k: int = 30) -> list[dict[str, Any]]:
    imp = artifact.get("feature_importance_gain") or {}
    items = sorted(imp.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [{"feature": k, "gain": float(v)} for k, v in items]


def shap_summary(
    artifact: dict[str, Any],
    df: pd.DataFrame,
    *,
    max_rows: int = 500,
    top_k: int = 20,
) -> dict[str, Any]:
    """
    Compute mean |SHAP| when shap is installed; else fall back to gain importance.
    """
    feature_cols: list[str] = list(artifact.get("feature_columns") or [])
    medians = artifact.get("feature_medians") or {}
    sample = df.sample(n=min(max_rows, len(df)), random_state=int(artifact.get("seed") or 42)) if len(df) else df
    x, _ = impute_matrix(sample, feature_cols, medians=medians)
    booster = artifact.get("_booster")
    if booster is None:
        return {"method": "none", "error": "booster not loaded", "mean_abs_shap": []}

    try:
        import shap

        explainer = shap.TreeExplainer(booster)
        values = explainer.shap_values(x)
        if isinstance(values, list):
            values = values[0]
        arr = np.asarray(values, dtype=np.float64)
        mean_abs = np.mean(np.abs(arr), axis=0)
        ranked = sorted(
            [{"feature": feature_cols[i], "mean_abs_shap": float(mean_abs[i])} for i in range(len(feature_cols))],
            key=lambda d: d["mean_abs_shap"],
            reverse=True,
        )[:top_k]
        return {"method": "shap_tree", "n_rows": int(len(sample)), "mean_abs_shap": ranked}
    except Exception as exc:
        LOG.warning("SHAP unavailable or failed (%s); using gain importance", exc)
        return {
            "method": "gain_fallback",
            "error": str(exc),
            "mean_abs_shap": [
                {"feature": d["feature"], "mean_abs_shap": d["gain"]} for d in global_importance(artifact, top_k=top_k)
            ],
        }


def local_shap_top(
    artifact: dict[str, Any],
    feature_row: dict[str, Any],
    *,
    top_k: int = 5,
) -> dict[str, list[dict[str, float | str]]]:
    """Top positive/negative SHAP contributors for one row."""
    feature_cols: list[str] = list(artifact.get("feature_columns") or [])
    medians = artifact.get("feature_medians") or {}
    frame = pd.DataFrame([feature_row])
    x, _ = impute_matrix(frame, feature_cols, medians=medians)
    booster = artifact.get("_booster")
    if booster is None:
        return {"shap_top_positive": [], "shap_top_negative": []}
    try:
        import shap

        explainer = shap.TreeExplainer(booster)
        values = explainer.shap_values(x)
        if isinstance(values, list):
            values = values[0]
        vec = np.asarray(values, dtype=np.float64).reshape(-1)
        pairs = [(feature_cols[i], float(vec[i])) for i in range(len(feature_cols))]
        pos = sorted([p for p in pairs if p[1] > 0], key=lambda t: t[1], reverse=True)[:top_k]
        neg = sorted([p for p in pairs if p[1] < 0], key=lambda t: t[1])[:top_k]
        return {
            "shap_top_positive": [{"feature": f, "value": v} for f, v in pos],
            "shap_top_negative": [{"feature": f, "value": v} for f, v in neg],
        }
    except Exception:
        # Approximate with gain * signed z-ish value
        imp = artifact.get("feature_importance_gain") or {}
        scored = []
        for col in feature_cols:
            raw = feature_row.get(col)
            try:
                val = float(raw) if raw is not None else medians.get(col, 0.0)
            except (TypeError, ValueError):
                val = float(medians.get(col, 0.0))
            med = float(medians.get(col, 0.0))
            signed = (val - med) * float(imp.get(col, 0.0))
            scored.append((col, signed))
        pos = sorted([p for p in scored if p[1] > 0], key=lambda t: t[1], reverse=True)[:top_k]
        neg = sorted([p for p in scored if p[1] < 0], key=lambda t: t[1])[:top_k]
        return {
            "shap_top_positive": [{"feature": f, "value": v} for f, v in pos],
            "shap_top_negative": [{"feature": f, "value": v} for f, v in neg],
        }
