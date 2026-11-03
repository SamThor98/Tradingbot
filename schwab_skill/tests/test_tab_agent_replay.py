"""Historical replay warm-start. No orders."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from core import decision_packet
from core.research_tab_agent import ResearchTabAgent
from core.tab_agent_digest import _markdown, build_digest
from core.tab_agent_governor import govern_agent
from core.tab_agent_objective import ObjectiveConfig, compare_books, evaluate_book
from core.tab_agent_replay import (
    append_sample,
    collect_cases,
    load_samples,
    parse_decision,
    pit_payload,
    replay_path,
    run_replay,
    scale_out_return,
    truncate_history,
)
from scripts.tab_agent_replay import main


def _config() -> ObjectiveConfig:
    return ObjectiveConfig(min_samples=5, min_live_samples=10, replay_weight=0.5)


def _frame() -> pd.DataFrame:
    index = pd.bdate_range("2026-06-01", periods=40)
    close = [100.0 + i for i in range(40)]
    for pos in range(21, 40):
        close[pos] = 5.0
    return pd.DataFrame(
        {"close": close, "high": [price + 1 for price in close], "low": [price - 1 for price in close]},
        index=index,
    )


def test_replay_weight_pulls_the_mean_toward_live_rows() -> None:
    weighted = evaluate_book(
        [10.0, 10.0, 0.0],
        ["2026-01-01", "2026-01-02", "2026-01-03"],
        config=_config(),
        weights=[1.0, 1.0, 0.5],
        sources=["live", "live", "replay"],
    )
    assert weighted["n"] == 3
    assert weighted["n_live"] == 2
    assert weighted["n_replay"] == 1
    assert weighted["avg_realized_return_pct"] == 8.0


def test_promotion_requires_live_samples_even_when_replay_fills_the_book() -> None:
    config = _config()
    live = evaluate_book([1.0] * 12, ["2026-01-01"] * 12, config=config)
    candidate = evaluate_book(
        [0.1] * 4 + [20.0] * 20,
        ["2026-02-01"] * 24,
        config=config,
        weights=[1.0] * 4 + [0.5] * 20,
        sources=["live"] * 4 + ["replay"] * 20,
    )
    assert candidate["n"] >= config.min_samples
    assert candidate["n_live"] == 4
    assert compare_books(candidate, live, config=config) == "insufficient_live_sample"


def test_pit_payload_drops_outcomes_and_future_fields() -> None:
    payload = pit_payload(
        {
            "ticker": "AAPL",
            "price": 100,
            "stage_2": True,
            "outcome": {"realized_return_pct": 40},
            "future_return": 40,
            "refs": {"stop_pct": 0.08, "realized_return_pct": 40},
        },
        "2026-06-01",
    )
    assert payload["ticker"] == "AAPL"
    assert payload["price"] == 100
    assert payload["stop_pct"] == 0.08
    assert payload["as_of"] == "2026-06-01"
    assert "outcome" not in payload
    assert "future_return" not in payload
    assert "realized_return_pct" not in payload
    assert parse_decision("STRONG SETUP", tab="research", quality_threshold=60) == "long"
    assert parse_decision("HOLD OFF", tab="today", quality_threshold=60) == "cash"
    assert parse_decision("debate_score: 72", tab="debate", quality_threshold=60) == "long"
    assert parse_decision("score: 40", tab="debate", quality_threshold=60) == "cash"
    assert parse_decision("Buy the put", tab="today", quality_threshold=60) is None


def test_label_uses_only_the_horizon_window(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_PACKET_HORIZON_DAYS", "20")
    created = datetime(2026, 6, 1, tzinfo=timezone.utc)
    frame = _frame()
    window = truncate_history(frame, created, 20)
    assert window is not None
    assert len(window) == 21
    assert 5.0 not in window["close"].tolist()
    case = {"ticker": "AAPL", "created_at": created, "entry_price": 100.0, "stop_pct": 0.08}
    labeled = scale_out_return(case, frame, skill_dir=None, now=datetime(2026, 11, 3, tzinfo=timezone.utc))
    assert labeled is not None
    assert labeled["realized_return_pct"] > 0
    assert labeled["exit_model"] == "scale_out"


def test_collects_matured_packets_and_scan_rows(tmp_path) -> None:
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "OLD", "price": 10, "stop_pct": 0.08}],
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "NEW", "price": 10, "stop_pct": 0.08}],
        created_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    db_path = tmp_path / "scans.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE scan_results (id INTEGER PRIMARY KEY, user_id TEXT, ticker TEXT, payload_json TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO scan_results (user_id, ticker, payload_json, created_at) VALUES (?, ?, ?, ?)",
        ("local", "SCAN", json.dumps({"ticker": "SCAN", "price": 20, "stop_pct": 0.08}), "2026-07-01T15:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    cases = collect_cases(
        tmp_path,
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        now=now,
        horizon_days=20,
        db_path=db_path,
    )
    tickers = [case["ticker"] for case in cases]
    assert tickers == ["OLD", "SCAN"]


def test_dry_run_estimates_cost_without_calls_or_writes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAB_AGENT_REPLAY_USD_PER_CALL", "0.02")
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "OLD", "price": 100, "stop_pct": 0.08, "outcome": {"realized_return_pct": 99}}],
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    calls: list[str] = []
    report = run_replay(
        tmp_path,
        tab="today",
        since="2026-01-01",
        dry_run=True,
        max_llm_calls=1,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        db_path=tmp_path / "missing.db",
        complete=lambda _system, user: calls.append(user) or "STRONG SETUP",
        versions=[{"id": "live-1", "prompt_key": "verdict", "text": "rules", "status": "live"}],
    )
    assert report["estimated_llm_calls"] == 1
    assert report["estimated_cost_usd"] == 0.02
    assert report["calls_under_cap"] == 1
    assert report["llm_calls"] == 0
    assert calls == []
    assert not replay_path(tmp_path).exists()
    assert "model may already know" in report["caveat"]


def test_max_llm_calls_caps_the_run_and_tags_replay(tmp_path) -> None:
    for offset, ticker in enumerate(("AAA", "BBB", "CCC")):
        decision_packet.record_scan_packets(
            tmp_path,
            [{"ticker": ticker, "price": 100, "stop_pct": 0.08, "future_return": 50}],
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=offset),
        )
    calls: list[str] = []

    def _complete(_system: str, user: str) -> str:
        calls.append(user)
        return "STRONG SETUP"

    report = run_replay(
        tmp_path,
        tab="today",
        since="2026-01-01",
        max_llm_calls=2,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        db_path=tmp_path / "missing.db",
        complete=_complete,
        history_provider=lambda _ticker: _frame(),
        versions=[{"id": "live-1", "prompt_key": "verdict", "text": "rules", "status": "live"}],
    )
    assert report["estimated_llm_calls"] == 3
    assert report["truncated"] is True
    assert report["stored"] == 2
    assert len(calls) == 2
    assert "future_return" not in calls[0]
    rows = load_samples(tmp_path)
    assert len(rows) == 2
    assert {row["source"] for row in rows} == {"replay"}
    assert all(row["exit_model"] == "scale_out" for row in rows)


def test_research_score_and_governor_keep_replay_below_the_live_floor(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAB_AGENT_MIN_SAMPLES", "10")
    monkeypatch.setenv("TAB_AGENT_MIN_LIVE_SAMPLES", "10")
    monkeypatch.setenv("TAB_AGENT_REPLAY_WEIGHT", "0.5")
    monkeypatch.setenv("DECISION_PACKET_HORIZON_DAYS", "20")
    agent = ResearchTabAgent()
    registry = agent.registry(tmp_path)
    live = registry.live_version("research", "verdict_rules")
    shadow = registry.add_version(
        "research",
        prompt_key="verdict_rules",
        text="shadow rules",
        parent=str(live["id"]),
        rationale="replay probe",
        status="shadow",
    )
    packets = []
    for index in range(12):
        packets.append(
            {
                "ticker": f"L{index}",
                "created_at": f"2026-01-{index + 1:02d}T15:00:00+00:00",
                "setup_type": "quick_check",
                "side": "long",
                "refs": {"source": "quick_check", "prompt_version": live["id"], "verdict": "STRONG SETUP"},
                "outcome": {"label": "win", "horizon_days": 20, "realized_return_pct": 1.0, "exit_model": "scale_out"},
            }
        )
    for index in range(4):
        packets.append(
            {
                "ticker": f"S{index}",
                "created_at": f"2026-02-{index + 1:02d}T15:00:00+00:00",
                "setup_type": "quick_check",
                "side": "long",
                "refs": {
                    "source": "quick_check",
                    "shadow_prompt_version": shadow["id"],
                    "shadow_verdict": "STRONG SETUP",
                },
                "outcome": {"label": "win", "horizon_days": 20, "realized_return_pct": 1.0, "exit_model": "scale_out"},
            }
        )
    (tmp_path / "decision_packets.json").write_text(json.dumps({"packets": packets}), encoding="utf-8")
    for index in range(20):
        append_sample(
            tmp_path,
            {
                "source": "replay",
                "tab_id": "research",
                "prompt_key": "verdict_rules",
                "version_id": shadow["id"],
                "ticker": f"R{index}",
                "as_of": f"2026-03-{index + 1:02d}",
                "decision": "long",
                "realized_return_pct": 20.0,
                "horizon_days": 20,
                "exit_model": "scale_out",
            },
        )
    score = agent.score(tmp_path, registry=registry)
    by_id = {row["version_id"]: row for row in score["versions"]}
    assert by_id[shadow["id"]]["n_live"] == 4
    assert by_id[shadow["id"]]["n_replay"] == 20
    assert by_id[shadow["id"]]["n"] == 24
    comparison = next(row for row in score["comparisons"] if row["version_id"] == shadow["id"])
    assert comparison["edge_vs_live"] == "insufficient_live_sample"
    result = govern_agent(agent, tmp_path)
    assert all(action.get("action") != "auto_promote" for action in result["actions"])


def test_digest_reports_live_and_replay_counts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DECISION_PACKET_HORIZON_DAYS", "20")
    agent = ResearchTabAgent()
    registry = agent.registry(tmp_path)
    live = registry.live_version("research", "verdict_rules")
    append_sample(
        tmp_path,
        {
            "source": "replay",
            "tab_id": "research",
            "prompt_key": "verdict_rules",
            "version_id": live["id"],
            "ticker": "AAPL",
            "as_of": "2026-03-02",
            "decision": "cash",
            "realized_return_pct": 0.0,
            "horizon_days": 20,
            "exit_model": "scale_out",
        },
    )
    digest = build_digest(tmp_path)
    research = next(tab for tab in digest["tabs"] if tab["tab_id"] == "research")
    row = next(item for item in research["live"] if item["version_id"] == live["id"])
    assert row["n_replay"] == 1
    assert row["n_live"] == 0
    assert "replay=1" in _markdown(digest)


def test_cli_dry_run_prints_the_estimate(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("TAB_AGENT_REPLAY_USD_PER_CALL", "0.01")
    old = datetime.now(timezone.utc) - timedelta(days=90)
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "OLD", "price": 50, "stop_pct": 0.08}],
        created_at=old,
    )
    code = main(
        [
            "--tab",
            "research",
            "--since",
            "2020-01-01",
            "--dry-run",
            "--skill-dir",
            str(tmp_path),
            "--db",
            str(tmp_path / "missing.db"),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "estimated_llm_calls=" in captured.out
    assert "estimated_cost_usd=" in captured.out
    assert "model may already know" in captured.out
    assert not replay_path(tmp_path).exists()
