"""IC-driven composite quality score — merges rank-v2 predictive components with safety caps."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PTS_VOLUME_CAP = 20.0
PTS_MIROFISH_CAP = 15.0
# 40d IC on full-history audit: close_vs_sma200_pct ~ +0.07 vs pts_volume ~ +0.02.
TREND_PCT_CAP = 0.20
BREAKOUT_VOLUME_RATIO_CAP = 2.0
SKILL_DIR = Path(__file__).resolve().parent.parent


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


@dataclass
class CompositeQualityWeights:
    """Weights for composite_score (live sort key)."""

    direct_trend_weight: float = 0.70
    direct_volume_weight: float = 0.20
    direct_signal_weight: float = 0.05
    direct_mirofish_weight: float = 0.05
    use_direct_components: bool = True
    stack_blend_weight: float = 0.0
    edge_signal_weight: float = 1.0
    edge_pup_weight: float = 0.0
    composite_edge_weight: float = 0.0
    composite_reliability_weight: float = 0.0
    composite_execution_weight: float = 0.0
    exclude_52w: bool = True
    safety_caps_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def composite_quality_weights_from_config(skill_dir: Path | None = None) -> CompositeQualityWeights:
    from config import (
        get_score_composite_direct_mirofish_weight,
        get_score_composite_direct_signal_weight,
        get_score_composite_direct_trend_weight,
        get_score_composite_direct_volume_weight,
        get_score_composite_edge_weight,
        get_score_composite_execution_weight,
        get_score_composite_reliability_weight,
        get_score_composite_safety_caps_only,
        get_score_composite_stack_blend_weight,
        get_score_composite_use_direct_components,
        get_score_edge_exclude_52w,
        get_score_edge_pup_weight,
        get_score_edge_signal_weight,
    )

    sd = skill_dir or SKILL_DIR
    return CompositeQualityWeights(
        direct_trend_weight=float(get_score_composite_direct_trend_weight(sd)),
        direct_volume_weight=float(get_score_composite_direct_volume_weight(sd)),
        direct_signal_weight=float(get_score_composite_direct_signal_weight(sd)),
        direct_mirofish_weight=float(get_score_composite_direct_mirofish_weight(sd)),
        use_direct_components=bool(get_score_composite_use_direct_components(sd)),
        stack_blend_weight=float(get_score_composite_stack_blend_weight(sd)),
        edge_signal_weight=float(get_score_edge_signal_weight(sd)),
        edge_pup_weight=float(get_score_edge_pup_weight(sd)),
        composite_edge_weight=float(get_score_composite_edge_weight(sd)),
        composite_reliability_weight=float(get_score_composite_reliability_weight(sd)),
        composite_execution_weight=float(get_score_composite_execution_weight(sd)),
        exclude_52w=bool(get_score_edge_exclude_52w(sd)),
        safety_caps_only=bool(get_score_composite_safety_caps_only(sd)),
    )


def _pts_from_mapping(
    mapping: dict[str, Any],
    key: str,
    fallback_keys: tuple[str, ...] = (),
) -> float:
    raw = mapping.get(key)
    if raw is None:
        for alt in fallback_keys:
            if mapping.get(alt) is not None:
                raw = mapping.get(alt)
                break
    try:
        return float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0


def trend_norm_from_pct(close_vs_sma200_pct: float) -> float:
    """Map distance above 200 SMA (fraction) to 0–100 predictive trend score."""
    try:
        pct = max(0.0, float(close_vs_sma200_pct))
    except (TypeError, ValueError):
        pct = 0.0
    return _clamp((pct / TREND_PCT_CAP) * 100.0)


def trend_norm_from_price(*, price: float, sma_200: float) -> float:
    if sma_200 <= 0:
        return 0.0
    return trend_norm_from_pct((float(price) / float(sma_200)) - 1.0)


def breakout_volume_points(latest_volume: Any, avg_vol_50: Any) -> float:
    """Map breakout volume confirmation to the same 0-20 component scale."""

    try:
        latest = float(latest_volume or 0.0)
        avg = float(avg_vol_50 or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if latest <= 0.0 or avg <= 0.0:
        return 0.0
    ratio = latest / avg
    return _clamp(((ratio - 1.0) / (BREAKOUT_VOLUME_RATIO_CAP - 1.0)) * PTS_VOLUME_CAP, 0.0, PTS_VOLUME_CAP)


def resolve_rank_volume_points(
    pts_volume: Any,
    *,
    latest_volume: Any = None,
    avg_vol_50: Any = None,
) -> float:
    """Use VCP dry-up points or breakout confirmation, whichever is stronger."""

    try:
        dry_up_pts = float(pts_volume or 0.0)
    except (TypeError, ValueError):
        dry_up_pts = 0.0
    return max(_clamp(dry_up_pts, 0.0, PTS_VOLUME_CAP), breakout_volume_points(latest_volume, avg_vol_50))


def normalized_component_scores(
    *,
    signal_score: float,
    pts_52w: float,
    pts_volume: float,
    pts_mirofish: float,
    close_vs_sma200_pct: float = 0.0,
    exclude_52w: bool,
) -> tuple[float, float, float, float]:
    """Return (signal_norm, volume_norm, mirofish_norm, trend_norm) on 0–100 scale."""
    sig = float(signal_score)
    if exclude_52w:
        sig = max(0.0, sig - float(pts_52w))
    sig_norm = _clamp(sig)
    vol_norm = _clamp((float(pts_volume) / PTS_VOLUME_CAP) * 100.0)
    miro_norm = _clamp((float(pts_mirofish) / PTS_MIROFISH_CAP) * 100.0)
    trend_norm = trend_norm_from_pct(close_vs_sma200_pct)
    return sig_norm, vol_norm, miro_norm, trend_norm


def compute_predictive_core(
    *,
    signal_score: float,
    pts_52w: float,
    pts_volume: float,
    pts_mirofish: float,
    close_vs_sma200_pct: float = 0.0,
    weights: CompositeQualityWeights,
) -> float:
    sig_norm, vol_norm, miro_norm, trend_norm = normalized_component_scores(
        signal_score=signal_score,
        pts_52w=pts_52w,
        pts_volume=pts_volume,
        pts_mirofish=pts_mirofish,
        close_vs_sma200_pct=close_vs_sma200_pct,
        exclude_52w=weights.exclude_52w,
    )
    blend = (
        (weights.direct_trend_weight * trend_norm)
        + (weights.direct_volume_weight * vol_norm)
        + (weights.direct_signal_weight * sig_norm)
        + (weights.direct_mirofish_weight * miro_norm)
    )
    return _clamp(blend)


def apply_composite_risk_caps(
    composite: float,
    *,
    reliability_score: float | None,
    execution_score: float | None,
    sec_risk_tag: str | None,
    forensic_flags: list[str] | None,
    sec_risk_score: float | None = None,
) -> float:
    capped = float(composite)
    rel = float(reliability_score) if reliability_score is not None else 100.0
    exe = float(execution_score) if execution_score is not None else 100.0
    if rel < 40.0:
        capped = min(capped, 55.0)
    if exe < 45.0:
        capped = min(capped, 58.0)
    tag = str(sec_risk_tag or "unknown").lower()
    if tag == "unknown" and sec_risk_score is not None:
        try:
            risk = float(sec_risk_score)
            tag = "high" if risk >= 0.67 else ("medium" if risk >= 0.33 else "low")
        except (TypeError, ValueError):
            tag = "unknown"
    flags = list(forensic_flags or [])
    if tag == "high" or "beneish_manipulator" in flags or "altman_distress" in flags:
        capped = min(capped, 45.0)
    return round(_clamp(capped), 2)


def compute_edge_score(
    *,
    edge_signal: float,
    p_up_calibrated: float,
    weights: CompositeQualityWeights,
) -> float:
    return _clamp(
        (weights.edge_signal_weight * float(edge_signal))
        + (weights.edge_pup_weight * float(p_up_calibrated) * 100.0)
    )


def compute_composite_quality(
    *,
    signal_score: float,
    pts_52w: float,
    pts_volume: float,
    pts_mirofish: float,
    p_up_calibrated: float,
    reliability_score: float | None,
    execution_score: float | None,
    sec_risk_tag: str | None,
    forensic_flags: list[str] | None,
    sec_risk_score: float | None = None,
    close_vs_sma200_pct: float = 0.0,
    weights: CompositeQualityWeights | None = None,
) -> float:
    w = weights or composite_quality_weights_from_config()
    sig_for_edge, _, _, _ = normalized_component_scores(
        signal_score=signal_score,
        pts_52w=pts_52w,
        pts_volume=pts_volume,
        pts_mirofish=pts_mirofish,
        close_vs_sma200_pct=close_vs_sma200_pct,
        exclude_52w=w.exclude_52w,
    )
    edge = compute_edge_score(
        edge_signal=sig_for_edge,
        p_up_calibrated=p_up_calibrated,
        weights=w,
    )
    rel = float(reliability_score) if reliability_score is not None else 82.0
    exe = float(execution_score) if execution_score is not None else 100.0

    if w.use_direct_components:
        predictive = compute_predictive_core(
            signal_score=signal_score,
            pts_52w=pts_52w,
            pts_volume=pts_volume,
            pts_mirofish=pts_mirofish,
            close_vs_sma200_pct=close_vs_sma200_pct,
            weights=w,
        )
        if w.stack_blend_weight > 0 and not w.safety_caps_only:
            stack = (
                (edge * w.composite_edge_weight)
                + (rel * w.composite_reliability_weight)
                + (exe * w.composite_execution_weight)
            )
            base = ((1.0 - w.stack_blend_weight) * predictive) + (w.stack_blend_weight * stack)
        elif w.stack_blend_weight > 0:
            base = ((1.0 - w.stack_blend_weight) * predictive) + (w.stack_blend_weight * edge)
        else:
            base = predictive
    else:
        base = (
            (edge * w.composite_edge_weight)
            + (rel * w.composite_reliability_weight)
            + (exe * w.composite_execution_weight)
        )

    return apply_composite_risk_caps(
        base,
        reliability_score=reliability_score,
        execution_score=execution_score,
        sec_risk_tag=sec_risk_tag,
        forensic_flags=forensic_flags,
        sec_risk_score=sec_risk_score,
    )


def composite_quality_from_signal_row(
    signal_row: dict[str, Any],
    skill_dir: Path | None = None,
) -> float:
    comps = signal_row.get("score_components") if isinstance(signal_row.get("score_components"), dict) else {}
    flags = signal_row.get("forensic_flags")
    if not isinstance(flags, list):
        flags = []
    rel = signal_row.get("reliability_score")
    exe = signal_row.get("execution_score")
    try:
        rel_f = float(rel) if rel is not None else None
    except (TypeError, ValueError):
        rel_f = None
    try:
        exe_f = float(exe) if exe is not None else None
    except (TypeError, ValueError):
        exe_f = None
    p_up = signal_row.get("p_up_calibrated")
    try:
        p_up_f = float(p_up) if p_up is not None else 0.5
    except (TypeError, ValueError):
        p_up_f = 0.5
    sec_score = signal_row.get("sec_risk_score")
    try:
        sec_score_f = float(sec_score) if sec_score is not None else None
    except (TypeError, ValueError):
        sec_score_f = None

    price = _pts_from_mapping(signal_row, "price")
    sma_200 = _pts_from_mapping(signal_row, "sma_200")
    trend_pct_raw = signal_row.get("close_vs_sma200_pct")
    if trend_pct_raw is None and price > 0 and sma_200 > 0:
        trend_pct = (price / sma_200) - 1.0
    else:
        try:
            trend_pct = float(trend_pct_raw or 0.0)
        except (TypeError, ValueError):
            trend_pct = 0.0

    return compute_composite_quality(
        signal_score=_pts_from_mapping(signal_row, "signal_score"),
        pts_52w=_pts_from_mapping(comps, "pts_52w", ("pts_52w",)),
        pts_volume=_pts_from_mapping(comps, "pts_volume", ("pts_volume",)),
        pts_mirofish=_pts_from_mapping(comps, "pts_mirofish", ("pts_mirofish",)),
        p_up_calibrated=p_up_f,
        reliability_score=rel_f,
        execution_score=exe_f,
        sec_risk_tag=str(signal_row.get("sec_risk_tag") or "unknown"),
        forensic_flags=flags,
        sec_risk_score=sec_score_f,
        close_vs_sma200_pct=trend_pct,
        weights=composite_quality_weights_from_config(skill_dir),
    )


def _series_from_df(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    raw = df[col] if col in df.columns else default
    out = pd.to_numeric(raw, errors="coerce")
    if not isinstance(out, pd.Series):
        out = pd.Series([out] * len(df), index=df.index, dtype=float)
    return out.fillna(default)


def _trend_pct_series(df: pd.DataFrame) -> pd.Series:
    if "close_vs_sma200_pct" in df.columns:
        raw = pd.to_numeric(df["close_vs_sma200_pct"], errors="coerce")
        if isinstance(raw, pd.Series) and raw.notna().any():
            return raw.fillna(0.0)
    if "price" in df.columns and "sma_200" in df.columns:
        price = pd.to_numeric(df["price"], errors="coerce").fillna(0.0)
        sma = pd.to_numeric(df["sma_200"], errors="coerce").fillna(0.0)
        with pd.option_context("mode.chained_assignment", None):
            pct = pd.Series(0.0, index=df.index, dtype=float)
            valid = sma > 0
            pct.loc[valid] = (price.loc[valid] / sma.loc[valid]) - 1.0
            return pct.fillna(0.0)
    if "pts_sma" in df.columns:
        # Proxy audit rows: pts_sma = min(25, close_vs_sma200_pct * 100).
        return (pd.to_numeric(df["pts_sma"], errors="coerce").fillna(0.0) / 100.0).clip(lower=0.0)
    return pd.Series(0.0, index=df.index, dtype=float)


def compute_composite_quality_series(
    df: pd.DataFrame,
    weights: CompositeQualityWeights,
) -> pd.Series:
    """Vectorized composite for offline tuning."""
    signal = _series_from_df(df, "signal_score")
    pts_52w = _series_from_df(df, "pts_52w")
    pts_volume = _series_from_df(df, "pts_volume")
    pts_mirofish = _series_from_df(df, "pts_mirofish")
    trend_pct = _trend_pct_series(df)

    sig_norm = signal.copy()
    if weights.exclude_52w:
        sig_norm = (signal - pts_52w).clip(lower=0.0, upper=100.0)
    sig_norm = sig_norm.clip(0.0, 100.0)
    vol_norm = ((pts_volume / PTS_VOLUME_CAP) * 100.0).clip(0.0, 100.0)
    miro_norm = ((pts_mirofish / PTS_MIROFISH_CAP) * 100.0).clip(0.0, 100.0)
    trend_norm = (trend_pct.clip(lower=0.0) / TREND_PCT_CAP * 100.0).clip(0.0, 100.0)

    predictive = (
        (weights.direct_trend_weight * trend_norm)
        + (weights.direct_volume_weight * vol_norm)
        + (weights.direct_signal_weight * sig_norm)
        + (weights.direct_mirofish_weight * miro_norm)
    ).clip(0.0, 100.0)

    p_up_raw = df["p_up_calibrated"] if "p_up_calibrated" in df.columns else None
    if p_up_raw is None or (isinstance(p_up_raw, pd.Series) and p_up_raw.notna().sum() < 10):
        p_up_raw = df["p_up_calibrated_proxy"] if "p_up_calibrated_proxy" in df.columns else None
    p_up = pd.to_numeric(p_up_raw, errors="coerce") if p_up_raw is not None else pd.Series([0.5] * len(df), index=df.index)
    if not isinstance(p_up, pd.Series):
        p_up = pd.Series([p_up] * len(df), index=df.index, dtype=float)
    p_up = p_up.fillna(0.5).clip(0.01, 0.99)

    edge = (
        (weights.edge_signal_weight * sig_norm) + (weights.edge_pup_weight * p_up * 100.0)
    ).clip(0.0, 100.0)

    rel_col = "reliability_score" if "reliability_score" in df.columns else "reliability_score_proxy"
    exe_col = "execution_score" if "execution_score" in df.columns else "execution_score_proxy"
    reliability = (
        _series_from_df(df, rel_col, default=82.0).clip(0.0, 100.0)
        if rel_col in df.columns
        else pd.Series(82.0, index=df.index)
    )
    execution = (
        _series_from_df(df, exe_col, default=100.0).clip(0.0, 100.0)
        if exe_col in df.columns
        else pd.Series(100.0, index=df.index)
    )

    if weights.use_direct_components:
        if weights.stack_blend_weight > 0 and not weights.safety_caps_only:
            stack = (
                (edge * weights.composite_edge_weight)
                + (reliability * weights.composite_reliability_weight)
                + (execution * weights.composite_execution_weight)
            )
            base = ((1.0 - weights.stack_blend_weight) * predictive) + (weights.stack_blend_weight * stack)
        elif weights.stack_blend_weight > 0:
            base = ((1.0 - weights.stack_blend_weight) * predictive) + (weights.stack_blend_weight * edge)
        else:
            base = predictive
    else:
        base = (
            (edge * weights.composite_edge_weight)
            + (reliability * weights.composite_reliability_weight)
            + (execution * weights.composite_execution_weight)
        )

    composite = base.copy()
    composite = np.where(reliability < 40.0, np.minimum(composite, 55.0), composite)
    composite = np.where(execution < 45.0, np.minimum(composite, 58.0), composite)
    sec = _series_from_df(df, "sec_risk_score", default=0.0) if "sec_risk_score" in df.columns else None
    if sec is not None:
        composite = np.where(sec >= 0.67, np.minimum(composite, 45.0), composite)
    return pd.Series(np.clip(composite, 0.0, 100.0), index=df.index, dtype=float)
