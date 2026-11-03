"""Trust score maps extension onto 0–100 and applies safety caps after."""

from __future__ import annotations

from core.trust_score import trust_score_block, trust_score_from_pts


def test_full_span_maps_least_extended_to_100() -> None:
    assert trust_score_from_pts(0.0) == 100.0
    assert trust_score_from_pts(20.0) == 50.0
    assert trust_score_from_pts(40.0) == 0.0
    assert trust_score_from_pts(80.0) == 0.0


def test_block_records_evidence_and_leaves_raw_when_uncapped() -> None:
    block = trust_score_block({"reliability_score": 80.0}, pts_52w=10.0)
    assert block["trust_score_raw"] == 75.0
    assert block["trust_score"] == 75.0
    assert block["trust_score_caps"] == []
    assert block["trust_score_book"] == "control_legacy_aug"
    assert block["trust_score_label"] == "net_return"
    assert "1.4212" in block["trust_score_evidence"]
    assert "does not reorder" in block["trust_score_evidence"]


def test_forensic_cap_lowers_a_high_raw_score() -> None:
    block = trust_score_block(
        {"forensic_flags": ["beneish_manipulator"], "reliability_score": 90.0},
        pts_52w=0.0,
    )
    assert block["trust_score_raw"] == 100.0
    assert block["trust_score"] == 45.0
    assert block["trust_score_caps"] == ["forensic"]


def test_missing_safety_inputs_do_not_cap() -> None:
    block = trust_score_block({}, pts_52w=8.0)
    assert block["trust_score"] == block["trust_score_raw"]
    assert block["trust_score_caps"] == []
