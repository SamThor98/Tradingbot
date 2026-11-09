"""Debate quality bar: walk-forward sweep on stored seat scores, no model calls."""

from __future__ import annotations

import json

from core.debate_quality import book_at_threshold, sweep_thresholds, tune_debate_threshold
from core.debate_tab_agent import DebateTabAgent
from core.tab_agent_objective import ObjectiveConfig
from core.tab_agent_replay import append_sample


def _config() -> ObjectiveConfig:
    return ObjectiveConfig(min_samples=5, drawdown_tolerance=0.20, horizon_days=20)


def _case(day: str, score: float, ret: float, name: str) -> dict:
    return {"ticker": name, "as_of": day, "debate_score": score, "counterfactual_return_pct": ret}


def _book(early: bool, index: int, winner: bool) -> dict:
    month = "01" if early else "06"
    day = f"2026-{month}-{(index % 27) + 1:02d}"
    if winner:
        return _case(day, 52.0, 6.0, f"{'E' if early else 'L'}W{index}")
    return _case(day, 90.0, -4.0, f"{'E' if early else 'L'}L{index}")


def test_threshold_needs_thirty_longs_and_a_confirming_holdout() -> None:
    cases = [_book(True, i, True) for i in range(40)]
    cases += [_book(True, i, False) for i in range(8)]
    cases += [_book(False, i, True) for i in range(16)]
    cases += [_book(False, i, False) for i in range(4)]
    report = sweep_thresholds(cases, _config(), live_threshold=60)
    assert report["llm_calls"] == 0
    assert report["accepted"] is True
    assert report["chosen_threshold"] <= 50
    assert report["split_date"] is not None
    sixty = next(row for row in report["rows"] if row["threshold"] == 60)
    assert sixty["eligible"] is False
    chosen = next(row for row in report["rows"] if row["threshold"] == report["chosen_threshold"])
    assert chosen["n_longs"] >= 30
    assert chosen["max_drawdown"] >= -0.20


def test_holdout_losses_block_a_threshold_that_only_wins_early() -> None:
    early = [_case(f"2026-01-{(i % 27) + 1:02d}", 52.0, 8.0, f"E{i}") for i in range(40)]
    late = [_case(f"2026-06-{(i % 27) + 1:02d}", 52.0, -8.0, f"L{i}") for i in range(20)]
    mild = [_case(f"2026-0{1 if i < 4 else 6}-{(i % 20) + 1:02d}", 90.0, 1.0, f"M{i}") for i in range(8)]
    report = sweep_thresholds(early + late + mild, _config(), live_threshold=60)
    assert report["accepted"] is False
    assert report["chosen_threshold"] == 60


def test_cash_is_every_score_under_the_bar() -> None:
    cases = [_case("2026-03-02", 59.0, 12.0, "AAA"), _case("2026-03-03", 60.0, 4.0, "BBB")]
    book = book_at_threshold(cases, 60, _config())
    assert book["n"] == 2
    assert book["n_longs"] == 1
    assert book["win_rate"] == 1.0


def test_tune_command_writes_a_shadow_and_does_not_promote(tmp_path) -> None:
    agent = DebateTabAgent()
    reg = agent.registry(tmp_path)
    ids = {key: reg.live_version("debate", key)["id"] for key in ("system", "round1", "round2")}
    cases = [_book(True, i, True) for i in range(40)] + [_book(False, i, True) for i in range(16)]
    for case in cases:
        append_sample(
            tmp_path,
            {
                "source": "replay",
                "tab_id": "debate",
                "prompt_key": "combination",
                "version_id": "combo:old",
                "prompt_versions": ids,
                "ticker": case["ticker"],
                "as_of": case["as_of"],
                "decision": "cash",
                "debate_score": case["debate_score"],
                "realized_return_pct": 0.0,
                "counterfactual_return_pct": case["counterfactual_return_pct"],
                "scored": True,
            },
        )
    report = tune_debate_threshold(tmp_path)
    assert report["llm_calls"] == 0
    assert report["accepted"] is True
    shadow = next(row for row in reg.versions("debate", "quality") if row.get("status") == "shadow")
    assert f"QUALITY_THRESHOLD={int(report['chosen_threshold'])}" in shadow["text"]
    assert reg.live_version("debate", "quality")["text"].startswith("QUALITY_THRESHOLD=60")
    review = json.loads((tmp_path / ".tab_agent_reviews" / f"debate-{shadow['id']}.json").read_text(encoding="utf-8"))
    assert "QUALITY_THRESHOLD" in review["diff"]
    status = agent.score(tmp_path)
    assert status["quality_threshold"] == 60
    assert "|q=" in status["versions"][0]["version_id"]
    assert status["quality_sweep"]["llm_calls"] == 0
