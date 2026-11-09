"""Historical replay warm-start. No orders."""

from __future__ import annotations

import json
import logging
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
    fill_case_from_history,
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

    def _complete(_system: str, user: str) -> str:
        calls.append(user)
        return "STRONG SETUP"

    report = run_replay(
        tmp_path,
        tab="today",
        since="2026-01-01",
        dry_run=True,
        max_llm_calls=1,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        db_path=tmp_path / "missing.db",
        complete=_complete,
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


def test_json_verdict_beats_a_rubric_echo() -> None:
    echoed = (
        "HOLD OFF means stay in cash. Never suggest a short, an inverse ETF, or an option. "
        "Cash is valid.\n"
        '{"decision":"long","verdict":"STRONG SETUP"}'
    )
    assert parse_decision(echoed, tab="research", quality_threshold=60) == "long"
    template = "AAPL is WATCH. Chart stage2=yes. Cash is valid. No short."
    assert parse_decision(template, tab="research", quality_threshold=60) == "long"
    boilerplate = "HOLD OFF means stay in cash. Cash is valid. No short."
    assert parse_decision(boilerplate, tab="research", quality_threshold=60) is None


def test_debate_median_ignores_the_first_seat() -> None:
    seats = json.dumps(
        {
            "seats": [
                {"name": "trend", "score": 10, "claim": "weak"},
                {"name": "setup", "score": 90, "claim": "ok"},
                {"name": "risk", "score": 90, "claim": "ok"},
                {"name": "skeptic", "score": 90, "claim": "ok"},
                {"name": "business", "score": 90, "claim": "ok"},
            ],
            "chair": "long",
        }
    )
    assert parse_decision(seats, tab="debate", quality_threshold=60) == "long"


def test_low_win_rate_with_a_positive_average_is_one_large_winner() -> None:
    """Fifteen cash zeros and one +83.84% scale-out: 6.25% wins, +5.24% average."""
    returns = [0.0] * 15 + [83.84]
    dates = [f"2026-01-{index + 1:02d}" for index in range(16)]
    book = evaluate_book(returns, dates, config=ObjectiveConfig(min_samples=5))
    assert book["win_rate"] == 0.0625
    assert book["n_longs"] == 16
    assert book["avg_realized_return_pct"] == 5.24
    assert book["max_drawdown"] == 0.0


def test_win_rate_is_taken_longs_only() -> None:
    """Cash rows stay in the equity path and stay out of the win rate."""
    returns = [10.0, -4.0, 0.0, 0.0, 8.0]
    decisions = ["long", "long", "cash", "cash", "long"]
    book = evaluate_book(
        returns,
        [f"2026-01-{index + 1:02d}" for index in range(5)],
        config=ObjectiveConfig(min_samples=1),
        decisions=decisions,
    )
    assert book["n"] == 5
    assert book["n_longs"] == 3
    assert book["win_rate"] == 0.6667


def _rising_frame() -> pd.DataFrame:
    index = pd.bdate_range("2026-06-01", periods=30)
    close = [100.0 + (2 * i) for i in range(30)]
    return pd.DataFrame(
        {"close": close, "high": [price + 1 for price in close], "low": [price - 1 for price in close]},
        index=index,
    )


def _stopped_frame() -> pd.DataFrame:
    index = pd.bdate_range("2026-06-01", periods=30)
    close = [100.0] + [90.0] * 29
    high = [101.0] + [91.0] * 29
    low = [99.0] + [88.0] * 29
    return pd.DataFrame({"close": close, "high": high, "low": low}, index=index)


def test_missing_stop_does_not_flatten_the_label(monkeypatch) -> None:
    monkeypatch.setenv("DECISION_PACKET_HORIZON_DAYS", "20")
    created = datetime(2026, 6, 1, tzinfo=timezone.utc)
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    missing = scale_out_return(
        {"ticker": "AAPL", "created_at": created, "entry_price": 100.0},
        _rising_frame(),
        None,
        now=now,
    )
    assert missing is not None
    assert missing["exit_model"] == "scale_out"
    assert missing["stop_source"] in {"atr", "base_pct"}
    assert missing["realized_return_pct"] > 0
    priced = scale_out_return(
        {"ticker": "AAPL", "created_at": created, "entry_price": 100.0, "stop_price": 92.0},
        _rising_frame(),
        None,
        now=now,
    )
    assert priced is not None
    assert priced["exit_model"] == "scale_out"
    assert priced["realized_return_pct"] > 0


def test_research_replay_scores_a_winner_and_a_loser(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DECISION_PACKET_HORIZON_DAYS", "20")
    monkeypatch.setenv("TAB_AGENT_REPLAY_WEIGHT", "0.5")
    for ticker, day in (("WIN", 1), ("LOSE", 2)):
        decision_packet.record_scan_packets(
            tmp_path,
            [{"ticker": ticker, "price": 100, "stop_pct": 0.08, "stage_2": True, "vcp": True, "signal_score": 80}],
            created_at=datetime(2026, 6, day, tzinfo=timezone.utc),
        )
    agent = ResearchTabAgent()
    registry = agent.registry(tmp_path)
    versions = [row for row in registry.versions("research") if row.get("status") == "live"]

    def _history(ticker: str) -> pd.DataFrame:
        return _stopped_frame() if ticker == "LOSE" else _rising_frame()

    report = run_replay(
        tmp_path,
        tab="research",
        since="2026-01-01",
        max_llm_calls=10,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        db_path=tmp_path / "missing.db",
        complete=lambda _system, _user: '{"decision":"long","verdict":"STRONG SETUP"}',
        history_provider=_history,
        versions=versions,
        progress_every=0,
    )
    assert report["stored"] == 2
    assert report["skipped_non_decision"] == 2
    by_ticker: dict[str, float] = {}
    for row in load_samples(tmp_path):
        by_ticker[str(row["ticker"])] = float(row["realized_return_pct"])
    assert by_ticker["WIN"] > 0
    assert by_ticker["LOSE"] < 0
    score = agent.score(tmp_path, registry=registry)
    rationale = next(row for row in score["versions"] if row.get("prompt_key") == "rationale")
    assert rationale["decision_prompt"] is False
    assert rationale["primary_score"] is None
    for row in score["versions"]:
        if row.get("prompt_key") != "verdict_rules":
            continue
        assert row["n_replay"] == 2
        assert row["n_live"] == 0
        assert row["win_rate"] == 0.5
        assert row["avg_realized_return_pct"] not in (0, None)
        assert row["sortino"] not in (0, None)
        assert row["max_drawdown"] < 0


def test_parse_failure_retries_once_and_logs_the_raw_reply(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING)
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "AAA", "price": 100, "stop_pct": 0.08}],
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    calls: list[str] = []

    def _complete(_system: str, user: str) -> str:
        calls.append(user)
        if len(calls) == 1:
            return "The chart looks interesting, but I will not name a setup."
        return '{"decision":"cash","verdict":"HOLD OFF"}'

    report = run_replay(
        tmp_path,
        tab="research",
        since="2026-01-01",
        max_llm_calls=5,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        db_path=tmp_path / "missing.db",
        complete=_complete,
        history_provider=lambda _ticker: _rising_frame(),
        versions=[{"id": "rules-1", "prompt_key": "verdict_rules", "text": "template", "status": "live"}],
        progress_every=0,
    )
    assert report["parse_retries"] == 1
    assert report["parse_failures"] == 0
    assert report["llm_calls"] == 2
    assert report["stored"] == 1
    assert load_samples(tmp_path)[0]["decision"] == "cash"
    assert "will not name a setup" in caplog.text


