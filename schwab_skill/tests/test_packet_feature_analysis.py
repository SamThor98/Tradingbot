"""Tests for packet feature lift analysis (Kronos + management integrity)."""

from __future__ import annotations

from core import decision_packet, packet_feature_analysis


def _pkt(
    *,
    kronos=None,
    mgmt=None,
    label="win",
    ret=3.0,
    horizon=10,
):
    return {
        "kronos": kronos,
        "management_integrity": mgmt,
        "outcome": {
            "label": label,
            "realized_return_pct": ret,
            "horizon_days": horizon,
        },
    }


def test_kronos_cohort_buckets() -> None:
    packets = [
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, ret=5.0),
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, label="loss", ret=-2.0),
        _pkt(kronos={"direction": "down", "confidence_bucket": "low"}, ret=1.0),
    ]
    metrics = packet_feature_analysis._cohort_metrics(packets, packet_feature_analysis.kronos_bucket)
    assert metrics["cohorts"]["up_high"]["resolved"] == 2
    assert metrics["missing_feature"] == 0


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
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, horizon=15, ret=2.0),
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, horizon=30, ret=1.0),
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


def test_pilot_recommends_kronos_on_short_era_lift() -> None:
    short = [
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, horizon=10, ret=6.0),
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, horizon=12, ret=5.0),
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, horizon=8, ret=4.0),
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, horizon=15, ret=3.0),
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, horizon=18, ret=2.0),
        _pkt(kronos={"direction": "down", "confidence_bucket": "low"}, horizon=10, ret=-4.0),
    ]
    long = [
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, horizon=25, ret=1.0),
        _pkt(kronos={"direction": "up", "confidence_bucket": "high"}, horizon=30, ret=0.5),
    ]
    packets = short + long
    report = packet_feature_analysis.feature_lift_report(packets)
    pilot = report["pilot_recommendation"]
    assert pilot["ready_for_single_era_pilot"] is True
    assert pilot["recommended_feature"] == "kronos"
