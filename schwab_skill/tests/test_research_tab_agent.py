"""Research quick-check packets and the research tab agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.decision_packet import load_packets
from core.research_quick_check import record_quick_check_packet, resolve_quick_check_decision
from core.research_tab_agent import ResearchTabAgent
from core.tab_agent_review import read_verdict


def _decision(**kwargs: object) -> dict:
    base = dict(
        ticker="AAPL",
        has_price_data=True,
        stage_2=True,
        vcp=True,
        signal_score=75,
        stop_pct=0.07,
    )
    base.update(kwargs)
    return resolve_quick_check_decision(**base)  # type: ignore[arg-type]


def test_builtin_rules_match_the_old_verdict_and_record_a_packet(tmp_path: Path) -> None:
    decision = _decision()
    assert decision["verdict"] == "STRONG SETUP"
    assert decision["prompt_version"] == "builtin"
    assert "No short" in decision["rationale"]
    when = datetime(2026, 8, 3, 15, tzinfo=timezone.utc)
    first = record_quick_check_packet(
        tmp_path, ticker="AAPL", price=100.0, stop_pct=0.07, decision=decision, created_at=when
    )
    second = record_quick_check_packet(
        tmp_path, ticker="aapl", price=101.0, stop_pct=0.07, decision=decision, created_at=when
    )
    assert first["recorded"] == 1
    assert second["skipped_duplicate"] == 1
    stored = load_packets(tmp_path)[0]
    assert stored["kind"] == "staged"
    assert stored["entry_price"] == 100.0
    assert stored["refs"]["verdict"] == "STRONG SETUP"
    assert stored["refs"]["prompt_version"] == "builtin"
    assert stored["refs"]["rationale"]
    assert stored["refs"]["stop_pct"] == 0.07
    assert stored["p_up_calibrated"] == pytest.approx(0.72)


def test_hold_off_scores_as_cash_and_a_long_uses_the_scale_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_RESEARCH_ENABLED", "true")
    agent = ResearchTabAgent()
    live = agent.registry(tmp_path).live_version("research", "verdict_rules")
    packets = [
        {
            "ticker": "AAPL",
            "created_at": "2026-06-02T15:00:00+00:00",
            "refs": {"source": "quick_check", "verdict": "HOLD OFF", "prompt_version": live["id"], "prompt_key": "verdict_rules"},
            "outcome": {"label": "win", "realized_return_pct": 12.0, "horizon_days": 20},
        },
        {
            "ticker": "MSFT",
            "created_at": "2026-06-03T15:00:00+00:00",
            "refs": {"source": "quick_check", "verdict": "STRONG SETUP", "prompt_version": live["id"], "prompt_key": "verdict_rules"},
            "outcome": {"label": "win", "realized_return_pct": 8.0, "horizon_days": 20, "exit_model": "scale_out"},
        },
    ]
    from core.decision_packet import overwrite_packets

    overwrite_packets(tmp_path, packets)
    score = agent.score(tmp_path)
    book = next(row for row in score["versions"] if row["version_id"] == live["id"])
    assert book["n"] == 2
    assert book["avg_realized_return_pct"] == 4.0


def test_enabled_prompt_can_raise_the_strong_setup_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_RESEARCH_ENABLED", "true")
    agent = ResearchTabAgent()
    live = agent.registry(tmp_path).live_version("research", "verdict_rules")
    revised = live["text"].replace("STRONG_SETUP_MIN_SCORE=60", "STRONG_SETUP_MIN_SCORE=80")
    created = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps({"text": revised, "rationale": "Raise the bar. Sample may be small."}),
        prompt_key="verdict_rules",
    )
    shadow_id = created["created"][0]["id"]
    verdict = read_verdict(tmp_path, "research", shadow_id)
    assert verdict is None
    request = json.loads((tmp_path / ".tab_agent_reviews" / f"research-{shadow_id}.json").read_text(encoding="utf-8"))
    assert "STRONG_SETUP_MIN_SCORE=80" in request["diff"]
    agent.approve(tmp_path, shadow_id)
    monkeypatch.setenv("TAB_AGENT_RESEARCH_ENABLED", "true")
    decision = resolve_quick_check_decision(
        ticker="AAPL",
        has_price_data=True,
        stage_2=True,
        vcp=True,
        signal_score=70,
        skill_dir=tmp_path,
    )
    assert decision["verdict"] == "WATCH"
    assert decision["prompt_version"] == shadow_id


def test_agent_off_keeps_the_builtin_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_RESEARCH_ENABLED", "false")
    decision = resolve_quick_check_decision(
        ticker="AAPL",
        has_price_data=True,
        stage_2=True,
        vcp=True,
        signal_score=70,
        skill_dir=tmp_path,
    )
    assert decision["verdict"] == "STRONG SETUP"
    assert decision["prompt_version"] == "builtin"


def test_out_of_bound_score_is_clamped(tmp_path: Path) -> None:
    from core.research_quick_check import min_score_from_text

    assert min_score_from_text("STRONG_SETUP_MIN_SCORE=5", tmp_path) == 40.0
    assert min_score_from_text("STRONG_SETUP_MIN_SCORE=99", tmp_path) == 90.0
