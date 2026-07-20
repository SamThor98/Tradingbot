"""Regime-aware score calibration for prob-rank selection."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.regime_context import chop_mask, risk_off_mask


def _zscore(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    std = float(s.std(skipna=True) or 0.0)
    if std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean(skipna=True)) / std


def _zscore_by_group(series: pd.Series, group: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    g = group.astype(str)
    mu = s.groupby(g).transform("mean")
    sig = s.groupby(g).transform("std").replace(0.0, np.nan)
    return ((s - mu) / sig).fillna(0.0)


def apply_regime_aware_scores(
    df: pd.DataFrame,
    *,
    model_col: str = "expected_return_40d",
    control_col: str = "rank_score_v2",
    out_col: str = "expected_return_40d_calibrated",
    risk_off_blend: float = 0.65,
    risk_off_threshold: float = 0.35,
    group_col: str | None = "era",
) -> pd.DataFrame:
    """
    Blend model score toward control rank in risk-off regimes.

    In risk-on: use within-group z-score of the model.
    In risk-off: ``(1-w)*z_model + w*z_control`` with ``w=risk_off_blend``.

    Rationale: on ``bear_rates`` the raw LightGBM rank IC was negative while
    ``rank_score_v2`` stayed mildly positive — blend restores ranking direction
    without hard filters.
    """
    out = df.copy()
    if model_col not in out.columns:
        raise ValueError(f"missing model_col {model_col}")
    if group_col and group_col in out.columns:
        z_model = _zscore_by_group(out[model_col], out[group_col])
        z_ctrl = (
            _zscore_by_group(out[control_col], out[group_col])
            if control_col in out.columns
            else z_model
        )
    else:
        z_model = _zscore(out[model_col])
        z_ctrl = _zscore(out[control_col]) if control_col in out.columns else z_model

    mask = risk_off_mask(out, threshold=risk_off_threshold)
    w = float(np.clip(risk_off_blend, 0.0, 1.0))
    calibrated = z_model.copy()
    if mask.any() and control_col in out.columns and out[control_col].notna().any():
        calibrated.loc[mask] = (1.0 - w) * z_model.loc[mask] + w * z_ctrl.loc[mask]
    out[out_col] = calibrated
    out["regime_risk_off_flag"] = mask.astype(int)
    out["regime_blend_w"] = np.where(mask, w, 0.0)
    return out


def fit_risk_off_blend(
    df: pd.DataFrame,
    *,
    model_col: str = "expected_return_40d",
    control_col: str = "rank_score_v2",
    label_col: str = "net_return",
    candidates: tuple[float, ...] = (0.0, 0.35, 0.5, 0.65, 0.8, 1.0),
) -> dict[str, Any]:
    """
    Pick blend weight maximizing Spearman IC on risk-off rows only.

    Intended for research sweeps on frozen dual-run samples (not live).
    """
    work = df.dropna(subset=[model_col, label_col]).copy()
    mask = risk_off_mask(work)
    subset = work.loc[mask]
    if len(subset) < 80 or control_col not in subset.columns:
        # Too thin to tune — prefer moderate blend, not full control takeover
        return {
            "best_blend": 0.5,
            "ic_by_blend": {},
            "n_risk_off": int(len(subset)),
            "note": "default_blend_thin_risk_off_sample",
        }

    ics: dict[str, float] = {}
    best_w = 0.65
    best_ic = -999.0
    for w in candidates:
        trial = apply_regime_aware_scores(
            subset, model_col=model_col, control_col=control_col, risk_off_blend=w
        )
        ic = float(
            trial["expected_return_40d_calibrated"].corr(trial[label_col], method="spearman")
        )
        if ic != ic:
            ic = -999.0
        ics[str(w)] = ic
        if ic > best_ic:
            best_ic = ic
            best_w = float(w)
    return {
        "best_blend": best_w,
        "best_ic": best_ic if best_ic > -900 else None,
        "ic_by_blend": ics,
        "n_risk_off": int(len(subset)),
    }


def apply_chop_aware_scores(
    df: pd.DataFrame,
    *,
    model_col: str = "expected_return_40d",
    compression_col: str = "compression_score",
    breakout_col: str = "breakout_velocity",
    out_col: str = "expected_return_40d_chop_cal",
    chop_blend: float = 0.55,
    chop_threshold: float = 0.55,
    breakout_penalty: float = 0.35,
    group_col: str | None = "era",
) -> pd.DataFrame:
    """
    In chop regimes, blend model toward compression and penalize hot breakouts.

    Dual-run evidence: in ``volatility_chop``, ``compression_score`` IC was
    positive while ``breakout_velocity`` / ``ret_20d_prev`` were negative — the
    raw model preferred failed breakouts.
    """
    out = df.copy()
    if model_col not in out.columns:
        raise ValueError(f"missing model_col {model_col}")
    if group_col and group_col in out.columns:
        z_model = _zscore_by_group(out[model_col], out[group_col])
        z_comp = (
            _zscore_by_group(out[compression_col], out[group_col])
            if compression_col in out.columns
            else z_model
        )
        z_brk = (
            _zscore_by_group(out[breakout_col], out[group_col])
            if breakout_col in out.columns
            else pd.Series(0.0, index=out.index)
        )
    else:
        z_model = _zscore(out[model_col])
        z_comp = _zscore(out[compression_col]) if compression_col in out.columns else z_model
        z_brk = _zscore(out[breakout_col]) if breakout_col in out.columns else pd.Series(0.0, index=out.index)

    mask = chop_mask(out, threshold=chop_threshold)
    w = float(np.clip(chop_blend, 0.0, 1.0))
    pen = float(np.clip(breakout_penalty, 0.0, 1.0))
    calibrated = z_model.copy()
    if mask.any():
        blended = (1.0 - w) * z_model.loc[mask] + w * z_comp.loc[mask]
        calibrated.loc[mask] = blended - pen * z_brk.loc[mask]
    out[out_col] = calibrated
    out["regime_chop_flag"] = mask.astype(int)
    out["chop_blend_w"] = np.where(mask, w, 0.0)
    return out


def add_chop_helper_features(df: pd.DataFrame) -> pd.DataFrame:
    """Derived ticker helpers (no rematerialize) for chop-era learning."""
    out = df.copy()
    ret = pd.to_numeric(out.get("ret_20d_prev"), errors="coerce")
    atr = pd.to_numeric(out.get("atr_pct"), errors="coerce")
    brk = pd.to_numeric(out.get("breakout_velocity"), errors="coerce")
    comp = pd.to_numeric(out.get("compression_score"), errors="coerce")
    # Efficiency proxy: move vs noise (ATR path)
    out["trend_efficiency_20d"] = (ret.abs() / (atr.replace(0.0, np.nan) * np.sqrt(20.0) + 1e-6)).clip(
        0.0, 5.0
    )
    # Hot breakout without compression — historically toxic in chop
    out["breakout_hot_raw"] = brk.fillna(0.0) * (1.0 - comp.fillna(0.5)).clip(0.0, 1.0)
    return out
