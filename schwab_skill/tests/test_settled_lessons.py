"""Settled-lessons card: evidence floors, labels, and operator decisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from webapp.settled_lessons import (
    SettledLessonRejected,
    build_settled_lessons,
    record_settled_lesson_decision,
)


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _study(trips: int, *, win_rate: float = 54.0, band_count: int = 10, band_avg: float = 1.5) -> dict:
    return {
        "round_trips_count": trips,
        "win_rate": win_rate,
        "avg_return_pct": 0.4,
        "min_round_trips_met": trips >= 30,
        "by_conviction": {
            "40-60": {"count": band_count, "win_rate": 40.0, "avg_return_pct": -0.8},
            "60-80": {"count": band_count, "win_rate": 62.0, "avg_return_pct": band_avg},
        },
    }


def _gate(path: Path, *, pf: float, worst: float) -> None:
    _write(
        path / "phase1_progress_signal_gate.json",
        {
            "ranking": [
                {
                    "config_id": "control_legacy",
                    "pf_mean_treatment": pf,
                    "worst_era_pf_treatment": worst,
                }
            ]
        },
    )


def _proposal(path: Path) -> None:
    _write(
        path / "strategy_update.json",
        {
            "generated_at": "2026-09-26T00:00:00+00:00",
            "updates": [
                {
                    "env_key": "VOLUME_RATIO_MIN",
                    "current_value": 1.1,
                    "suggested_value": 1.2,
                    "rationale": "Adjust VOLUME_RATIO_MIN from 1.1 to 1.2.",
                }
            ],
            "env_overrides": {"VOLUME_RATIO_MIN": "1.2"},
        },
    )


def test_empty_book_is_still_paper(tmp_path: Path) -> None:
    snap = build_settled_lessons(tmp_path)
    assert snap["settled"]["status"] == "still_paper"
    assert snap["pocket"]["label"] == "Paper"
    assert snap["actions_enabled"] is False
    assert snap["stop_reason"] == "still_paper"
    assert snap["protected"] is True
    assert "PEAD canary" in snap["settled"]["headline"]


def test_thin_sample_rejects_decision(tmp_path: Path) -> None:
    _write(tmp_path / ".self_study.json", _study(12))
    _proposal(tmp_path)
    with pytest.raises(SettledLessonRejected, match="Still paper"):
        record_settled_lesson_decision(tmp_path, "keep")
    assert not (tmp_path / "validation_artifacts" / "settled_lesson_decisions.jsonl").exists()


def test_blocked_pocket_when_signal_gate_fails(tmp_path: Path) -> None:
    _write(tmp_path / ".self_study.json", _study(40))
    _gate(tmp_path, pf=1.089, worst=0.903)
    _write(
        tmp_path / ".hypothesis_ledger.json",
        {
            "records": [
                {
                    "source": "advisory",
                    "outcomes": {"5": {"thesis_hit": True, "return_pct": 1.2}},
                },
                {
                    "source": "advisory",
                    "outcomes": {"5": {"thesis_hit": False, "return_pct": -0.4}},
                },
                {
                    "source": "advisory",
                    "outcomes": {"5": {"thesis_hit": True, "return_pct": 0.5}},
                },
            ]
        },
    )
    snap = build_settled_lessons(tmp_path)
    assert snap["settled"]["status"] == "scored"
    assert snap["settled"]["probability"]["beats_base_rate"] is True
    assert snap["pocket"]["label"] == "Blocked"
    assert snap["signal_gate"]["passes"] is False
    assert snap["actions_enabled"] is False
    assert snap["stop_reason"] == "no_proposal"


def test_keep_records_decision_without_touching_settings(tmp_path: Path) -> None:
    _write(tmp_path / ".self_study.json", _study(40))
    _gate(tmp_path, pf=1.25, worst=1.05)
    _proposal(tmp_path)
    before = (tmp_path / "strategy_update.json").read_text(encoding="utf-8")
    snap = record_settled_lesson_decision(tmp_path, "keep")
    assert snap["pocket"]["label"] == "Shadow"
    assert snap["decision"]["action"] == "keep"
    assert snap["actions_enabled"] is True
    assert (tmp_path / "strategy_update.json").read_text(encoding="utf-8") == before
    assert not (tmp_path / ".env").exists()
    journal = (tmp_path / "validation_artifacts" / "settled_lesson_decisions.jsonl").read_text(encoding="utf-8")
    row = json.loads(journal.strip())
    assert row["action"] == "keep"
    assert row["applied_to_env"] is False
    assert row["order_placed"] is False


def test_revert_discards_proposal_and_blocks_another_decision(tmp_path: Path) -> None:
    _write(tmp_path / ".self_study.json", _study(40))
    _gate(tmp_path, pf=1.25, worst=1.05)
    _proposal(tmp_path)
    snap = record_settled_lesson_decision(tmp_path, "revert")
    assert snap["decision"]["action"] == "revert"
    assert snap["actions_enabled"] is False
    assert snap["stop_reason"] == "reverted"
    with pytest.raises(SettledLessonRejected, match="already reverted"):
        record_settled_lesson_decision(tmp_path, "sharpen")
    assert (tmp_path / "strategy_update.json").is_file()


def test_pead_canary_floor_stays_paper(tmp_path: Path) -> None:
    _write(tmp_path / ".self_study.json", _study(40))
    _write(
        tmp_path / ".hypothesis_ledger.json",
        {
            "records": [
                {
                    "sleeve_id": "S1",
                    "source": "pead_canary",
                    "outcomes": {"5": {"thesis_hit": True, "return_pct": 2.0}},
                }
            ]
        },
    )
    snap = build_settled_lessons(tmp_path)
    assert snap["pead_canary"]["status"] == "still_paper"
    assert snap["pead_canary"]["n"] == 1
    assert "1 of 40" in snap["settled"]["headline"]
