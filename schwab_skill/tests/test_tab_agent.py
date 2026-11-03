"""Today tab agent: scoring honesty, proposal lifecycle, and rollback."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tab_agent import (
    TabAgentError,
    score_prompt_versions,
    wilson_ci,
)
from core.today_tab_agent import TodayTabAgent, parse_prompt_proposal, resolve_for_scan
from engine_analysis import AGENT_SYSTEMS


def _version(vid: str, key: str, status: str, parent: str | None = None) -> dict:
    return {
        "id": vid,
        "prompt_key": key,
        "text": "prompt",
        "parent": parent,
        "rationale": "test",
        "created_at": "2026-09-01T00:00:00+00:00",
        "status": status,
    }


def _packet(ticker: str, day: str, label: str, ret: float) -> dict:
    return {
        "ticker": ticker,
        "created_at": f"{day}T15:00:00+00:00",
        "outcome": {"label": label, "realized_return_pct": ret, "horizon_days": 10},
    }


def _use(vid: str, ticker: str, day: str) -> dict:
    return {"version_id": vid, "prompt_key": "institutional_trend", "ticker": ticker, "day": day, "role": "live"}


def test_wilson_ci_stays_inside_unit_interval() -> None:
    low, high = wilson_ci(36, 40) or (None, None)
    assert low is not None and high is not None
    assert 0.0 <= low < 0.9 < high <= 1.0
    assert wilson_ci(0, 0) is None


def test_small_sample_is_not_a_winner() -> None:
    versions = [
        _version("live1", "institutional_trend", "live"),
        _version("cand1", "institutional_trend", "pending", parent="live1"),
    ]
    packets = [
        _packet("AAA", "2026-09-01", "win", 4.0),
        _packet("BBB", "2026-09-01", "win", 3.0),
        _packet("CCC", "2026-09-01", "win", 2.0),
        _packet("DDD", "2026-09-02", "loss", -2.0),
    ]
    attributions = [
        _use("cand1", "AAA", "2026-09-01"),
        _use("cand1", "BBB", "2026-09-01"),
        _use("cand1", "CCC", "2026-09-01"),
        _use("live1", "DDD", "2026-09-02"),
    ]
    report = score_prompt_versions(
        versions=versions,
        packets=packets,
        attributions=attributions,
        min_samples=30,
        horizon_days=10,
    )
    assert report["auto_promote"] is False
    cand = next(row for row in report["versions"] if row["version_id"] == "cand1")
    assert cand["n"] == 3
    assert cand["sufficient"] is False
    assert cand["win_rate"] == 1.0
    comparison = report["comparisons"][0]
    assert comparison["edge_vs_live"] == "insufficient_sample"
    assert comparison["auto_promote"] is False


def test_large_separated_sample_is_flagged_but_not_promoted() -> None:
    versions = [
        _version("live1", "institutional_trend", "live"),
        _version("cand1", "institutional_trend", "shadow", parent="live1"),
    ]
    packets = []
    attributions = []
    for i in range(40):
        day = f"2026-08-{(i % 28) + 1:02d}"
        packets.append(_packet(f"L{i}", day, "loss" if i < 24 else "win", -1.5 if i < 24 else 1.0))
        attributions.append(_use("live1", f"L{i}", day))
        packets.append(_packet(f"C{i}", day, "win" if i < 36 else "loss", 2.5 if i < 36 else -0.4))
        attributions.append(_use("cand1", f"C{i}", day))
    report = score_prompt_versions(
        versions=versions,
        packets=packets,
        attributions=attributions,
        events=[
            {
                "ticker": "C0",
                "ts": "2026-08-01T15:00:00+00:00",
                "reason": "meta_policy_suppressed",
                "forward_return_pct": -2.0,
            },
            {
                "ticker": "C1",
                "ts": "2026-08-02T15:00:00+00:00",
                "reason": "quality_filtered",
                "forward_return_pct": 3.0,
            },
        ],
        min_samples=30,
        horizon_days=10,
    )
    cand = next(row for row in report["versions"] if row["version_id"] == "cand1")
    assert cand["sufficient"] is True
    assert cand["n"] == 40
    assert cand["suppress_n"] == 2
    assert cand["suppress_precision"] == 0.5
    assert cand["suppress_sufficient"] is False
    comparison = next(row for row in report["comparisons"] if row["version_id"] == "cand1")
    assert comparison["edge_vs_live"] == "better_than_live"
    assert comparison["auto_promote"] is False
    assert report["auto_promote"] is False


def test_other_horizon_is_excluded() -> None:
    packet = _packet("AAA", "2026-09-01", "win", 5.0)
    packet["outcome"]["horizon_days"] = 20
    report = score_prompt_versions(
        versions=[_version("live1", "institutional_trend", "live")],
        packets=[packet],
        attributions=[_use("live1", "AAA", "2026-09-01")],
        min_samples=1,
        horizon_days=10,
    )
    assert report["versions"][0]["n"] == 0


def _proposal_text() -> str:
    return (
        '{"text": "You are an Institutional Trend-Follower who skips late-stage extensions '
        'and still returns one JSON object with conviction_score.", "rationale": "Misses were extended names."}'
    )


def test_propose_stays_pending_until_approve_and_rollback_restores_parent(tmp_path: Path) -> None:
    agent = TodayTabAgent()
    before = agent.status(tmp_path)
    live = before["prompts"][0]["live"]
    key = live["prompt_key"]
    original = live["text"]
    assert original == AGENT_SYSTEMS[key]

    proposed = agent.propose(tmp_path, llm=lambda _system, _user: _proposal_text(), prompt_key=key)
    assert proposed["auto_promote"] is False
    assert len(proposed["created"]) == 1
    pending = proposed["created"][0]
    assert pending["status"] == "pending"
    assert pending["parent"] == live["id"]
    assert agent.registry(tmp_path).live_version(agent.tab_id, key)["text"] == original

    rejected = agent.reject(tmp_path, pending["id"])
    assert rejected["status"] == "rejected"
    assert agent.registry(tmp_path).live_version(agent.tab_id, key)["id"] == live["id"]

    again = agent.propose(tmp_path, llm=lambda _system, _user: _proposal_text(), prompt_key=key)
    candidate = again["created"][0]
    shadowed = agent.shadow(tmp_path, candidate["id"])
    assert shadowed["status"] == "shadow"
    assert agent.registry(tmp_path).live_version(agent.tab_id, key)["id"] == live["id"]

    approved = agent.approve(tmp_path, candidate["id"])
    assert approved["status"] == "live"
    assert approved["text"] != original
    retired = agent.registry(tmp_path).get(agent.tab_id, live["id"])
    assert retired is not None and retired["status"] == "retired"

    restored = agent.rollback(tmp_path, live["id"])
    assert restored["status"] == "live"
    assert restored["text"] == original
    assert agent.registry(tmp_path).get(agent.tab_id, candidate["id"])["status"] == "retired"


def test_illegal_transitions(tmp_path: Path) -> None:
    agent = TodayTabAgent()
    status = agent.status(tmp_path)
    live_id = status["prompts"][0]["live"]["id"]
    assert agent.rollback(tmp_path, live_id)["id"] == live_id
    with pytest.raises(TabAgentError):
        agent.reject(tmp_path, live_id)
    with pytest.raises(TabAgentError):
        agent.approve(tmp_path, "missing-version")
    with pytest.raises(TabAgentError):
        agent.rollback(tmp_path, "missing-version")


def test_parse_prompt_proposal_rejects_short_text() -> None:
    assert parse_prompt_proposal('{"text": "too short", "rationale": "x"}') is None
    parsed = parse_prompt_proposal(_proposal_text())
    assert parsed is not None
    assert "Institutional" in parsed["text"]
    assert parsed["rationale"]


def test_resolve_for_scan_is_builtin_until_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "false")
    live, shadow, bindings = resolve_for_scan(tmp_path)
    assert live == dict(AGENT_SYSTEMS)
    assert shadow == {}
    assert bindings == []
    assert not (tmp_path / ".tab_agent_registry.json").exists()

    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "true")
    monkeypatch.setenv("TAB_AGENT_SHADOW_ENABLED", "false")
    live, shadow, bindings = resolve_for_scan(tmp_path)
    assert live["institutional_trend"] == AGENT_SYSTEMS["institutional_trend"]
    assert shadow == {}
    assert len(bindings) == 3
    assert (tmp_path / ".tab_agent_registry.json").exists()


def test_disabled_scan_path_does_not_call_shadow_llm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TAB_AGENT_TODAY_ENABLED", "false")
    calls = {"n": 0}

    def _boom(*_args, **_kwargs):
        calls["n"] += 1
        return ""

    monkeypatch.setattr("engine_analysis._call_llm", _boom)
    from engine_analysis import _persona_systems, _shadow_persona_votes

    live, shadow, bindings = _persona_systems(tmp_path)
    assert live["mean_reversion"] == AGENT_SYSTEMS["mean_reversion"]
    assert _shadow_persona_votes("prompt", shadow, {}) == []
    assert bindings == []
    assert calls["n"] == 0


def test_today_panel_is_on_the_today_tab() -> None:
    html = Path(__file__).resolve().parent.parent.joinpath("webapp", "static", "index.html").read_text(encoding="utf-8")
    assert 'id="todayTabAgent"' in html
    assert "todayTabAgent.js" in html
