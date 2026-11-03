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


def test_longer_drawdown_inside_the_guardrail_does_not_block() -> None:
    config = _config(drawdown_tolerance=0.20, drawdown_duration_tolerance_days=5)
    live_returns = [0.6, -0.4] * 12
    live_dates = _dates(len(live_returns))
    cand_returns = [2.0] * 10 + [-0.8] + [0.0] * 18 + [2.0] * 6
    cand_dates = _dates(len(cand_returns))
    live = evaluate_book(live_returns, live_dates, config=config)
    cand = evaluate_book(cand_returns, cand_dates, config=config)
    assert cand["max_drawdown_duration_days"] > live["max_drawdown_duration_days"] + 5
    assert cand["max_drawdown"] > -0.20
    assert compare_books(cand, live, config=config) == "better_than_live"


def test_drawdown_past_twenty_percent_blocks_a_higher_sortino() -> None:
    config = _config(drawdown_tolerance=0.20)
    live = evaluate_book([0.5, -0.45] * 16, _dates(32), config=config)
    cand = evaluate_book([1.2] * 28 + [-22.0], _dates(29), config=config)
    assert cand["primary_score"] > live["primary_score"]
    assert cand["max_drawdown"] < -0.20
    assert compare_books(cand, live, config=config) == "drawdown_worse"


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
        llm=lambda _system, _user: json.dumps({"text": revised, "rationale": "Sortino first. Sample may be small."}),
        prompt_key=key,
    )
    assert proposed["auto_promote"] is False
    assert proposed["created"][0]["status"] == "shadow"
    assert agent.registry(tmp_path).live_version("today", key)["text"] == original
    agent.approve(tmp_path, proposed["created"][0]["id"])
    assert agent.registry(tmp_path).live_version("today", key)["text"] == revised
    retired_id = proposed["created"][0]["parent"]
    agent.rollback(tmp_path, retired_id)
    assert agent.registry(tmp_path).live_version("today", key)["text"] == original
    with pytest.raises(TabAgentError):
        agent.reject(tmp_path, agent.registry(tmp_path).live_version("today", key)["id"])


def _book(version_id: str, status: str, primary: float, drawdown: float, parent: str | None = None) -> dict:
    return {
        "version_id": version_id,
        "prompt_key": "institutional_trend",
        "status": status,
        "parent": parent,
        "n": 40,
        "sufficient": True,
        "primary_metric": "sortino",
        "primary_score": primary,
        "primary_unbounded": False,
        "max_drawdown": drawdown,
        "max_drawdown_duration_days": 3,
        "win_rate": 0.5,
        "avg_realized_return_pct": 0.4,
    }


def test_govern_promotes_shadow_and_writes_a_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    agent = TodayTabAgent()
    key = "institutional_trend"
    live = agent.registry(tmp_path).live_version("today", key)
    created = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps(
            {"text": live["text"] + " Shadow candidate for a smoother curve.", "rationale": "Cut the crash days."}
        ),
        prompt_key=key,
    )
    shadow_id = created["created"][0]["id"]
    from core.tab_agent_review import verdict_path

    verdict_path(tmp_path, "today", shadow_id).write_text(
        json.dumps({"verdict": "approve", "notes": "quant ok", "reviewer": "quant"}),
        encoding="utf-8",
    )
    live_stats = _book(live["id"], "live", 0.2, -0.04)
    shadow_stats = _book(shadow_id, "shadow", 0.9, -0.04, parent=live["id"])

    def fake_score(_skill_dir, registry=None, since_ts=None):
        return {
            "versions": [live_stats, shadow_stats],
            "comparisons": [
                {
                    "version_id": shadow_id,
                    "prompt_key": key,
                    "edge_vs_live": "better_than_live",
                    "auto_promote": False,
                }
            ],
            "winner_rule": "risk adjusted",
        }

    agent.score = fake_score  # type: ignore[method-assign]
    result = agent.govern(tmp_path)
    assert result["actions"][0]["action"] == "auto_promote"
    assert agent.registry(tmp_path).live_version("today", key)["id"] == shadow_id
    text = (tmp_path / ".tab_agent_changes.jsonl").read_text(encoding="utf-8")
    assert "AUTO_PROMOTE" in text
    assert "maxDD=" in text
    assert "No order" not in text or "No order" in text


