"""Backtest tab agent: walk-forward scores from backtest_runs, flag default off."""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.backtest_tab_agent import (
    BacktestTabAgent,
    live_prompt_text,
    load_backtest_runs,
    note_queued_run,
    oos_returns,
)
from webapp.strategy_chat import STRATEGY_CHAT_SYSTEM, strategy_chat_system


def _run(run_id: str, trades: list[dict], *, start: str = "2026-06-01", end: str = "2026-08-01") -> dict:
    return {
        "id": run_id,
        "status": "completed",
        "created_at": "2026-08-02T00:00:00+00:00",
        "spec": {"start_date": start, "end_date": end},
        "result": {"total_trades": len(trades), "trades": trades, "total_return_net_pct": 3.0},
    }


def test_oos_drops_trades_entered_before_the_prompt() -> None:
    run = _run(
        "r1",
        [
            {"entry_date": "2026-05-01", "exit_date": "2026-05-20", "net_return": 0.10},
            {"entry_date": "2026-07-01", "exit_date": "2026-07-20", "net_return": 0.02},
        ],
    )
    points = oos_returns(run, "2026-06-15T00:00:00+00:00")
    assert points == [("2026-07-20", 2.0)]


def test_window_that_starts_before_the_prompt_is_not_a_single_return() -> None:
    run = {
        "id": "r2",
        "status": "completed",
        "spec": {"start_date": "2026-01-01", "end_date": "2026-06-01"},
        "result": {"total_return_net_pct": 11.0},
    }
    assert oos_returns(run, "2026-03-01") == []
    later = dict(run)
    later["spec"] = {"start_date": "2026-04-01", "end_date": "2026-06-01"}
    assert oos_returns(later, "2026-03-01") == [("2026-06-01", 11.0)]


def test_shadow_beats_live_only_on_its_own_oos_runs(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_BACKTEST_ENABLED", "true")
    monkeypatch.setenv("TAB_AGENT_MIN_SAMPLES", "8")
    agent = BacktestTabAgent()
    live = agent.registry(tmp_path).live_version("backtest", "strategy_chat")
    created = agent.propose(
        tmp_path,
        llm=lambda _s, _u: json.dumps(
            {
                "text": live["text"] + "\nPrefer smoother out-of-sample equity.",
                "rationale": "Sample may be small.",
            }
        ),
        prompt_key="strategy_chat",
    )
    shadow_id = created["created"][0]["id"]
    reg = agent.registry(tmp_path)
    reg._mutate(  # type: ignore[attr-defined]
        "backtest",
        shadow_id,
        lambda row, _rows: row.__setitem__("created_at", "2026-06-01T00:00:00+00:00"),
    )
    reg._mutate(
        "backtest",
        live["id"],
        lambda row, _rows: row.__setitem__("created_at", "2026-06-01T00:00:00+00:00"),
    )
    runs = [
        _run("live-run", [{"entry_date": "2026-06-02", "exit_date": "2026-06-20", "net_return": -0.04}]),
        _run("shadow-run", [{"entry_date": "2026-06-03", "exit_date": "2026-06-21", "net_return": 0.03}]),
    ]
    # Pad samples so the objective gate can separate them.
    for i in range(8):
        runs.append(
            _run(f"live-{i}", [{"entry_date": "2026-06-10", "exit_date": "2026-06-20", "net_return": -0.01}])
        )
        runs.append(
            _run(
                f"shadow-{i}",
                [{"entry_date": "2026-06-10", "exit_date": "2026-06-20", "net_return": 0.02 if i else -0.002}],
            )
        )
    attributions = {run["id"]: live["id"] for run in runs if str(run["id"]).startswith("live")}
    attributions.update({run["id"]: shadow_id for run in runs if str(run["id"]).startswith("shadow")})
    score = agent.score(tmp_path, runs=runs, attributions=attributions)
    by_id = {row["version_id"]: row for row in score["versions"]}
    assert by_id[shadow_id]["primary_score"] > by_id[live["id"]]["primary_score"]
    edge = next(row for row in score["comparisons"] if row["version_id"] == shadow_id)
    assert edge["edge_vs_live"] == "better_than_live"
    request = (tmp_path / ".tab_agent_reviews" / f"backtest-{shadow_id}.json").read_text(encoding="utf-8")
    assert "diff" in request


def test_flag_off_keeps_the_builtin_prompt_and_skips_attribution(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAB_AGENT_BACKTEST_ENABLED", "false")
    assert live_prompt_text(tmp_path) is None
    assert strategy_chat_system("Recent backtests:\nnone.").startswith(STRATEGY_CHAT_SYSTEM)
    note_queued_run(tmp_path, "run-1")
    assert not (tmp_path / ".tab_agent_backtest_runs.jsonl").exists()


def test_sqlite_loader_reads_backtest_runs(tmp_path) -> None:
    db = tmp_path / "webapp.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE backtest_runs (
            id TEXT PRIMARY KEY,
            status TEXT,
            spec_json TEXT,
            result_json TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO backtest_runs VALUES (?, ?, ?, ?, ?)",
        (
            "abc",
            "completed",
            json.dumps({"start_date": "2026-01-01", "end_date": "2026-02-01"}),
            json.dumps({"total_return_net_pct": 4.5, "total_trades": 3}),
            "2026-02-02T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()
    rows = load_backtest_runs(db)
    assert rows[0]["id"] == "abc"
    assert rows[0]["result"]["total_return_net_pct"] == 4.5