def test_debate_round2_is_stored_from_the_median(tmp_path) -> None:
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "AAA", "price": 100, "stop_pct": 0.08, "qual_trend": 80, "qual_setup": 80}],
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    versions = [
        {"id": "sys", "prompt_key": "system", "text": "Score the checklist.", "status": "live"},
        {"id": "r1", "prompt_key": "round1", "text": "First round. Score each seat.", "status": "live"},
        {
            "id": "r2",
            "prompt_key": "round2",
            "text": "Second round. Move each score by at most 15 points from round1.",
            "status": "live",
        },
    ]

    def _seats(first: int) -> str:
        return json.dumps(
            {
                "seats": [
                    {"name": "trend", "score": first, "claim": "ok"},
                    {"name": "setup", "score": 90, "claim": "ok"},
                    {"name": "risk", "score": 90, "claim": "ok"},
                    {"name": "skeptic", "score": 90, "claim": "ok"},
                    {"name": "business", "score": 90, "claim": "ok"},
                ],
                "chair": "long",
            }
        )

    users: list[str] = []

    def _complete(_system: str, user: str) -> str:
        users.append(user)
        return _seats(10 if len(users) == 1 else 90)

    dry = run_replay(
        tmp_path,
        tab="debate",
        since="2026-01-01",
        dry_run=True,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        db_path=tmp_path / "missing.db",
        versions=versions,
        progress_every=0,
    )
    assert dry["estimated_llm_calls"] == 2
    report = run_replay(
        tmp_path,
        tab="debate",
        since="2026-01-01",
        max_llm_calls=6,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        db_path=tmp_path / "missing.db",
        complete=_complete,
        history_provider=lambda _ticker: _rising_frame(),
        versions=versions,
        progress_every=0,
    )
    assert report["stored"] == 1
    assert report["llm_calls"] == 2
    assert "Second round" in users[1]
    rows = load_samples(tmp_path)
    assert len(rows) == 1
    assert rows[0]["prompt_key"] == "combination"
    assert rows[0]["prompt_versions"] == {"system": "sys", "round1": "r1", "round2": "r2"}
    assert rows[0]["decision"] == "long"
    assert rows[0]["debate_score"] == 90.0
    assert float(rows[0]["realized_return_pct"]) > 0