def test_govern_does_not_promote_on_drawdown_or_small_sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    agent = TodayTabAgent()
    key = "grok_research"
    live = agent.registry(tmp_path).live_version("today", key)
    created = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps(
            {"text": live["text"] + " Do not chase win rate.", "rationale": "Win rate is secondary."}
        ),
        prompt_key=key,
    )
    shadow_id = created["created"][0]["id"]

    def fake_score(_skill_dir, registry=None, since_ts=None):
        return {
            "versions": [],
            "comparisons": [
                {"version_id": shadow_id, "prompt_key": key, "edge_vs_live": "drawdown_worse", "auto_promote": False},
                {
                    "version_id": shadow_id,
                    "prompt_key": key,
                    "edge_vs_live": "insufficient_sample",
                    "auto_promote": False,
                },
            ],
        }

    agent.score = fake_score  # type: ignore[method-assign]
    result = agent.govern(tmp_path)
    assert result["actions"] == []
    assert agent.registry(tmp_path).live_version("today", key)["id"] == live["id"]


def test_govern_waits_for_a_review_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    agent = TodayTabAgent()
    key = "institutional_trend"
    live = agent.registry(tmp_path).live_version("today", key)
    created = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps(
            {"text": live["text"] + " Wait for the reviewer.", "rationale": "Metrics are not the only gate."}
        ),
        prompt_key=key,
    )
    shadow_id = created["created"][0]["id"]
    request = json.loads((tmp_path / ".tab_agent_reviews" / f"today-{shadow_id}.json").read_text(encoding="utf-8"))
    assert "diff" in request
    assert request["version_id"] == shadow_id

    def fake_score(_skill_dir, registry=None, since_ts=None):
        return {
            "versions": [_book(live["id"], "live", 0.2, -0.04), _book(shadow_id, "shadow", 0.9, -0.04, live["id"])],
            "comparisons": [
                {"version_id": shadow_id, "prompt_key": key, "edge_vs_live": "better_than_live", "auto_promote": False}
            ],
        }

    agent.score = fake_score  # type: ignore[method-assign]
    waiting = agent.govern(tmp_path)
    assert waiting["actions"] == []
    assert agent.registry(tmp_path).live_version("today", key)["id"] == live["id"]


def test_policy_thresholds_stay_inside_bounds(tmp_path: Path) -> None:
    from core.tab_agent_objective import apply_policy_update, liquidity_decision, load_objective

    stored = apply_policy_update(
        tmp_path,
        {"min_avg_dollar_volume": 1.0, "max_spread_pct": 0.5, "quality_threshold": 99},
    )
    assert stored["min_avg_dollar_volume"] == 5_000_000.0
    assert stored["max_spread_pct"] == 0.03
    assert stored["quality_threshold"] == 90.0
    assert stored["market_cap_penalty"] is False
    config = load_objective(tmp_path)
    assert liquidity_decision(1_000_000, 0.001, config=config) == "fail"
    assert liquidity_decision(30_000_000, 0.005, config=config) == "pass"
    assert config.max_names == 0


def test_cash_decision_scores_as_zero(tmp_path: Path) -> None:
    from core.tab_agent import score_prompt_versions

    versions = [{"id": "v1", "prompt_key": "institutional_trend", "status": "live", "parent": None}]
    attributions = [
        {"version_id": "v1", "ticker": "CASH", "day": "2026-06-01", "decision": "cash", "created_at": "2026-06-01"},
        {"version_id": "v1", "ticker": "AAPL", "day": "2026-06-02", "decision": "short", "created_at": "2026-06-02"},
    ]
    report = score_prompt_versions(versions=versions, packets=[], attributions=attributions, skill_dir=tmp_path)
    book = report["versions"][0]
    assert book["n"] == 1
    assert book["avg_realized_return_pct"] == 0.0


def test_digest_writes_markdown_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    from core.tab_agent_digest import write_daily_digest

    TodayTabAgent().registry(tmp_path)
    payload = write_daily_digest(tmp_path)
    assert (tmp_path / ".tab_agent_digest" / f"{payload['day']}.md").exists()
    assert (tmp_path / ".tab_agent_digest" / f"{payload['day']}.json").exists()
    text = (tmp_path / ".tab_agent_digest" / f"{payload['day']}.md").read_text(encoding="utf-8")
    assert "today" in text
    assert "No order" in text
