"""Overnight prompt lab: tournament, holdout gate, resume, and shadow-only install."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.system_tab_agent import BUILTIN_WORDING
from core.tab_agent_lab import (
    CHICAGO,
    LabConfig,
    call_with_backoff,
    in_lab_window,
    launch_lab_process,
    night_id,
    pid_alive,
    run_night,
    scheduler_tick,
)
from core.tab_agent_lab_score import (
    LabCase,
    flipped_holdout_cases,
    holdout_gate,
    score_long_cash,
    score_system_text,
    split_walk_forward,
    synthetic_cases,
)
from core.tab_agent_lab_tournament import (
    generator_user,
    offline_decide,
    offline_propose,
    public_train_rows,
    run_generation,
    select_winner,
)
from core.tab_agent_objective import ObjectiveConfig

TODAY = {
    "institutional_trend": (
        "Institutional Trend-Follower. Return EXACTLY one strict JSON object "
        "with conviction_score and continuation_probability."
    ),
    "mean_reversion": "Mean-Reversion. Return EXACTLY one strict JSON object with fade_score.",
    "retail_fomo": (
        "Retail FOMO. Return EXACTLY one strict JSON object with conviction_score and continuation_probability."
    ),
}
DEBATE = {
    "system": 'Reply with JSON only. "seats" trend, setup, risk, skeptic, business qual_business never recommend a short.',
    "round1": "Score each seat using the checklist.",
    "round2": "Move at most 15 points from round1.",
}
BACKTEST = {
    "strategy_chat": (
        "Scan studio. Call validate_strategy_spec and queue_backtest. "
        "Keep strategy_ids, universe_preset, and task_id. Never claim live trading."
    )
}


def _objective() -> ObjectiveConfig:
    return ObjectiveConfig(min_samples=12, horizon_days=20, drawdown_tolerance=0.20)


def _config(tabs: tuple[str, ...] = ("today",), generations: int = 1) -> LabConfig:
    return LabConfig(
        enabled=True,
        concurrency=1,
        candidates=4,
        holdout_weeks=6,
        margin=0.05,
        min_holdout=8,
        max_generations=generations,
        tabs=tabs,
    )


def test_window_includes_weekends_and_stops_at_seven() -> None:
    chicago = ZoneInfo("America/Chicago")
    assert in_lab_window(datetime(2026, 11, 14, 18, 0, tzinfo=chicago))  # Saturday
    assert in_lab_window(datetime(2026, 11, 15, 6, 59, tzinfo=chicago))  # Sunday
    assert not in_lab_window(datetime(2026, 11, 11, 7, 0, tzinfo=chicago))
    assert not in_lab_window(datetime(2026, 11, 11, 17, 59, tzinfo=chicago))
    assert night_id(datetime(2026, 11, 12, 2, 0, tzinfo=chicago)) == "2026-11-11"
    assert night_id(datetime(2026, 11, 11, 18, 30, tzinfo=chicago)) == "2026-11-11"


def test_generation_keeps_contract_and_scores_debate_as_one_book() -> None:
    cases = synthetic_cases("debate")
    train, holdout = split_walk_forward(cases, 6)
    rows = run_generation(
        tab="debate",
        generation=0,
        count=4,
        live_texts=DEBATE,
        parents=[],
        train_cases=train,
        holdout_cases=holdout,
        config=_objective(),
        propose=offline_propose,
        decide=offline_decide,
        drift_weight=0.15,
        rng=None,
    )
    assert len(rows) == 4
    kept = [row for row in rows if not row.get("dropped")]
    assert kept
    assert all(len(row["texts"]) == 3 for row in kept)
    assert all(isinstance(row["train"], dict) and "sortino_adj" in row["train"] for row in kept)
    assert all(set(row["texts"]) == {"system", "round1", "round2"} for row in kept)


def test_broken_contract_is_dropped() -> None:
    def _blurb(**_kwargs: object) -> str:
        return "Pick the ticker with the best story. No tools."

    rows = run_generation(
        tab="backtest",
        generation=0,
        count=2,
        live_texts=BACKTEST,
        parents=[],
        train_cases=synthetic_cases("backtest")[:8],
        holdout_cases=synthetic_cases("backtest")[8:12],
        config=_objective(),
        propose=_blurb,
        decide=offline_decide,
        drift_weight=0.15,
        rng=None,
    )
    assert rows
    assert all(row["dropped"] == "contract" for row in rows)


def test_generator_does_not_see_holdout_scores() -> None:
    sealed_marker = "98765.4321"
    parents = [
        {
            "id": "old",
            "operator": "tighten_cash",
            "prompt_key": "institutional_trend",
            "dropped": None,
            "train": {"sortino_adj": 1.25, "max_drawdown": -0.04, "smoothness": 0.8, "n": 30},
            "sealed": {"sortino_adj": float(sealed_marker), "n": 12},
        }
    ]
    history = public_train_rows(parents)
    user = generator_user(
        tab="today",
        prompt_key="institutional_trend",
        operator="crossover",
        parent_text=TODAY["institutional_trend"],
        partner_text=None,
        history=history,
    )
    assert sealed_marker not in user
    assert "1.25" in user
    with pytest.raises(RuntimeError):
        public_train_rows([{"id": "x", "train": {"sortino_adj": 1}, "holdout_sortino": 9}])


def test_holdout_failure_is_not_a_winner() -> None:
    cases = flipped_holdout_cases()
    train, holdout = split_walk_forward(cases, 6)
    rows = run_generation(
        tab="today",
        generation=0,
        count=4,
        live_texts=TODAY,
        parents=[],
        train_cases=train,
        holdout_cases=holdout,
        config=_objective(),
        propose=offline_propose,
        decide=offline_decide,
        drift_weight=0.15,
        rng=None,
    )
    live_train = _live_book(train)
    live_holdout = _live_book(holdout)
    winner, failures = select_winner(
        rows,
        live_train,
        live_holdout,
        margin=0.05,
        min_train=12,
        min_holdout=8,
        drawdown_limit=0.20,
    )
    assert winner is None
    assert failures.get("holdout_margin", 0) >= 1


def test_train_and_holdout_winner_clears_the_gate() -> None:
    cases = synthetic_cases("today")
    train, holdout = split_walk_forward(cases, 6)
    rows = run_generation(
        tab="today",
        generation=0,
        count=4,
        live_texts=TODAY,
        parents=[],
        train_cases=train,
        holdout_cases=holdout,
        config=_objective(),
        propose=offline_propose,
        decide=offline_decide,
        drift_weight=0.15,
        rng=None,
    )
    winner, _failures = select_winner(
        rows,
        _live_book(train),
        _live_book(holdout),
        margin=0.05,
        min_train=12,
        min_holdout=8,
        drawdown_limit=0.20,
    )
    assert winner is not None
    assert winner["gate"] == "winner"
    ok, reason = holdout_gate(
        winner["train"],
        winner["sealed"],
        _live_book(train),
        _live_book(holdout),
        margin=0.05,
        min_train=12,
        min_holdout=8,
        drawdown_limit=0.20,
    )
    assert ok, reason


def test_system_skip_is_scored_on_the_missed_return() -> None:
    cases = [
        LabCase("a", "2026-01-01", "AAA", 10.0, stated_confidence=0.2),
        LabCase("b", "2026-01-02", "BBB", -4.0, stated_confidence=0.9),
    ]
    book = score_system_text(cases, BUILTIN_WORDING, config=_objective(), live_text=BUILTIN_WORDING)
    # Low confidence skips the +10 winner, which is a -10 penalty. The equity path stays in cash.
    assert book["n"] == 2
    assert book["n_longs"] == 1
    assert book["n_skips"] == 1
    assert book["avg_realized_return_pct"] == pytest.approx((-10.0 + -4.0) / 2, abs=0.02)


def test_win_rate_excludes_cash_rows() -> None:
    cases = [
        LabCase("a", "2026-01-01", "AAA", 5.0, signal_score=80),
        LabCase("b", "2026-01-02", "BBB", -3.0, signal_score=80),
        LabCase("c", "2026-01-03", "CCC", 9.0, signal_score=10),
    ]
    text = TODAY["institutional_trend"] + " LAB_CASH_BELOW=40."
    book = score_long_cash(
        cases,
        ["long", "long", "cash"],
        config=_objective(),
        text=text,
        live_text=TODAY["institutional_trend"],
    )
    assert book["n"] == 3
    assert book["n_longs"] == 2
    assert book["win_rate"] == 0.5


def test_resume_skips_a_finished_generation(tmp_path: Path) -> None:
    _seed(tmp_path)
    calls = {"n": 0}

    def _count(**kwargs: object) -> str:
        calls["n"] += 1
        return offline_propose(**kwargs)  # type: ignore[arg-type]

    first = run_night(
        tmp_path,
        config=_config(generations=1),
        clock=_evening_clock(),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        finalize=False,
    )
    assert first["finished"] is False
    run_night(
        tmp_path,
        config=_config(generations=2),
        clock=_evening_clock(),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        finalize=True,
        hooks=(_count, offline_decide),
    )
    assert calls["n"] == 4


def test_finished_night_does_not_run_again(tmp_path: Path) -> None:
    _seed(tmp_path)
    calls = {"n": 0}

    def _count(**kwargs: object) -> str:
        calls["n"] += 1
        return offline_propose(**kwargs)  # type: ignore[arg-type]

    run_night(
        tmp_path,
        config=_config(generations=1),
        clock=_evening_clock(),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(_count, offline_decide),
    )
    done = calls["n"]
    again = run_night(
        tmp_path,
        config=_config(generations=3),
        clock=_evening_clock(),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(_count, offline_decide),
    )
    assert calls["n"] == done
    assert again["orders_touched"] is False
    assert again["tabs"]["today"]["generations"] == 1


def test_winner_is_installed_as_shadow_only(tmp_path: Path) -> None:
    from core.tab_agent import get_tab_agent
    from core.tab_agent_governor import install_lab_shadow
    from core.today_tab_agent import TodayTabAgent

    _seed(tmp_path)
    agent = get_tab_agent("today")
    before = {
        key: agent.registry(tmp_path).live_version("today", key)["id"]
        for key in ("institutional_trend", "mean_reversion", "retail_fomo")
    }
    report = run_night(
        tmp_path,
        config=_config(generations=1),
        clock=_evening_clock(),
        promote=True,
        cases=synthetic_cases("today"),
        objective=_objective(),
    )
    shadow = report["tabs"]["today"]["shadow"]
    assert shadow["installed"] is True
    assert shadow["orders_touched"] is False
    reg = agent.registry(tmp_path)
    for key, live_id in before.items():
        live = reg.live_version("today", key)
        assert live is not None
        assert live["id"] == live_id
        assert live["status"] == "live"
    installed = [reg.get("today", version_id) for version_id in shadow["version_ids"]]
    assert installed
    assert all(row is not None and row["status"] == "shadow" for row in installed)
    again = install_lab_shadow(
        tmp_path,
        "today",
        {row["prompt_key"]: row["text"] for row in installed if row},
        rationale=str(installed[0]["rationale"]) if installed[0] else "",
    )
    assert again["installed"] is True
    assert again["version_ids"] == shadow["version_ids"]
    # A direct live status is not a path this installer exposes.
    with pytest.raises(Exception):
        reg.add_version(
            "today",
            prompt_key="institutional_trend",
            text=TODAY["institutional_trend"] + " extra",
            parent=before["institutional_trend"],
            rationale="nope",
            status="live",
        )
    _ = TodayTabAgent


def test_three_empty_generations_stop_the_tab(tmp_path: Path) -> None:
    _seed(tmp_path)

    def _none(**_kwargs: object) -> None:
        return None

    report = run_night(
        tmp_path,
        config=_config(generations=0),
        clock=_evening_clock(),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(_none, offline_decide),
    )
    assert report["tabs"]["today"]["generations"] == 3
    assert "no_candidates" in report["tabs"]["today"]["errors"]
    assert report["tabs"]["today"]["shadow"]["installed"] is False


def test_backoff_retries_rate_limits_and_returns_empty_on_hard_failure() -> None:
    sleeps: list[float] = []
    attempts = {"n": 0}

    def _flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("429 rate limit")
        return "ok"

    assert call_with_backoff(_flaky, sleeper=sleeps.append) == "ok"
    assert attempts["n"] == 3
    assert sleeps == [1.0, 2.0]

    def _boom() -> str:
        raise RuntimeError("bad request")

    assert call_with_backoff(_boom, sleeper=sleeps.append) == ""


def test_scheduler_respects_the_kill_switch_and_a_live_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAB_AGENT_LAB_ENABLED", raising=False)
    assert scheduler_tick(tmp_path, datetime(2026, 11, 11, 19, 0, tzinfo=CHICAGO))["reason"] == "disabled"
    monkeypatch.setenv("TAB_AGENT_LAB_ENABLED", "1")
    assert scheduler_tick(tmp_path, datetime(2026, 11, 11, 12, 0, tzinfo=CHICAGO))["reason"] == "outside_window"
    pid_path = tmp_path / ".tab_agent_lab" / "lab.pid"
    pid_path.parent.mkdir(parents=True)
    import os

    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    assert pid_alive(os.getpid())
    outcome = launch_lab_process(tmp_path)
    assert outcome["launched"] is False
    assert outcome["reason"] == "already_running"


def test_digest_includes_the_lab_section(tmp_path: Path) -> None:
    from core.tab_agent_digest import write_daily_digest
    from core.tab_agent_lab import write_night_report

    write_night_report(
        tmp_path,
        {
            "night_id": "2026-11-10",
            "orders_touched": False,
            "candidates_tried": 4,
            "tokens": 1200,
            "spend_usd": 0.012,
            "tabs": {
                "today": {
                    "generations": 1,
                    "candidates_tried": 4,
                    "live_train": {"sortino_adj": 0.4},
                    "best_train": {"sortino_adj": 1.2},
                    "best_holdout": {"sortino_adj": 0.9},
                    "shadow": {"installed": True, "version_ids": ["abc"]},
                }
            },
        },
    )
    payload = write_daily_digest(tmp_path, datetime(2026, 11, 11, tzinfo=CHICAGO).date())
    text = Path(payload["markdown_path"]).read_text(encoding="utf-8")
    assert "Overnight lab" in text
    assert "shadow=True" in text
    assert payload["lab"]["night_id"] == "2026-11-10"


def _live_book(cases: list[LabCase]) -> dict:
    from core.tab_agent_lab_score import bundle_text, score_long_cash

    blob = bundle_text(TODAY, tuple(TODAY))
    decisions = [offline_decide(tab="today", texts=TODAY, case=case) for case in cases]
    return score_long_cash(cases, decisions, config=_objective(), text=blob, live_text=blob)


def _seed(tmp_path: Path) -> None:
    from core.today_tab_agent import TodayTabAgent

    TodayTabAgent().registry(tmp_path)


def _evening_clock():
    when = datetime(2026, 11, 11, 18, 30, tzinfo=CHICAGO)

    def clock() -> datetime:
        return when

    return clock
