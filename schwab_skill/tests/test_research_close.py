from __future__ import annotations

from pathlib import Path

import pytest

from core.research_close import run_research_close


def test_research_close_scores_before_study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def _score(skill_dir: Path) -> int:
        order.append("score")
        assert skill_dir == tmp_path
        return 0

    def _study(skill_dir: Path | None = None) -> dict[str, object]:
        order.append("study")
        assert skill_dir == tmp_path
        return {
            "round_trips_count": 2,
            "hypothesis_calibration": {"ledger_records": 4},
        }

    monkeypatch.setattr("core.research_close._score_ledger", _score)
    monkeypatch.setattr("self_study.run_self_study", _study)
    result = run_research_close(tmp_path)
    assert order == ["score", "study"]
    assert result["score_ok"] is True
    assert result["score_error"] is None
    assert result["round_trips_count"] == 2
    assert result["hypothesis_calibration"] == {"ledger_records": 4}
    assert result["hypothesis_cards"]["scored"] == 0
    assert result["hypothesis_cards_error"] is None


def test_research_close_studies_when_score_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _score(_skill_dir: Path) -> int:
        raise RuntimeError("auth down")

    monkeypatch.setattr("core.research_close._score_ledger", _score)
    monkeypatch.setattr(
        "self_study.run_self_study",
        lambda skill_dir=None: {"round_trips_count": 0},
    )
    result = run_research_close(tmp_path)
    assert result["score_ok"] is False
    assert result["score_error"] == "RuntimeError"
    assert result["study_error"] is None
    assert result["round_trips_count"] == 0
    assert result["hypothesis_cards"]["scored"] == 0
    assert result["hypothesis_cards_error"] is None
