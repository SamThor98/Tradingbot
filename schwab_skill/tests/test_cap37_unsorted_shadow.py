"""Cap-37 unsorted shadow never promotes the live sort by itself."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.compare_cap37_unsorted_vs_live import (  # noqa: E402
    build_report,
    cap37_unsorted,
    capacity_decision,
    capped_rank_decision,
    live_rank_cut,
    p76_book,
    shadow_verdict,
)
from webapp.decision_dashboard_service import summarize_book_width_artifact  # noqa: E402


def _arm(pf: float, worst: float, lo: float, hi: float) -> dict:
    return {
        "pf_mean": pf,
        "worst_era_pf": worst,
        "pf_pooled_ci": {"pf_lo": lo, "pf_hi": hi},
    }


def test_separated_winner_stays_unpromoted() -> None:
    decision = shadow_verdict(_arm(1.25, 1.05, 1.20, 1.40), _arm(1.03, 0.94, 0.95, 1.10))
    assert decision["verdict"] == "shadow_ready_sort_not_promoted"
    assert decision["promote_sort"] is False
    assert decision["week_block_ci_separates"] is True


def test_overlap_does_not_count_as_separated() -> None:
    decision = shadow_verdict(_arm(1.25, 1.05, 1.10, 1.40), _arm(1.03, 0.94, 0.95, 1.15))
    assert decision["verdict"] == "shadow_ahead_intervals_overlap"
    assert decision["promote_sort"] is False


def test_cap_keeps_missing_extension_and_drops_extended() -> None:
    frame = pd.DataFrame({"pts_52w": [10.0, 37.0, 38.0, None]})
    kept = cap37_unsorted(frame)
    assert len(kept) == 3
    assert float(kept["pts_52w"].max(skipna=True)) <= 37.0


def test_top10_ready_stays_unapplied() -> None:
    decision = capacity_decision(
        _arm(1.30, 1.08, 1.20, 1.40),
        _arm(1.28, 1.06, 1.18, 1.38),
        _arm(1.03, 0.94, 0.90, 1.10),
    )
    assert decision["verdict"] == "top10_ready_not_applied"
    assert decision["apply_live"] is False
    assert decision["recommended_arm"] == "p76_top10"


def test_full_book_used_only_when_top10_does_not_separate() -> None:
    decision = capacity_decision(
        _arm(1.22, 1.02, 1.05, 1.30),
        _arm(1.28, 1.06, 1.20, 1.40),
        _arm(1.03, 0.94, 0.90, 1.10),
    )
    assert decision["verdict"] == "p76_all_ready_not_applied"
    assert decision["apply_live"] is False
    assert decision["recommended_arm"] == "p76_all"


def test_overlap_keeps_the_live_top5() -> None:
    decision = capacity_decision(
        _arm(1.22, 1.02, 1.00, 1.30),
        _arm(1.24, 1.04, 1.02, 1.32),
        _arm(1.03, 0.94, 0.90, 1.15),
    )
    assert decision["verdict"] == "wider_ahead_intervals_overlap"
    assert decision["recommended_arm"] is None
    assert decision["apply_live"] is False


def test_daily_cap_never_exceeds_the_p76_book() -> None:
    rows = []
    for day in ("2020-01-06", "2020-01-07"):
        for i in range(12):
            rows.append(
                {
                    "entry_date": pd.Timestamp(day),
                    "rank_score_v2": 80.0,
                    "signal_score": float(12 - i),
                    "pts_52w": 10.0,
                    "net_return": 0.01,
                }
            )
    frame = pd.DataFrame(rows)
    assert len(p76_book(frame, percentile=0)) == 24
    assert len(live_rank_cut(frame, percentile=0, top_n=10)) == 20
    assert len(live_rank_cut(frame, percentile=0, top_n=5)) == 10


def test_build_report_keeps_live_settings_off() -> None:
    rows = []
    for day_i, day in enumerate(("2020-01-06", "2020-01-13", "2020-06-01", "2021-01-04")):
        for i in range(6):
            rows.append(
                {
                    "entry_date": pd.Timestamp(day),
                    "era": "late_bull" if day_i < 2 else "bear_rates",
                    "net_return": 0.02 if i < 4 else -0.01,
                    "pts_52w": 10.0 + i,
                    "signal_score": float(80 - i),
                    "rank_score_v2": float(90 - i),
                    "ticker": f"T{day_i}{i}",
                }
            )
    report = build_report(pd.DataFrame(rows), run_id="unit")
    assert report["promote_sort"] is False
    assert report["capacity"]["apply_live"] is False
    assert report["capped_rank"]["promote_sort"] is False
    assert report["capped_rank"]["apply_live"] is False
    assert set(report["arms"]) == {
        "pts52w_cap37_unsorted",
        "current_live_p76_top5",
        "p76_top10",
        "p76_all",
        "cap37_p76",
        "cap37_p76_top10",
        "cap37_p76_top5",
        "cap37_p76_top3",
    }
    assert report["arms"]["p76_all"]["n"] >= report["arms"]["p76_top10"]["n"]
    assert report["arms"]["p76_top10"]["n"] >= report["arms"]["current_live_p76_top5"]["n"]
    assert report["arms"]["pts52w_cap37_unsorted"]["n"] >= report["arms"]["cap37_p76"]["n"]
    assert report["arms"]["cap37_p76"]["n"] >= report["arms"]["cap37_p76_top10"]["n"]
    assert report["arms"]["cap37_p76_top10"]["n"] >= report["arms"]["cap37_p76_top5"]["n"]
    assert report["arms"]["cap37_p76_top5"]["n"] >= report["arms"]["cap37_p76_top3"]["n"]


def test_thin_top5_after_the_cap_is_not_a_lever() -> None:
    decision = capped_rank_decision(
        {"n": 100, "pf_mean": 1.15, "worst_era_pf": 1.04, "pf_pooled_ci": {"pf_lo": 1.0, "pf_hi": 1.3}},
        {"n": 96, "pf_mean": 1.16, "worst_era_pf": 1.05, "pf_pooled_ci": {"pf_lo": 1.0, "pf_hi": 1.3}},
    )
    assert decision["verdict"] == "top5_not_the_lever"
    assert decision["names_removed_by_top5"] == 4
    assert decision["apply_live"] is False
    assert decision["promote_sort"] is False


def test_wide_top5_cut_is_named_and_still_unapplied() -> None:
    decision = capped_rank_decision(
        {"n": 100, "pf_mean": 1.15, "worst_era_pf": 1.04, "pf_pooled_ci": {"pf_lo": 1.0, "pf_hi": 1.3}},
        {"n": 40, "pf_mean": 1.16, "worst_era_pf": 1.05, "pf_pooled_ci": {"pf_lo": 1.0, "pf_hi": 1.3}},
    )
    assert decision["verdict"] == "capped_top5_changes_membership"
    assert decision["names_removed_by_top5"] == 60
    assert decision["apply_live"] is False


def test_dashboard_summary_never_applies_live() -> None:
    summary = summarize_book_width_artifact(
        {
            "generated_at": "2026-10-07T00:00:00+00:00",
            "run_id": "control_legacy_aug",
            "verdict": "shadow_ahead_intervals_overlap",
            "promote_sort": False,
            "capacity": {
                "verdict": "top10_ready_not_applied",
                "apply_live": True,
                "recommended_arm": "p76_top10",
                "note": "shadow only",
            },
            "arms": {
                "current_live_p76_top5": {
                    "n": 10,
                    "pf_mean": 1.02,
                    "worst_era_pf": 0.94,
                    "pf_pooled_ci": {"pf_lo": 0.9, "pf_hi": 1.1},
                    "selection": "top 5",
                },
                "p76_top10": {
                    "n": 20,
                    "pf_mean": 1.26,
                    "worst_era_pf": 1.05,
                    "pf_pooled_ci": {"pf_lo": 1.2, "pf_hi": 1.4},
                    "selection": "top 10",
                },
            },
        }
    )
    assert summary["present"] is True
    assert summary["apply_live"] is False
    assert summary["promote_sort"] is False
    assert summary["verdict"] == "top10_ready_not_applied"
    assert summary["arms"][0]["role"] == "live"
    assert summary["arms"][0]["clears_floors"] is False
    assert summary["arms"][1]["clears_floors"] is True
    assert summary["capped_rank_verdict"] is None
    assert summary["names_removed_by_top5"] is None


def test_dashboard_summary_empty_without_artifact() -> None:
    summary = summarize_book_width_artifact(None)
    assert summary["present"] is False
    assert summary["apply_live"] is False
    assert summary["arms"] == []
