"""Shared tab-agent objective: risk-adjusted return beats raw win rate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.tab_agent import TabAgentError
from core.tab_agent_objective import ObjectiveConfig, compare_books, evaluate_book
from core.today_tab_agent import TodayTabAgent, resolve_for_scan
from engine_analysis import AGENT_SYSTEMS


def _config(**kwargs) -> ObjectiveConfig:
    base = dict(
        metric="sortino", min_samples=8, drawdown_tolerance=0.02, drawdown_duration_tolerance_days=10, horizon_days=10
    )
    base.update(kwargs)
    return ObjectiveConfig(**base)


def _dates(n: int, start: str = "2026-01-01") -> list[str]:
    year, month, day = (int(part) for part in start.split("-"))
    out = []
    for i in range(n):
        out.append(f"{year:04d}-{month:02d}-{min(28, day + i):02d}")
    return out


def test_higher_win_rate_loses_when_sortino_and_drawdown_are_worse() -> None:
    config = _config()
    # Live: modest wins, shallow dips.
    live_returns = [1.0, -0.4] * 10
    # Candidate: 80% wins but two crash days. Higher win rate, worse path.
    candidate_returns = [4.0] * 16 + [-20.0, -20.0]
    live = evaluate_book(live_returns, _dates(len(live_returns)), config=config)
    cand = evaluate_book(candidate_returns, _dates(len(candidate_returns), "2026-03-01"), config=config)
    assert cand["win_rate"] > live["win_rate"]
    assert compare_books(cand, live, config=config) != "better_than_live"


def test_better_sortino_with_deeper_drawdown_is_not_preferred() -> None:
    config = _config()
    live_returns = [1.0, -0.8] * 12
    candidate_returns = [3.0] * 22 + [-20.0, -20.0]
    live = evaluate_book(live_returns, _dates(len(live_returns)), config=config)
    cand = evaluate_book(candidate_returns, _dates(len(candidate_returns)), config=config)
    assert cand["primary_score"] > live["primary_score"]
    assert cand["max_drawdown"] < live["max_drawdown"] - config.drawdown_tolerance
    assert compare_books(cand, live, config=config) == "drawdown_worse"


def test_better_sortino_with_similar_drawdown_is_preferred() -> None:
    config = _config()
    live_returns = [0.4, -0.3] * 12
    candidate_returns = [1.2, -0.2] * 12
    live = evaluate_book(live_returns, _dates(len(live_returns)), config=config)
    cand = evaluate_book(candidate_returns, _dates(len(candidate_returns)), config=config)
    assert compare_books(cand, live, config=config) == "better_than_live"


def test_small_sample_is_insufficient() -> None:
    config = _config(min_samples=30)
    live = evaluate_book([1.0, 1.0, -0.2], _dates(3), config=config)
    cand = evaluate_book([2.0, 2.0, 2.0], _dates(3), config=config)
    assert compare_books(cand, live, config=config) == "insufficient_sample"


def test_longer_drawdown_blocks_an_otherwise_better_book() -> None:
    config = _config(drawdown_tolerance=0.05, drawdown_duration_tolerance_days=5)
    live_returns = [0.6, -0.4] * 12
    live_dates = _dates(len(live_returns))
    # Stronger drift, one small dip, then a long flat stretch that stays underwater.
    cand_returns = [2.0] * 10 + [-0.8] + [0.0] * 18 + [2.0] * 6
    cand_dates = _dates(len(cand_returns))
    live = evaluate_book(live_returns, live_dates, config=config)
    cand = evaluate_book(cand_returns, cand_dates, config=config)
    assert cand["max_drawdown_duration_days"] > live["max_drawdown_duration_days"] + 5
    assert compare_books(cand, live, config=config) == "drawdown_duration_worse"


def test_propose_stays_pending_and_scan_path_stays_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "false")
    live, shadow, bindings = resolve_for_scan(tmp_path)
    assert live == dict(AGENT_SYSTEMS)
    assert shadow == {}
    assert bindings == []

    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    agent = TodayTabAgent()
    key = "institutional_trend"
    original = agent.registry(tmp_path).live_version("today", key)["text"]
    assert original == AGENT_SYSTEMS[key]
    revised = original + " Prefer smoother equity curves over raw win rate."
    proposed = agent.propose(
        tmp_path,
        llm=lambda _system, _user: json.dumps(
            {"text": revised, "rationale": "Sortino first. Sample may be small."}
        ),
        prompt_key=key,
    )
    assert proposed["auto_promote"] is False
    assert proposed["created"][0]["status"] == "pending"
    assert agent.registry(tmp_path).live_version("today", key)["text"] == original
    agent.approve(tmp_path, proposed["created"][0]["id"])
    assert agent.registry(tmp_path).live_version("today", key)["text"] == revised
    retired_id = proposed["created"][0]["parent"]
    agent.rollback(tmp_path, retired_id)
    assert agent.registry(tmp_path).live_version("today", key)["text"] == original
    with pytest.raises(TabAgentError):
        agent.reject(tmp_path, agent.registry(tmp_path).live_version("today", key)["id"])
