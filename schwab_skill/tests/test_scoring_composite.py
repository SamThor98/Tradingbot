from __future__ import annotations

import pandas as pd

from core.scoring_composite import (
    CompositeQualityWeights,
    compute_composite_quality,
    compute_composite_quality_series,
    compute_predictive_core,
)


def test_predictive_core_weights_trend_and_volume() -> None:
    weights = CompositeQualityWeights()
    low = compute_predictive_core(
        signal_score=70.0,
        pts_52w=20.0,
        pts_volume=5.0,
        pts_mirofish=0.0,
        close_vs_sma200_pct=0.02,
        weights=weights,
    )
    high = compute_predictive_core(
        signal_score=70.0,
        pts_52w=20.0,
        pts_volume=18.0,
        pts_mirofish=0.0,
        close_vs_sma200_pct=0.12,
        weights=weights,
    )
    assert high > low


def test_trend_norm_from_price() -> None:
    from core.scoring_composite import trend_norm_from_price

    assert abs(trend_norm_from_price(price=110.0, sma_200=100.0) - 50.0) < 0.01


def test_predictive_core_weights_volume() -> None:
    weights = CompositeQualityWeights()
    low = compute_predictive_core(
        signal_score=70.0,
        pts_52w=20.0,
        pts_volume=5.0,
        pts_mirofish=0.0,
        weights=weights,
    )
    high = compute_predictive_core(
        signal_score=70.0,
        pts_52w=20.0,
        pts_volume=18.0,
        pts_mirofish=0.0,
        weights=weights,
    )
    assert high > low


def test_composite_quality_series_matches_row_logic() -> None:
    weights = CompositeQualityWeights(stack_blend_weight=0.0)
    row = {
        "signal_score": 72.0,
        "pts_52w": 18.0,
        "pts_volume": 14.0,
        "pts_mirofish": 6.0,
        "close_vs_sma200_pct": 0.08,
        "p_up_calibrated": 0.58,
        "reliability_score": 80.0,
        "execution_score": 85.0,
        "sec_risk_score": 0.0,
    }
    df = pd.DataFrame([row])
    series_score = float(compute_composite_quality_series(df, weights).iloc[0])
    scalar_score = compute_composite_quality(
        signal_score=72.0,
        pts_52w=18.0,
        pts_volume=14.0,
        pts_mirofish=6.0,
        close_vs_sma200_pct=0.08,
        p_up_calibrated=0.58,
        reliability_score=80.0,
        execution_score=85.0,
        sec_risk_tag="unknown",
        forensic_flags=[],
        weights=weights,
    )
    assert abs(series_score - scalar_score) < 0.02


def test_risk_caps_limit_composite() -> None:
    weights = CompositeQualityWeights()
    score = compute_composite_quality(
        signal_score=90.0,
        pts_52w=10.0,
        pts_volume=18.0,
        pts_mirofish=8.0,
        p_up_calibrated=0.7,
        reliability_score=30.0,
        execution_score=90.0,
        sec_risk_tag="unknown",
        forensic_flags=[],
        weights=weights,
    )
    assert score <= 55.0
