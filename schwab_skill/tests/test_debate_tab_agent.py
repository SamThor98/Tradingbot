"""Debate committee agent: grading, logging, and the pending lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.debate_committee import load_debate_log, review_signals
from core.debate_grade import grade_debate_log
from core.debate_tab_agent import DebateTabAgent
from core.decision_packet import build_packet, record_packet
from core.tab_agent import get_tab_agent


def _packet(ticker: str, day: str, label: str, ret: float, packet_id: str, horizon: int = 10) -> dict:
    packet = build_packet(
        ticker=ticker,
        kind="staged",
        signal={"price": 100.0},
        created_at=__import__("datetime").datetime.fromisoformat(f"{day}T15:00:00+00:00"),
    ).model_dump(mode="json")
    packet["packet_id"] = packet_id
    packet["outcome"] = {"label": label, "realized_return_pct": ret, "horizon_days": horizon}
    return packet


def _event(ticker: str, day: str, decision: str, conviction: float, version: str, packet_id: str | None = None) -> dict:
    return {
        "ticker": ticker,
        "date": day,
        "ts": f"{day}T15:00:00+00:00",
        "decision": decision,
        "conviction": conviction,
        "packet_id": packet_id,
        "prompt_versions": {"chair": version, "system": version},
    }


def test_small_sample_and_pass_counterfactual_stay_inconclusive() -> None:
    versions = [
        {"id": "chair-live", "prompt_key": "chair", "status": "live", "parent": None},
    ]
    packets = [
        _packet("AAA", "2026-09-01", "win", 3.0, "p-aaa"),
        _packet("BBB", "2026-09-02", "win", 4.0, "p-bbb"),
    ]
    events = [
        _event("AAA", "2026-09-01", "approve", 80, "chair-live", "p-aaa"),
        _event("BBB", "2026-09-02", "pass", 20, "chair-live", "p-bbb"),
    ]
    report = grade_debate_log(versions=versions, packets=packets, events=events, min_samples=30, horizon_days=10)
    assert report["auto_promote"] is False
    stat = report["versions"][0]
    assert stat["n"] == 1
    assert stat["sufficient"] is False
    assert stat["win_rate"] == 1.0
    assert report["calibration"]["verdict"] == "insufficient_sample"
    assert report["passes"]["n"] == 1
    assert report["passes"]["win_rate"] == 1.0
    assert report["passes"]["verdict"] == "insufficient_sample"
    assert report["join"]["packet_id"] == 2


def test_ticker_day_join_and_unmatched() -> None:
    versions = [{"id": "chair-live", "prompt_key": "chair", "status": "live", "parent": None}]
    packets = [_packet("AAA", "2026-09-01", "loss", -2.0, "p-aaa")]
    events = [
        {
            "ticker": "AAA",
            "date": "2026-09-01",
            "decision": "approve",
            "conviction": 55,
            "prompt_versions": {"chair": "chair-live"},
        },
        {
            "ticker": "ZZZ",
            "date": "2026-09-01",
            "decision": "approve",
            "conviction": 90,
            "prompt_versions": {"chair": "chair-live"},
        },
    ]
    report = grade_debate_log(versions=versions, packets=packets, events=events, min_samples=1, horizon_days=10)
    assert report["join"]["ticker_day"] == 1
    assert report["join"]["unmatched"] == 1
    assert report["versions"][0]["n"] == 1
    assert report["versions"][0]["win_rate"] == 0.0


def test_other_horizon_is_not_labeled() -> None:
    versions = [{"id": "chair-live", "prompt_key": "chair", "status": "live", "parent": None}]
    packets = [_packet("AAA", "2026-09-01", "win", 5.0, "p-aaa", horizon=20)]
    events = [_event("AAA", "2026-09-01", "approve", 70, "chair-live", "p-aaa")]
    report = grade_debate_log(versions=versions, packets=packets, events=events, min_samples=1, horizon_days=10)
    assert report["versions"][0]["n"] == 0
    assert report["join"]["unlabeled"] == 1


def test_calibration_bin_when_sample_is_large_enough() -> None:
    versions = [{"id": "c", "prompt_key": "chair", "status": "live", "parent": None}]
    packets = []
    events = []
    for i in range(4):
        pid = f"p{i}"
        packets.append(_packet(f"T{i}", f"2026-09-{i + 1:02d}", "win", 2.0, pid))
        events.append(_event(f"T{i}", f"2026-09-{i + 1:02d}", "approve", 90, "c", pid))
    report = grade_debate_log(versions=versions, packets=packets, events=events, min_samples=4, horizon_days=10)
    cal = report["calibration"]
    assert cal["verdict"] == "readable"
    assert cal["n"] == 4
    assert cal["ece"] == 0.1
    assert cal["bins"][0]["lo"] == 70.0
    assert cal["bins"][0]["win_rate"] == 1.0


def test_review_is_off_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_DEBATE_ENABLED", "false")
    calls = {"n": 0}

    def _boom(*_args, **_kwargs):
        calls["n"] += 1
        return ""

    out = review_signals(tmp_path, [{"ticker": "AAPL", "price": 10}], llm=_boom)
    assert out["ran"] is False
    assert out["calls"] == 0
    assert calls["n"] == 0
    assert not (tmp_path / ".debate_committee_log.jsonl").exists()


def _chair_json(decision: str, conviction: int, reason: str) -> str:
    return json.dumps({"decision": decision, "conviction": conviction, "reason": reason})


def test_review_logs_packet_id_and_skips_shadow_llm_when_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_DEBATE_ENABLED", "true")
    monkeypatch.setenv("TAB_AGENT_DEBATE_SHADOW_ENABLED", "false")
    packet = build_packet(ticker="AAPL", signal={"price": 180})
    record_packet(tmp_path, packet)
    calls = {"n": 0}

    def llm(_system: str, user: str) -> str:
        calls["n"] += 1
        if "JSON object" in user:
            return _chair_json("approve", 72, "live call")
        return "round notes"

    agent = DebateTabAgent()
    proposed = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps(
            {
                "text": "Chair round for {ticker}. A longer revised chair prompt that still returns JSON. {context}",
                "rationale": "Recent approve calls lost. Sample is still small.",
            }
        ),
        prompt_key="chair",
    )
    assert proposed["auto_promote"] is False
    assert proposed["created"][0]["status"] == "pending"
    live_row = agent.registry(tmp_path).live_version("debate", "chair")
    agent.shadow(tmp_path, proposed["created"][0]["id"])
    out = review_signals(tmp_path, [{"ticker": "AAPL", "price": 180, "_filter_status": "kept"}], llm=llm)
    assert out["logged"] == 1
    assert calls["n"] == 3
    rows = load_debate_log(tmp_path)
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["date"]
    assert rows[0]["packet_id"] == packet.packet_id
    assert rows[0]["prompt_versions"]["chair"] == live_row["id"]
    assert rows[0]["decision"] == "approve"
    assert rows[0]["shadow_decision"] is None
    assert agent.registry(tmp_path).live_version("debate", "chair")["text"] == live_row["text"]


def test_shadow_records_without_replacing_the_live_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_DEBATE_ENABLED", "true")
    monkeypatch.setenv("TAB_AGENT_DEBATE_SHADOW_ENABLED", "true")
    agent = DebateTabAgent()
    agent.status(tmp_path)
    created = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps(
            {
                "text": "Chair round for {ticker}. Shadow candidate prompt that still returns JSON. {context}",
                "rationale": "Wrong passes would have won. Not enough samples to trust this.",
            }
        ),
        prompt_key="chair",
    )
    agent.shadow(tmp_path, created["created"][0]["id"])
    calls = {"n": 0}

    def llm(_system: str, user: str) -> str:
        calls["n"] += 1
        if calls["n"] >= 4:
            return _chair_json("pass", 15, "shadow")
        if "JSON object" in user:
            return _chair_json("approve", 66, "live")
        return "notes"

    out = review_signals(tmp_path, [{"ticker": "MSFT", "price": 400}], llm=llm)
    assert out["calls"] == 4
    row = load_debate_log(tmp_path)[0]
    assert row["decision"] == "approve"
    assert row["shadow_decision"] == "pass"
    assert row["packet_id"] is None


def test_approve_and_rollback(tmp_path: Path) -> None:
    agent = get_tab_agent("debate")
    assert isinstance(agent, DebateTabAgent)
    live = agent.registry(tmp_path).live_version("debate", "system")
    original = live["text"]
    revised = original + " Weigh missed winners explicitly."
    created = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps({"text": revised, "rationale": "Wrong calls were passes that won."}),
        prompt_key="system",
    )
    version_id = created["created"][0]["id"]
    assert agent.registry(tmp_path).live_version("debate", "system")["id"] == live["id"]
    agent.approve(tmp_path, version_id)
    assert agent.registry(tmp_path).live_version("debate", "system")["text"] == revised
    agent.rollback(tmp_path, live["id"])
    assert agent.registry(tmp_path).live_version("debate", "system")["text"] == original


def test_scan_pass_invokes_debate_review_which_stays_idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_DEBATE_ENABLED", "false")
    from core import outcome_backfill

    seen = {"n": 0}

    def spy(skill_dir, signals, **_kwargs):
        seen["n"] = len(signals or [])
        return {"ran": False, "calls": 0}

    monkeypatch.setattr("core.debate_committee.review_signals", spy)
    outcome_backfill.run_scan_outcome_pass(
        tmp_path,
        [{"ticker": "NVDA", "price": 100}],
        history_provider=lambda _t: None,
        force_backfill=True,
    )
    assert seen["n"] == 1
    assert not (tmp_path / ".debate_committee_log.jsonl").exists()
