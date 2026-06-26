from __future__ import annotations

from core.scoring_rank_v2 import (
    apply_rank_v2_risk_caps,
    compute_rank_score_v2,
    rank_v2_from_signal_row,
)


def test_compute_rank_score_v2_volume_heavy() -> None:
    low_vol = compute_rank_score_v2(signal_score=70.0, pts_volume=5.0, pts_mirofish=0.0, exclude_52w=False)
    high_vol = compute_rank_score_v2(signal_score=70.0, pts_volume=18.0, pts_mirofish=0.0, exclude_52w=False)
    assert high_vol > low_vol


def test_rank_v2_from_signal_row_uses_components() -> None:
    row = {
        "signal_score": 60.0,
        "score_components": {"pts_volume": 16.0, "pts_mirofish": 10.0, "pts_52w": 20.0},
        "reliability_score": 80.0,
        "execution_score": 75.0,
    }
    score = rank_v2_from_signal_row(row)
    assert 0.0 <= score <= 100.0
    assert score > 50.0


def test_exclude_52w_lowers_rank_when_52w_inflates_signal() -> None:
    with_52w = compute_rank_score_v2(
        signal_score=80.0, pts_52w=30.0, pts_volume=10.0, pts_mirofish=0.0, exclude_52w=False
    )
    without_52w = compute_rank_score_v2(
        signal_score=80.0, pts_52w=30.0, pts_volume=10.0, pts_mirofish=0.0, exclude_52w=True
    )
    assert without_52w < with_52w


def test_risk_caps_limit_high_rank_on_bad_reliability() -> None:
    uncapped = compute_rank_score_v2(
        signal_score=90.0,
        pts_volume=18.0,
        pts_mirofish=10.0,
        reliability_score=100.0,
        execution_score=100.0,
        exclude_52w=True,
    )
    capped = compute_rank_score_v2(
        signal_score=90.0,
        pts_volume=18.0,
        pts_mirofish=10.0,
        reliability_score=30.0,
        execution_score=100.0,
        exclude_52w=True,
    )
    assert capped <= 55.0
    assert uncapped > capped


def test_apply_rank_v2_risk_caps_forensic() -> None:
    assert apply_rank_v2_risk_caps(
        80.0,
        reliability_score=90.0,
        execution_score=90.0,
        sec_risk_tag="high",
        forensic_flags=[],
    ) <= 45.0