def test_progress_printer_and_resume_skips_stored_cases(tmp_path, capsys) -> None:
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "AAA", "price": 100, "stop_pct": 0.08}],
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    version = {"id": "live-1", "prompt_key": "verdict_rules", "text": "rules", "status": "live"}
    calls: list[int] = []

    def _complete(_system: str, _user: str) -> str:
        calls.append(1)
        return '{"decision":"long","verdict":"STRONG SETUP"}'

    kwargs = {
        "tab": "today",
        "since": "2026-01-01",
        "max_llm_calls": 5,
        "now": datetime(2026, 11, 3, tzinfo=timezone.utc),
        "db_path": tmp_path / "missing.db",
        "complete": _complete,
        "history_provider": lambda _ticker: _rising_frame(),
        "versions": [version],
        "progress_every": 1,
    }
    first = run_replay(tmp_path, **kwargs)
    printed = capsys.readouterr().out
    assert first["stored"] == 1
    assert "replay progress" in printed
    assert "llm_calls=1" in printed
    assert "parse_failures=0" in printed
    second = run_replay(tmp_path, **kwargs)
    assert second["llm_calls"] == 0
    assert second["skipped_duplicate"] == 1
    assert second["estimated_llm_calls"] == 0
    assert calls == [1]


def _seat_card(score: int) -> str:
    names = ("trend", "setup", "risk", "skeptic", "business")
    return json.dumps({"seats": [{"name": name, "score": score, "claim": "ok"} for name in names], "chair": "desk"})


def test_all_zero_debate_card_is_not_a_cash_decision() -> None:
    assert parse_decision(_seat_card(0), tab="debate", quality_threshold=60) is None
    assert parse_decision(_seat_card(75), tab="debate", quality_threshold=60) == "long"


def test_debate_zero_template_retries_into_a_long(tmp_path) -> None:
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "AAA", "price": 100, "stop_pct": 0.08}],
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    versions = [
        {"id": "sys", "prompt_key": "system", "text": "Score the checklist.", "status": "live"},
        {"id": "r1", "prompt_key": "round1", "text": "First round.", "status": "live"},
        {"id": "r2", "prompt_key": "round2", "text": "Second round.", "status": "live"},
    ]

    def _complete(_system: str, user: str) -> str:
        if "did not parse" in user:
            return _seat_card(80)
        return _seat_card(0)

    report = run_replay(
        tmp_path,
        tab="debate",
        since="2026-01-01",
        max_llm_calls=8,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        db_path=tmp_path / "missing.db",
        complete=_complete,
        history_provider=lambda _ticker: _rising_frame(),
        versions=versions,
        progress_every=0,
    )
    assert report["stored"] == 1
    rows = load_samples(tmp_path)
    assert len(rows) == 1
    assert rows[0]["prompt_key"] == "combination"
    assert rows[0]["decision"] == "long"
    assert rows[0]["debate_score"] == 80.0
    assert float(rows[0]["realized_return_pct"]) > 0


