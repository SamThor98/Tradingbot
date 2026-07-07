"""Tests for packet feature lift analysis (management integrity)."""

from __future__ import annotations

from core import decision_packet, packet_feature_analysis


def _pkt(
    *,
    mgmt=None,
    label="win",
    ret=3.0,
    horizon=10,
):
    return {
        "management_integrity": mgmt,
        "outcome": {
            "label": label,
            "realized_return_pct": ret,
            "horizon_days": horizon,
        },
    }


def test_management_integrity_cohort() -> None:
    packets = [
        _pkt(mgmt={"score_bucket": "high", "score": 80}, ret=2.0),
        _pkt(mgmt={"score_bucket": "low", "score": 40}, ret=-1.0, label="loss"),
    ]
    metrics = packet_feature_analysis._cohort_metrics(
        packets, packet_feature_analysis.management_integrity_bucket
    )
    assert "high" in metrics["cohorts"]
    assert "low" in metrics["cohorts"]


def test_era_split_horizons() -> None:
    packets = [
        _pkt(mgmt={"score_bucket": "high", "score": 80}, horizon=15, ret=2.0),
        _pkt(mgmt={"score_bucket": "high", "score": 80}, horizon=30, ret=1.0),
    ]
    eras = packet_feature_analysis.split_by_horizon_era(packets)
    assert len(eras["le_20d"]) == 1
    assert len(eras["21_40d"]) == 1


def test_build_packet_includes_management_integrity() -> None:
    pkt = decision_packet.build_packet(
        ticker="AAPL",
        signal={
            "management_integrity": {
                "score": 72,
                "score_bucket": "high",
                "profile": "scaled_growth",
                "red_flag_count": 1,
            }
        },
    )
    assert pkt.management_integrity is not None
    assert pkt.management_integrity["score"] == 72


def test_pilot_recommends_management_integrity_on_short_era_lift() -> None:
    short = [
        _pkt(mgmt={"score_bucket": "high", "score": 80}, horizon=10, ret=6.0),
        _pkt(mgmt={"score_bucket": "high", "score": 80}, horizon=12, ret=5.0),
        _pkt(mgmt={"score_bucket": "high", "score": 80}, horizon=8, ret=4.0),
        _pkt(mgmt={"score_bucket": "high", "score": 80}, horizon=15, ret=3.0),
        _pkt(mgmt={"score_bucket": "high", "score": 80}, horizon=18, ret=2.0),
        _pkt(mgmt={"score_bucket": "low", "score": 40}, horizon=10, ret=-4.0),
    ]
    long = [
        _pkt(mgmt={"score_bucket": "high", "score": 80}, horizon=25, ret=1.0),
        _pkt(mgmt={"score_bucket": "high", "score": 80}, horizon=30, ret=0.5),
    ]
    packets = short + long
    report = packet_feature_analysis.feature_lift_report(packets)
    pilot = report["pilot_recommendation"]
    assert pilot["ready_for_single_era_pilot"] is True
    assert pilot["recommended_feature"] == "management_integrity"
