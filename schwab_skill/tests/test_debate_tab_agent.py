"""Debate tab agent: no name cap, cash is scored, review files are written."""

from __future__ import annotations

import json

from core.debate_committee import maybe_attach_committee
from core.debate_grade import attach_debate_grade
from core.debate_tab_agent import DebateTabAgent
from core.decision_packet import overwrite_packets
from tests.test_debate_grade import _passing_signal


def _payload() -> str:
    return json.dumps(
        {
            "seats": [
                {"name": "trend", "score": 80, "claim": "trend"},
                {"name": "setup", "score": 80, "claim": "setup"},
                {"name": "risk", "score": 70, "claim": "risk"},
                {"name": "skeptic", "score": 60, "claim": "skeptic"},
                {"name": "business", "score": None, "claim": "n/a"},
            ],
            "chair": "Long only.",
        }
    )


def test_quality_bar_replaces_the_name_cap(monkeypatch) -> None:
    monkeypatch.setattr("config.get_debate_committee_mode", lambda skill_dir=None: "shadow")
    monkeypatch.setattr("config.get_tab_agent_debate_enabled", lambda skill_dir=None: True)
    monkeypatch.setattr("config.get_tab_agent_debate_max_names", lambda skill_dir=None: 0)
    monkeypatch.setattr("config.get_debate_committee_max_per_scan", lambda skill_dir=None: 1)

    diagnostics: dict = {}
    first = _passing_signal()
    attach_debate_grade(first, diagnostics={}, skill_dir=None)
    maybe_attach_committee(first, diagnostics=diagnostics, skill_dir=None, complete=lambda _p: _payload(), force=True)
    second = _passing_signal(ticker="MSFT")
    attach_debate_grade(second, diagnostics={}, skill_dir=None)
    maybe_attach_committee(second, diagnostics=diagnostics, skill_dir=None, complete=lambda _p: _payload(), force=True)
    assert diagnostics["debate_committee_ran"] == 2
    assert second["debate_grade"].get("debate_score") is not None

    low = _passing_signal(ticker="LOW", signal_score=10, score_components={"pts_52w": 0, "pts_volume": 0})
    attach_debate_grade(low, diagnostics={}, skill_dir=None)
    if low["debate_grade"].get("qual_raw") is not None and low["debate_grade"]["qual_raw"] >= 60:
        low["debate_grade"]["qual_raw"] = 40
    maybe_attach_committee(low, diagnostics=diagnostics, skill_dir=None, complete=lambda _p: _payload(), force=True)
    assert diagnostics["debate_committee_below_quality"] == 1
    assert low["debate_grade"].get("debate_score") is None


def test_cash_pass_scores_zero_and_proposal_writes_a_review(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAB_AGENT_DEBATE_ENABLED", "true")
    agent = DebateTabAgent()
    reg = agent.registry(tmp_path)
    system = reg.live_version("debate", "system")
    day = "2026-06-02"
    packet = {
        "packet_id": "pkt1",
        "created_at": "2026-06-02T15:00:00+00:00",
        "ticker": "AAPL",
        "kind": "staged",
        "entry_price": 100.0,
        "outcome": {
            "label": "win",
            "realized_return_pct": 8.0,
            "horizon_days": 20,
            "exit_model": "scale_out",
        },
    }
    overwrite_packets(tmp_path, [packet])
    log = tmp_path / ".debate_committee_log.jsonl"
    rows = [
        {
            "ts": "2026-06-02T20:00:00+00:00",
            "as_of": day,
            "ticker": "AAPL",
            "debate_score": 40,
            "prompt_versions": {"system": system["id"], "round1": "builtin", "round2": "builtin"},
            "side": "long",
        },
        {
            "ts": "2026-06-03T20:00:00+00:00",
            "as_of": "2026-06-03",
            "ticker": "MSFT",
            "debate_score": 80,
            "prompt_versions": {"system": system["id"], "round1": "builtin", "round2": "builtin"},
            "side": "short",
        },
    ]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    # A second long endorsement on another day so the cash zero is visible next to a trade.
    extra_packet = dict(packet)
    extra_packet["packet_id"] = "pkt2"
    extra_packet["ticker"] = "NVDA"
    extra_packet["created_at"] = "2026-06-04T15:00:00+00:00"
    outcome = packet["outcome"]
    assert isinstance(outcome, dict)
    extra_packet["outcome"] = dict(outcome)
    overwrite_packets(tmp_path, [packet, extra_packet])
    with log.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "ts": "2026-06-04T20:00:00+00:00",
                    "as_of": "2026-06-04",
                    "ticker": "NVDA",
                    "debate_score": 80,
                    "prompt_versions": {"system": system["id"]},
                    "side": "long",
                }
            )
            + "\n"
        )
    score = agent.score(tmp_path)
    book = next(row for row in score["versions"] if row["version_id"] == system["id"])
    assert book["n"] == 2
    assert book["avg_realized_return_pct"] == 4.0

    created = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps(
            {
                "text": system["text"] + " Stay in cash when the regime is weak. Never short.",
                "rationale": "Cash avoids drawdown. Sample may be small.",
                "quality_threshold": 99,
            }
        ),
        prompt_key="system",
    )
    version_id = created["created"][0]["id"]
    request = json.loads((tmp_path / ".tab_agent_reviews" / f"debate-{version_id}.json").read_text(encoding="utf-8"))
    assert "Never short" in request["diff"] or "Stay in cash" in request["diff"]
    assert request["sample_size"] == book["n"]
    policy = json.loads((tmp_path / ".tab_agent_policy.json").read_text(encoding="utf-8"))
    assert policy["quality_threshold"] == 90.0
    assert policy["max_names"] == 0
    assert "never recommend a short" in system["text"].lower()