def test_mean_reversion_fade_sign_is_the_decision() -> None:
    cash = '{"fade_score": 40, "exhaustion_probability": 0.7, "decision": "long"}'
    held = '{"fade_score": -30, "exhaustion_probability": 0.2}'
    assert parse_decision(cash, tab="today", quality_threshold=60, prompt_key="mean_reversion") == "cash"
    assert parse_decision(held, tab="today", quality_threshold=60, prompt_key="mean_reversion") == "long"


def test_constant_long_is_flagged_and_non_decision_is_not_scored(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TAB_AGENT_MIN_SAMPLES", "5")
    monkeypatch.setenv("DECISION_PACKET_HORIZON_DAYS", "20")
    agent = ResearchTabAgent()
    registry = agent.registry(tmp_path)
    live = registry.live_version("research", "verdict_rules")
    for index in range(8):
        append_sample(
            tmp_path,
            {
                "source": "replay",
                "tab_id": "research",
                "prompt_key": "verdict_rules",
                "version_id": live["id"],
                "ticker": f"T{index}",
                "as_of": f"2026-03-{index + 1:02d}",
                "decision": "long",
                "realized_return_pct": 4.0 if index % 2 == 0 else -2.0,
                "counterfactual_return_pct": 4.0 if index % 2 == 0 else -2.0,
                "horizon_days": 20,
                "exit_model": "scale_out",
                "stop_source": "stored",
                "scored": True,
            },
        )
    score = agent.score(tmp_path, registry=registry)
    rules = next(row for row in score["versions"] if row["version_id"] == live["id"])
    assert rules["degenerate"] is True
    assert rules["degenerate_reason"] == "all_long"
    assert score["baseline"]["name"] == "buy_every_scan"
    assert score["baseline"]["n"] == 8
    assert rules["edge_vs_baseline"] == 0.0
    assert rules["decision_variance"] == 0
    rationale = next(row for row in score["versions"] if row["prompt_key"] == "rationale")
    assert rationale["scored"] is False


def test_rescore_drops_zero_debate_and_derives_a_stop(tmp_path) -> None:
    from core.tab_agent_replay import rescore_replay

    rows = [
        {
            "source": "replay",
            "tab_id": "debate",
            "prompt_key": "round2",
            "version_id": "r2",
            "ticker": "AAA",
            "as_of": "2026-06-01",
            "decision": "cash",
            "debate_score": 0,
            "realized_return_pct": 0.0,
            "exit_model": "fixed_horizon_no_stop",
            "scored": True,
        },
        {
            "source": "replay",
            "tab_id": "today",
            "prompt_key": "grok_code_review",
            "version_id": "g",
            "ticker": "AAA",
            "as_of": "2026-06-01",
            "decision": "long",
            "realized_return_pct": 5.0,
            "exit_model": "scale_out",
            "scored": True,
        },
        {
            "source": "replay",
            "tab_id": "today",
            "prompt_key": "institutional_trend",
            "version_id": "t",
            "ticker": "AAA",
            "as_of": "2026-06-01",
            "decision": "long",
            "realized_return_pct": 1.0,
            "exit_model": "fixed_horizon_no_stop",
            "stop_source": "none",
            "scored": True,
        },
    ]
    path = tmp_path / ".tab_agent_replay.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    summary = rescore_replay(
        tmp_path,
        history_provider=lambda _ticker: _rising_frame(),
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
    )
    assert summary["llm_calls"] == 0
    assert summary["zero_debate_unscored"] == 1
    assert summary["non_decision"] == 1
    assert "debate" in summary["needs_llm"]
    assert summary["relabeled"] == 1
    stored = load_samples(tmp_path)
    debate = next(row for row in stored if row["prompt_key"] == "round2")
    trend = next(row for row in stored if row["prompt_key"] == "institutional_trend")
    review = next(row for row in stored if row["prompt_key"] == "grok_code_review")
    assert debate["scored"] is False
    assert review["scored"] is False
    assert trend["exit_model"] == "scale_out"
    assert trend["stop_source"] in {"atr", "base_pct"}
    assert trend["realized_return_pct"] > 0


