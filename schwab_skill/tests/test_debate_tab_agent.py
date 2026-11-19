"""Debate tab agent: no name cap, cash is scored, review files are written."""

from __future__ import annotations

import json

import pytest

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
    round1 = reg.live_version("debate", "round1")
    round2 = reg.live_version("debate", "round2")
    versions = {"system": system["id"], "round1": round1["id"], "round2": round2["id"]}
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
            "prompt_versions": versions,
            "side": "long",
        },
        {
            "ts": "2026-06-03T20:00:00+00:00",
            "as_of": "2026-06-03",
            "ticker": "MSFT",
            "debate_score": 80,
            "prompt_versions": versions,
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
                    "prompt_versions": versions,
                    "side": "long",
                }
            )
            + "\n"
        )
    score = agent.score(tmp_path)
    live_rows = [row for row in score["versions"] if row.get("status") == "live"]
    assert len(live_rows) == 1
    book = live_rows[0]
    assert book["prompt_key"] == "combination"
    assert str(book["version_id"]).startswith("combo:")
    assert book["prompt_versions"] == versions
    assert book["n"] == 2
    assert book["n_longs"] == 1
    assert book["win_rate"] == 1.0
    assert book["avg_realized_return_pct"] == 4.0
    status = agent.status(tmp_path)
    assert len(status["prompts"]) == 1
    assert status["prompts"][0]["prompt_key"] == "combination"
    assert status["prompts"][0]["live"]["id"] == book["version_id"]
    from core.tab_agent_digest import write_daily_digest

    payload = write_daily_digest(tmp_path)
    debate = next(tab for tab in payload["tabs"] if tab["tab_id"] == "debate")
    assert len(debate["live"]) == 1
    assert debate["live"][0]["prompt_key"] == "combination"
    assert debate["live"][0]["n_longs"] == 1
    assert debate["live"][0]["win_rate"] == 1.0
    text = (tmp_path / ".tab_agent_digest" / f"{payload['day']}.md").read_text(encoding="utf-8")
    assert "longs=1" in text
    assert "win_rate=1.0" in text

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


def test_legacy_debate_copies_score_as_one_combination(tmp_path) -> None:
    from core.tab_agent_replay import append_sample

    agent = DebateTabAgent()
    reg = agent.registry(tmp_path)
    ids = {key: reg.live_version("debate", key)["id"] for key in ("system", "round1", "round2")}
    for key, version_id in ids.items():
        append_sample(
            tmp_path,
            {
                "source": "replay",
                "tab_id": "debate",
                "prompt_key": key,
                "version_id": version_id,
                "ticker": "AAA",
                "as_of": "2026-06-01",
                "decision": "long",
                "realized_return_pct": 8.0,
                "scored": True,
            },
        )
    book = next(row for row in agent.score(tmp_path)["versions"] if row["status"] == "live")
    assert book["n"] == 1
    assert book["n_replay"] == 1
    assert book["avg_realized_return_pct"] == 8.0


