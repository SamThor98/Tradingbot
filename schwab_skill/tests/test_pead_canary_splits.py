"""Paper S1 splits stay observational: flags attach, membership does not change."""

from __future__ import annotations

from core.pead_canary_splits import (
    annotate_pead_shadow_name,
    format_s1_split_lines,
    summarize_s1_splits,
)
from core.r7_promotion import r7_may_promote
from signal_scanner import _partition_stage_a_by_entry_family


def test_annotate_flags_extension_and_large_surprise() -> None:
    extended = annotate_pead_shadow_name(
        {"ticker": "EXT", "price": 130.0, "sma_200": 100.0, "pead_surprise_pct": 0.20}
    )
    inside = annotate_pead_shadow_name(
        {"ticker": "IN", "price": 110.0, "sma_200": 100.0, "pead_surprise_pct": 0.06}
    )
    missing = annotate_pead_shadow_name({"ticker": "NA", "pead_surprise_pct": None})
    assert extended["pead_extension_drop_would_drop"] is True
    assert extended["close_vs_sma200_pct"] == 0.3
    assert extended["pead_surprise_large"] is True
    assert extended["executable"] is False
    assert inside["pead_extension_drop_would_drop"] is False
    assert inside["pead_surprise_large"] is False
    assert missing["pead_extension_drop_would_drop"] is None
    assert missing["pead_surprise_large"] is None


def test_annotate_keeps_a_stamped_flag_when_distance_is_gone() -> None:
    row = annotate_pead_shadow_name(
        {
            "ticker": "KEEP",
            "pead_extension_drop_would_drop": False,
            "pead_surprise_large": True,
        }
    )
    assert row["pead_extension_drop_would_drop"] is False
    assert row["pead_surprise_large"] is True


def test_partition_keeps_extended_names_in_capacity_order() -> None:
    part = _partition_stage_a_by_entry_family(
        [
            {
                "ticker": "EXT",
                "entry_family": "pead_primary",
                "stage_a_score": 10.0,
                "edge_score": 90.0,
                "price": 130.0,
                "sma_200": 100.0,
                "pead_beat": True,
                "pead_surprise_pct": 0.2,
            },
            {
                "ticker": "IN",
                "entry_family": "pead_primary",
                "stage_a_score": 99.0,
                "edge_score": 40.0,
                "price": 105.0,
                "sma_200": 100.0,
                "pead_beat": True,
                "pead_surprise_pct": 0.04,
            },
            {"ticker": "S2", "entry_family": "stage2", "stage_a_score": 80.0, "edge_score": 70.0},
        ],
        top_n=5,
        pead_shadow_max=10,
        pead_capacity_top_n=5,
    )
    names = part["pead_primary_shadow_names"]
    assert [n["ticker"] for n in names] == ["EXT", "IN"]
    assert names[0]["pead_extension_drop_would_drop"] is True
    assert names[0]["capacity_top_n"] is True
    assert names[1]["pead_extension_drop_would_drop"] is False
    assert names[1]["pead_surprise_large"] is False
    assert {c["ticker"] for c in part["executable"]} == {"S2"}


def test_splits_are_observational_and_r7_uses_the_full_sleeve() -> None:
    records = [
        {
            "sleeve_id": "S1",
            "source": "pead_canary",
            "raw_context": {
                "pead_extension_drop_would_drop": True,
                "pead_surprise_large": True,
            },
            "outcomes": {"5": {"thesis_hit": False, "return_pct": -4.0}},
        },
        {
            "sleeve_id": "S1",
            "source": "pead_canary",
            "raw_context": {
                "pead_extension_drop_would_drop": False,
                "pead_surprise_large": False,
                "pead_surprise_pct": 0.04,
            },
            "outcomes": {"5": {"thesis_hit": True, "return_pct": 3.0}},
        },
        {
            "sleeve_id": "S1",
            "source": "pead_canary",
            "raw_context": {},
            "outcomes": {"5": {"thesis_hit": True, "return_pct": 1.0}},
        },
        {
            "sleeve_id": "S0",
            "outcomes": {"5": {"thesis_hit": True, "return_pct": 50.0}},
        },
    ]
    summary = summarize_s1_splits(records, horizon="5")
    slices = summary["slices"]
    assert slices["all"]["n"] == 3
    assert slices["all"]["mean_return_pct"] == 0.0
    assert slices["not_extended"]["n"] == 1
    assert slices["not_extended"]["mean_return_pct"] == 3.0
    assert slices["high_surprise"]["n"] == 1
    assert slices["high_surprise"]["mean_return_pct"] == -4.0
    assert summary["extension_unmeasured"] == 1
    assert summary["surprise_unmeasured"] == 1
    assert summary["allow_live"] is False
    assert summary["changes_rank"] is False
    lines = format_s1_split_lines(summary)
    assert any(line.startswith("s1_split all ") for line in lines)
    gate = r7_may_promote(records, sleeve_id="S1", offline_floors_ok=True)
    assert gate["summary"]["n"] == 3
    assert "insufficient_n:3<40" in gate["reasons"]