def test_collect_cases_fills_thin_packets_from_the_stored_scan(tmp_path) -> None:
    created = datetime(2026, 6, 1, 15, tzinfo=timezone.utc)
    decision_packet.overwrite_packets(
        tmp_path,
        [
            {
                "packet_id": "thin",
                "ticker": "AAA",
                "created_at": created.isoformat(),
                "entry_price": 100.0,
                "rank_score": 70,
                "setup_type": "breakout",
                "refs": {"stop_pct": 0.08},
            }
        ],
    )
    db = tmp_path / "webapp.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE scan_results (ticker TEXT, payload_json TEXT, created_at TEXT, user_id TEXT)")
    conn.execute(
        "INSERT INTO scan_results (ticker, payload_json, created_at, user_id) VALUES (?, ?, ?, ?)",
        (
            "AAA",
            json.dumps(
                {
                    "ticker": "AAA",
                    "stage_2": True,
                    "vcp": True,
                    "sma_50": 90,
                    "sma_200": 80,
                    "sector": "Tech",
                    "rs_rating": 91,
                    "avg_dollar_volume": 50_000_000,
                    "qual_trend": 80,
                    "qual_setup": 70,
                    "qual_asymmetry": 60,
                    "qual_crowding": 55,
                    "qual_business": 50,
                    "qual_raw": 65,
                }
            ),
            created.isoformat(),
            "local",
        ),
    )
    conn.commit()
    conn.close()
    cases = collect_cases(
        tmp_path,
        since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        horizon_days=20,
        db_path=db,
    )
    assert len(cases) == 1
    payload = pit_payload(cases[0]["signal"], cases[0]["as_of"])
    assert payload["stage_2"] is True
    assert payload["vcp"] is True
    assert payload["sma_50"] == 90
    assert payload["sma_200"] == 80
    assert payload["sector"] == "Tech"
    assert payload["rs_rating"] == 91
    assert payload["avg_dollar_volume"] == 50_000_000
    assert payload["qual_raw"] == 65
    assert payload["qual_business"] == 50


def test_history_fill_stops_at_the_case_date():
    end = pd.Timestamp("2026-06-01")
    before = pd.bdate_range(end=end, periods=60)
    after = pd.bdate_range(start=end + pd.offsets.BDay(1), periods=10)
    close = [100.0] * len(before) + [10_000.0] * len(after)
    volume = [1_000_000.0] * len(before) + [50_000_000.0] * len(after)
    index = list(before) + list(after)
    frame = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": volume},
        index=index,
    )
    case = {
        "ticker": "AAA",
        "as_of": "2026-06-01",
        "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "entry_price": 100.0,
        "stop_pct": 0.08,
        "signal": {"ticker": "AAA"},
    }
    missing = fill_case_from_history(case, frame, None)
    assert case["signal"]["sma_50"] == 100.0
    assert case["signal"]["avg_dollar_volume"] == 100_000_000.0
    assert "sma_200" in missing
    assert "sector" in missing
    assert "relative_strength" in missing


def test_unfilled_case_is_counted_and_still_replayed(tmp_path) -> None:
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "AAA", "price": 100, "stop_pct": 0.08}],
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    report = run_replay(
        tmp_path,
        tab="today",
        since="2026-01-01",
        max_llm_calls=4,
        now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        db_path=tmp_path / "missing.db",
        complete=lambda _system, _user: '{"decision":"long","verdict":"STRONG SETUP"}',
        history_provider=lambda _ticker: _rising_frame(),
        versions=[{"id": "live-1", "prompt_key": "verdict_rules", "text": "rules", "status": "live"}],
        progress_every=0,
    )
    assert report["history_fill"] is True
    assert report["unfilled_cases"] == 1
    assert "sector" in report["unfilled_field_counts"]
    assert report["stored"] == 1