def test_shadow_debate_combination_promotes_together(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("core.debate_tab_agent.compare_books", lambda *_a, **_k: "better_than_live")
    monkeypatch.setattr("core.tab_agent_review.review_gate", lambda *_a, **_k: "approved")
    from core.tab_agent_governor import govern_agent

    agent = DebateTabAgent()
    reg = agent.registry(tmp_path)
    before = {}
    for key in ("system", "round1"):
        live = reg.live_version("debate", key)
        before[key] = live["id"]
        reg.add_version(
            "debate",
            prompt_key=key,
            text=str(live["text"]) + " Stay in cash when the regime is weak.",
            parent=str(live["id"]),
            rationale="Revise the combination together.",
            status="shadow",
        )
    status = agent.status(tmp_path)
    assert len(status["prompts"]) == 1
    assert status["prompts"][0]["shadow"]["prompt_versions"]["system"] != before["system"]
    result = govern_agent(agent, tmp_path)
    assert any(
        row.get("action") == "auto_promote" and row.get("prompt_key") == "combination" for row in result["actions"]
    )
    assert "Stay in cash" in reg.live_version("debate", "system")["text"]
    assert "Stay in cash" in reg.live_version("debate", "round1")["text"]
    assert "Stay in cash" not in reg.live_version("debate", "round2")["text"]


def test_debate_selection_uses_the_same_day_scan_pool(tmp_path, monkeypatch) -> None:
    """Debate selection is the cache-walked long minus that day's scan rows, not n=0."""
    import pandas as pd

    from core.tab_agent import scan_leg
    from core.tab_agent_book import ROUND_TRIP_COST, _Calendar, _compound, _price_lookup, _resolve_leg

    cache = tmp_path / "prices"
    cache.mkdir()
    index = pd.bdate_range("2026-06-01", periods=40)
    held = [100.0 + step * 0.35 for step in range(len(index))]
    aaa = pd.DataFrame(
        {
            "open": [100.0] * len(index),
            "high": [price + 1.0 for price in held],
            "low": [max(price - 0.5, 91.0) for price in held],
            "close": held,
            "volume": [1_000_000.0] * len(index),
        },
        index=index,
    )
    ran = [100.0 * (1.04**step) for step in range(len(index))]
    bbb = pd.DataFrame(
        {
            "open": [100.0] * len(index),
            "high": [price + 1.0 for price in ran],
            "low": [90.0] * len(index),
            "close": ran,
            "volume": [1_000_000.0] * len(index),
        },
        index=index,
    )
    aaa.to_csv(cache / "AAA.csv", index_label="date")
    bbb.to_csv(cache / "BBB.csv", index_label="date")
    monkeypatch.setenv("BACKTEST_PRICE_CACHE_DIR", str(cache))

    agent = DebateTabAgent()
    reg = agent.registry(tmp_path)
    versions = {key: reg.live_version("debate", key)["id"] for key in ("system", "round1", "round2")}
    packets = []
    events = []
    for day in ("2026-06-01", "2026-06-08"):
        packets.append(
            {
                "ticker": "AAA",
                "created_at": f"{day}T15:00:00+00:00",
                "side": "long",
                "stop_price": 80.0,
                "outcome": {
                    "label": "win",
                    "realized_return_pct": 40.0,
                    "horizon_days": 20,
                    "exit_model": "scale_out",
                },
            }
        )
        packets.append(
            {
                "ticker": "BBB",
                "created_at": f"{day}T15:00:00+00:00",
                "side": "long",
                "stop_price": 110.0,
                "outcome": {
                    "label": "win",
                    "realized_return_pct": 50.0,
                    "horizon_days": 20,
                    "exit_model": "scale_out",
                },
            }
        )
        events.append(
            {
                "ts": f"{day}T20:00:00+00:00",
                "as_of": day,
                "ticker": "AAA",
                "debate_score": 80,
                "prompt_versions": versions,
                "side": "long",
            }
        )
    overwrite_packets(tmp_path, packets)
    (tmp_path / ".debate_committee_log.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events),
        encoding="utf-8",
    )
    book = next(row for row in agent.score(tmp_path)["versions"] if row.get("status") == "live")
    lookup = _price_lookup(tmp_path)
    assert lookup is not None
    calendar = _Calendar(None)

    def net(packet: dict) -> float:
        path, source = _resolve_leg(
            scan_leg(packet, horizon_days=20),
            cost=ROUND_TRIP_COST,
            apply_cost=True,
            calendar=calendar,
            lookup=lookup,
        )
        assert source == "cache"
        return _compound([ret for session, ret in path if calendar.is_session(session)])

    values = []
    for offset in (0, 2):
        chosen = net(packets[offset])
        peer = net(packets[offset + 1])
        values.append(chosen - (chosen + peer) / 2.0)
    expected = sum(values) / len(values)
    stored_mix = sum(net(packets[offset]) - 0.45 for offset in (0, 2)) / 2.0
    selection = book["selection_vs_pool"]
    assert book["n_longs"] == 2
    assert book["avg_realized_return_pct"] == 40.0
    assert selection["n"] == 2
    assert selection["n_weeks"] == 2
    assert selection["ci_low"] is not None
    assert selection["ci_high"] is not None
    assert selection["mean"] == pytest.approx(expected, abs=1e-6)
    assert selection["shrunk"] == pytest.approx(expected * 2.0 / 12.0, abs=1e-6)
    assert abs(selection["mean"] - stored_mix) > 0.2
