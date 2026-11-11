"""Overnight prompt lab: tournament, holdout gate, resume, and shadow-only install."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.system_tab_agent import BUILTIN_WORDING
from core.tab_agent_lab import (
    CHICAGO,
    Budget,
    LabConfig,
    ModelFailureGuard,
    _model_call,
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
    conservative_sortino,
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
    assert winner["operator"] != "smooth_curve"
    live_longs = _live_book(train)["n_longs"]
    assert winner["train"]["n_longs"] >= 0.60 * live_longs
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


def test_a_no_downside_streak_stays_finite() -> None:
    score = conservative_sortino([5.0, 5.0, 5.0, 5.0])
    assert score == pytest.approx(1.224745, abs=1e-4)
    assert score < 8


def test_trading_less_cannot_win() -> None:
    """Four untouched winners must not beat a full live book, even with no losers."""
    cases = [
        LabCase(f"c{i}", f"2026-03-{(i % 27) + 1:02d}", f"T{i}", 4.0 if i % 2 == 0 else -3.0, signal_score=80.0)
        for i in range(40)
    ]
    config = _objective()
    live = score_long_cash(cases, ["long"] * 40, config=config)
    thin_side = ["long" if i % 2 == 0 and i < 8 else "cash" for i in range(40)]
    thin = score_long_cash(cases, thin_side, config=config)
    cash = score_long_cash(cases, ["cash"] * 40, config=config)
    assert thin["n_longs"] == 4
    assert thin["primary_unbounded"] is True
    assert thin["sortino_method"] == "shrunk"
    assert thin["sortino_adj"] < 8
    assert live["sortino_method"] == "sample"
    ok, reason = holdout_gate(thin, thin, live, live, margin=0.05, min_train=8, min_holdout=8, drawdown_limit=0.20)
    assert ok is False
    assert reason == "train_too_few_trades"
    ok, reason = holdout_gate(cash, cash, live, live, margin=0.05, min_train=8, min_holdout=8, drawdown_limit=0.20)
    assert ok is False
    assert reason == "train_too_few_trades"


def test_skipping_down_to_half_the_live_book_cannot_win() -> None:
    """A higher Sortino from sitting out the losers is not a win at 50% participation."""
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
    skipped = next(row for row in rows if row["operator"] == "smooth_curve")
    assert skipped["train"]["primary_unbounded"] is True
    assert skipped["train"]["sortino_method"] == "shrunk"
    assert skipped["train"]["sortino_adj"] < 8
    live_train = _live_book(train)
    live_holdout = _live_book(holdout)
    ok, reason = holdout_gate(
        skipped["train"],
        skipped["sealed"],
        live_train,
        live_holdout,
        margin=0.05,
        min_train=12,
        min_holdout=8,
        drawdown_limit=0.20,
    )
    assert ok is False
    assert reason == "train_low_participation"
    assert skipped["train"]["n_longs"] < 0.60 * live_train["n_longs"]


def test_night_defaults_are_sized_for_max_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAB_AGENT_LAB_CONCURRENCY", raising=False)
    monkeypatch.delenv("TAB_AGENT_LAB_CANDIDATES", raising=False)
    loaded = LabConfig.load(tmp_path)
    assert loaded.concurrency == 6
    assert loaded.candidates == 8


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
        return offline_propose(**kwargs)

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
        return offline_propose(**kwargs)

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


def test_model_call_loads_skill_dotenv_and_charges_only_a_returned_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lab must call config._load_env(skill_dir) and pass that dict into _call_llm.

    engine_analysis._load_env() takes no arguments. Calling it with skill_dir
    raises TypeError before the client is built, so this fails closed on that
    signature mismatch. Only the HTTP client is fake.
    """
    _reset_llm_circuit()
    _write_lab_env(tmp_path)
    for key in ("MIROFISH_API_KEY", "OPENAI_API_KEY", "OPENAI_KEY", "LLM_MODEL_NAME", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    system = "system-prompt-for-the-lab-call"
    user = "user-prompt-for-the-lab-call"
    reply = "model-reply-that-actually-came-back"
    constructed, created = _patch_openai(monkeypatch, content=reply)
    budget = _budget()

    text = _model_call(tmp_path, _config(), system, user, budget, sleeper=lambda _seconds: None)

    assert text == reply
    assert constructed[0]["api_key"] == "sk-lab-test-key"
    assert created[0]["model"] == "gpt-4o-mini"
    assert created[0]["max_tokens"] == 700
    assert created[0]["messages"] == [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    assert budget.tokens == (len(system) + len(user) + len(reply)) // 4
    assert budget.spend_usd == round(budget.tokens / 1000.0 * 0.01, 4)


def test_failed_model_call_does_not_charge_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_llm_circuit()
    _write_lab_env(tmp_path)
    monkeypatch.delenv("MIROFISH_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    constructed, created = _patch_openai(monkeypatch, error=RuntimeError("connection refused"))
    budget = _budget()

    text = _model_call(tmp_path, _config(), "system " * 30, "user " * 30, budget, sleeper=lambda _seconds: None)

    assert text == ""
    assert constructed and created
    assert budget.tokens == 0
    assert budget.spend_usd == 0.0


def test_identical_exceptions_stop_the_night(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _seed(tmp_path)
    _write_lab_env(tmp_path)
    calls = {"n": 0}

    def _boom(*_args: object, **_kwargs: object) -> str:
        calls["n"] += 1
        raise TypeError("_load_env() takes 0 positional arguments but 1 was given")

    monkeypatch.setattr("engine_analysis._call_llm", _boom)
    with caplog.at_level(logging.WARNING, logger="core.tab_agent_lab"):
        report = run_night(
            tmp_path,
            config=_config(generations=20),
            clock=_evening_clock(),
            promote=False,
            use_llm=True,
            cases=synthetic_cases("today"),
            objective=_objective(),
        )

    assert calls["n"] == 2
    assert report["tokens"] == 0
    assert report["spend_usd"] == 0.0
    assert report["stopped_early"] is True
    assert "takes 0 positional arguments" in report["stop_reason"]
    assert report["orders_touched"] is False
    assert report["tabs"]["today"]["generations"] == 0
    assert any("takes 0 positional arguments" in str(item) for item in report["tabs"]["today"]["errors"])
    stops = [rec for rec in caplog.records if "stopping the night" in rec.getMessage()]
    warnings = [rec for rec in caplog.records if rec.getMessage().startswith("Lab model call failed")]
    assert len(stops) == 1
    assert stops[0].levelno == logging.ERROR
    assert len(warnings) == 1


def test_model_failure_guard_stops_only_on_a_majority() -> None:
    guard = ModelFailureGuard()
    guard.begin_generation()
    guard.note_failure(TypeError("same"))
    guard.note_success()
    guard.note_success()
    guard.note_success()
    assert guard.aborted is False

    guard.begin_generation()
    guard.note_failure(TypeError("same"))
    guard.note_failure(RuntimeError("other"))
    assert guard.aborted is False

    guard.begin_generation()
    guard.note_failure(TypeError("same"))
    guard.note_failure(TypeError("same"))
    assert guard.aborted is True
    guard.begin_generation()
    assert guard.aborted is True


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


def _budget() -> Budget:
    return Budget(0, 0.01)


def _write_lab_env(skill_dir: Path) -> None:
    from config import clear_env_cache

    (skill_dir / ".env").write_text("MIROFISH_API_KEY=sk-lab-test-key\n", encoding="utf-8")
    clear_env_cache()


def _reset_llm_circuit() -> None:
    from engine_analysis import _LLM_CIRCUIT

    _LLM_CIRCUIT["consecutive_failures"] = 0.0
    _LLM_CIRCUIT["open_until"] = 0.0


def _patch_openai(
    monkeypatch: pytest.MonkeyPatch, *, content: str | None = None, error: BaseException | None = None
) -> tuple[list[dict], list[dict]]:
    import openai

    constructed: list[dict] = []
    created: list[dict] = []

    class _Completions:
        def create(self, **kwargs: object) -> object:
            created.append(dict(kwargs))
            if error is not None:
                raise error
            message = type("Msg", (), {"content": content})()
            choice = type("Choice", (), {"message": message})()
            return type("Resp", (), {"choices": [choice]})()

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            constructed.append(dict(kwargs))
            self.chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setattr(openai, "OpenAI", _Client)
    return constructed, created


def _evening_clock():
    when = datetime(2026, 11, 11, 18, 30, tzinfo=CHICAGO)

    def clock() -> datetime:
        return when

    return clock
