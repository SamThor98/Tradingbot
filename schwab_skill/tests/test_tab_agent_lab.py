"""Overnight prompt lab: tournament, holdout gate, resume, and shadow-only install."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.system_tab_agent import BUILTIN_WORDING
from core.tab_agent_lab import (
    CHICAGO,
    Budget,
    LabConfig,
    LLMUnavailable,
    ModelFailureGuard,
    _model_call,
    baseline_cache_key,
    call_with_backoff,
    in_lab_window,
    lab_model_id,
    launch_lab_process,
    load_live_texts,
    night_id,
    pid_alive,
    run_night,
    scheduler_tick,
    select_baseline_sample,
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
        embargo_days=0,
        margin=0.05,
        min_holdout=8,
        min_train_cases=1,
        min_train_dates=1,
        min_holdout_cases=1,
        min_holdout_dates=1,
        train_per_era=0,
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
    monkeypatch.delenv("TAB_AGENT_LAB_BASELINE_SAMPLE", raising=False)
    loaded = LabConfig.load(tmp_path)
    assert loaded.concurrency == 6
    assert loaded.candidates == 8
    # 40 cases per window clears the 30-sample floor and stays a minority of a
    # night that previously spent ~2,281 decide calls on the full book.
    assert loaded.baseline_sample == 40
    monkeypatch.setenv("TAB_AGENT_LAB_BASELINE_SAMPLE", "0")
    assert LabConfig.load(tmp_path).baseline_sample == 0
    monkeypatch.setenv("TAB_AGENT_LAB_BASELINE_SAMPLE", "12")
    assert LabConfig.load(tmp_path).baseline_sample == 12


def test_system_text_is_a_calibration_score() -> None:
    cases = [
        LabCase("a", "2026-01-01", "AAA", 10.0, stated_confidence=0.2),
        LabCase("b", "2026-01-02", "BBB", -4.0, stated_confidence=0.9),
    ]
    book = score_system_text(cases, BUILTIN_WORDING, config=_objective(), live_text=BUILTIN_WORDING)
    # (0.2-1)^2 and (0.9-0)^2. A skip is not scored as -10.
    assert book["n"] == 2
    assert book["scoring"] == "calibration"
    assert book["sortino"] is None
    assert book["brier"] == pytest.approx((0.64 + 0.81) / 2, abs=1e-6)


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


def test_lab_grok_model_uses_xai_key_and_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A grok lab model calls api.x.ai with XAI_API_KEY. MiroFish and OpenAI keys stay unused."""
    _reset_llm_circuit()
    _clear_routing_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-process-openai")
    text = "\n".join(
        [
            "XAI_API_KEY=xai-lab-key",
            "MIROFISH_API_KEY=miro-lab-key",
            "OPENAI_API_KEY=sk-file-openai",
            "TAB_AGENT_LAB_MODEL=grok-4.3",
            "",
        ]
    )
    path = _write_env_text(tmp_path, text)
    before = path.read_text(encoding="utf-8")
    constructed, created = _patch_openai(monkeypatch, content="lab-ok")

    reply = _model_call(
        tmp_path,
        LabConfig.load(tmp_path),
        "system",
        "user",
        _budget(),
        sleeper=lambda _seconds: None,
    )

    assert reply == "lab-ok"
    assert constructed[0]["api_key"] == "xai-lab-key"
    assert constructed[0]["api_key"] != "miro-lab-key"
    assert constructed[0]["base_url"] == "https://api.x.ai/v1"
    assert "api.openai.com" not in str(constructed[0]["base_url"])
    assert created[0]["model"] == "grok-4.3"
    assert path.read_text(encoding="utf-8") == before
    assert "LLM_MODEL_NAME" not in before
    assert "LLM_BASE_URL" not in before


def test_lab_base_url_override_stays_in_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TAB_AGENT_LAB_BASE_URL replaces the xAI host on the call and is not written to .env."""
    _reset_llm_circuit()
    _clear_routing_env(monkeypatch)
    monkeypatch.setenv("LLM_MODEL_NAME", "gpt-4o-mini")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-process-openai")
    text = "\n".join(
        [
            "XAI_API_KEY=xai-lab-key",
            "MIROFISH_API_KEY=miro-lab-key",
            "TAB_AGENT_LAB_MODEL=grok-4.3",
            "TAB_AGENT_LAB_BASE_URL=https://proxy.x.ai/v1",
            "",
        ]
    )
    path = _write_env_text(tmp_path, text)
    before = path.read_text(encoding="utf-8")
    constructed, created = _patch_openai(monkeypatch, content="proxied")

    reply = _model_call(
        tmp_path,
        LabConfig.load(tmp_path),
        "system",
        "user",
        _budget(),
        sleeper=lambda _seconds: None,
    )

    assert reply == "proxied"
    assert constructed[0]["base_url"] == "https://proxy.x.ai/v1"
    assert constructed[0]["api_key"] == "xai-lab-key"
    assert created[0]["model"] == "grok-4.3"
    assert "api.openai.com" not in str(constructed[0]["base_url"])
    assert path.read_text(encoding="utf-8") == before
    assert os.environ["LLM_BASE_URL"] == "https://api.openai.com/v1"
    assert "LLM_MODEL_NAME" not in before
    assert "LLM_BASE_URL" not in before


def test_scan_call_llm_keeps_default_openai_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset LLM_MODEL_NAME and an empty LLM_BASE_URL stay on the OpenAI host.

    An XAI_API_KEY in the same env must not pull scan-time MiroFish onto xAI.
    """
    from engine_analysis import _call_llm

    _reset_llm_circuit()
    _clear_routing_env(monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", "xai-process-key")
    constructed, created = _patch_openai(monkeypatch, content="scan-ok")
    env = {
        "MIROFISH_API_KEY": "miro-scan-key",
        "XAI_API_KEY": "xai-file-key",
        "OPENAI_API_KEY": "sk-file-openai",
        "LLM_BASE_URL": "",
    }

    reply = _call_llm("prompt", "system", env)

    assert reply == "scan-ok"
    assert constructed[0]["api_key"] == "miro-scan-key"
    assert constructed[0]["base_url"] is None
    assert "x.ai" not in str(constructed[0]["base_url"] or "")
    assert created[0]["model"] == "gpt-4o-mini"


def test_lab_call_does_not_rewrite_dotenv_llm_routing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_llm_circuit()
    _clear_routing_env(monkeypatch)
    text = "\n".join(
        [
            "XAI_API_KEY=xai-lab-key",
            "MIROFISH_API_KEY=miro-lab-key",
            "TAB_AGENT_LAB_MODEL=grok-4.3",
            "TAB_AGENT_LAB_BASE_URL=https://api.x.ai/v1",
            "",
        ]
    )
    path = _write_env_text(tmp_path, text)
    before = path.read_bytes()
    _patch_openai(monkeypatch, content="ok")

    _model_call(
        tmp_path,
        LabConfig.load(tmp_path),
        "system",
        "user",
        _budget(),
        sleeper=lambda _seconds: None,
    )

    assert path.read_bytes() == before
    assert b"LLM_MODEL_NAME" not in before
    assert b"LLM_BASE_URL" not in before
    assert os.environ.get("LLM_MODEL_NAME") in (None, "")
    assert os.environ.get("LLM_BASE_URL") in (None, "")


def test_lab_xai_falls_back_to_mirofish_not_openai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_llm_circuit()
    _clear_routing_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-process-openai")
    path = _write_env_text(
        tmp_path,
        "MIROFISH_API_KEY=miro-lab-key\nOPENAI_API_KEY=sk-file-openai\nTAB_AGENT_LAB_MODEL=grok-4.3\n",
    )
    before = path.read_text(encoding="utf-8")
    constructed, created = _patch_openai(monkeypatch, content="fallback")

    _model_call(
        tmp_path,
        LabConfig.load(tmp_path),
        "system",
        "user",
        _budget(),
        sleeper=lambda _seconds: None,
    )

    assert constructed[0]["api_key"] == "miro-lab-key"
    assert constructed[0]["base_url"] == "https://api.x.ai/v1"
    assert created[0]["model"] == "grok-4.3"
    assert path.read_text(encoding="utf-8") == before


def test_call_llm_grok_does_not_send_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine_analysis import _call_llm

    _reset_llm_circuit()
    _clear_routing_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-process-openai")
    constructed, created = _patch_openai(monkeypatch, content="grok")
    env = {
        "XAI_API_KEY": "xai-direct-key",
        "MIROFISH_API_KEY": "miro-direct-key",
        "OPENAI_API_KEY": "sk-file-openai",
        "LLM_MODEL_NAME": "grok-4.3",
        "LLM_BASE_URL": "https://api.x.ai/v1",
    }

    _call_llm("prompt", "system", env, max_tokens=32)

    assert constructed[0]["api_key"] == "xai-direct-key"
    assert constructed[0]["base_url"] == "https://api.x.ai/v1"
    assert created[0]["model"] == "grok-4.3"

    _reset_llm_circuit()
    missing_xai, _created = _patch_openai(monkeypatch, content="miro")
    fallback_env = {
        "MIROFISH_API_KEY": "miro-direct-key",
        "OPENAI_API_KEY": "sk-file-openai",
        "LLM_MODEL_NAME": "grok-4.3",
        "LLM_BASE_URL": "https://api.x.ai/v1",
    }
    _call_llm("prompt", "system", fallback_env)
    assert missing_xai[0]["api_key"] == "miro-direct-key"


def test_explicit_openai_provider_keeps_openai_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_llm_circuit()
    _clear_routing_env(monkeypatch)
    _write_env_text(
        tmp_path,
        "\n".join(
            [
                "XAI_API_KEY=xai-lab-key",
                "MIROFISH_API_KEY=miro-lab-key",
                "OPENAI_API_KEY=sk-file-openai",
                "TAB_AGENT_LAB_MODEL=grok-4.3",
                "TAB_AGENT_LAB_PROVIDER=openai",
                "",
            ]
        ),
    )
    constructed, created = _patch_openai(monkeypatch, content="openai-route")

    _model_call(
        tmp_path,
        LabConfig.load(tmp_path),
        "system",
        "user",
        _budget(),
        sleeper=lambda _seconds: None,
    )

    assert constructed[0]["api_key"] == "miro-lab-key"
    assert constructed[0]["base_url"] is None
    assert created[0]["model"] == "grok-4.3"


def test_lab_provider_infers_xai_from_model_or_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import clear_env_cache, get_tab_agent_lab_base_url, get_tab_agent_lab_provider

    _clear_routing_env(monkeypatch)
    _write_env_text(tmp_path, "TAB_AGENT_LAB_MODEL=grok-4.3\n")
    assert get_tab_agent_lab_provider(tmp_path) == "xai"
    assert get_tab_agent_lab_base_url(tmp_path) == ""

    monkeypatch.setenv("TAB_AGENT_LAB_PROVIDER", "openai")
    assert get_tab_agent_lab_provider(tmp_path) == "openai"

    monkeypatch.delenv("TAB_AGENT_LAB_PROVIDER", raising=False)
    monkeypatch.setenv("TAB_AGENT_LAB_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("TAB_AGENT_LAB_BASE_URL", "https://api.x.ai/v1")
    assert get_tab_agent_lab_provider(tmp_path) == "xai"
    assert get_tab_agent_lab_base_url(tmp_path) == "https://api.x.ai/v1"

    monkeypatch.setenv("TAB_AGENT_LAB_BASE_URL", "https://api.openai.com/v1")
    assert get_tab_agent_lab_provider(tmp_path) == ""
    clear_env_cache()


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
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 1.25
    assert 2.0 <= sleeps[1] <= 2.5

    def _boom() -> str:
        raise RuntimeError("bad request")

    assert call_with_backoff(_boom, sleeper=sleeps.append) == ""
    assert len(sleeps) == 2

    limited = {"n": 0}

    def _limited() -> str:
        limited["n"] += 1
        if limited["n"] < 2:
            raise _RetryAfter("HTTP 429 resource-exhausted")
        return "ok"

    honored: list[float] = []
    assert call_with_backoff(_limited, sleeper=honored.append) == "ok"
    assert honored[0] >= 9.0

    def _always(message: str) -> int:
        seen = {"n": 0}

        def _fn() -> str:
            seen["n"] += 1
            raise RuntimeError(message)

        assert call_with_backoff(_fn, sleeper=lambda _seconds: None) == ""
        return seen["n"]

    assert _always("model is currently at capacity") == 6
    assert _always("HTTP 500") == 6
    assert _always("HTTP 504") == 6
    assert _always("takes 0 positional arguments") == 1

    def _hold() -> str:
        raise LLMUnavailable(12.0, "circuit_open")

    with pytest.raises(LLMUnavailable):
        call_with_backoff(_hold, sleeper=sleeps.append)
    assert len(sleeps) == 2


def test_rate_limit_then_success_finishes_a_normal_night(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 on the first calls retries, then the night scores prompts and finishes."""
    _reset_llm_circuit()
    _seed(tmp_path)
    _write_lab_env(tmp_path)
    calls = {"n": 0}
    reply = _proposal_reply()

    def _llm(*_args: object, **_kwargs: object) -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _RetryAfter("HTTP 429 resource-exhausted: model is currently at capacity", "5")
        return reply

    monkeypatch.setattr("engine_analysis._call_llm", _llm)
    start = datetime(2026, 11, 23, 18, 0, 51, tzinfo=CHICAGO)
    when, _mono, clock, sleeper = _moving_window(start)
    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        sleeper(seconds)

    report = run_night(
        tmp_path,
        config=_config(generations=1),
        clock=clock,
        promote=False,
        use_llm=True,
        cases=synthetic_cases("today"),
        objective=_objective(),
        sleeper=_sleep,
    )

    assert calls["n"] > 2
    assert any(seconds >= 5 for seconds in sleeps)
    assert report["tokens"] > 0
    assert report["spend_usd"] == round(report["tokens"] / 1000.0 * 0.01, 4)
    assert report["stopped_early"] is False
    assert report.get("status") != "llm_unavailable"
    assert report.get("stop_reason") != "llm_unavailable"
    assert report["orders_touched"] is False
    assert report["tabs"]["today"]["generations"] == 1
    assert report["tabs"]["today"]["candidates_tried"] > 0
    assert "no_candidates" not in report["tabs"]["today"]["errors"]
    assert when["t"] < start + timedelta(hours=1)
    done = calls["n"]
    again = run_night(
        tmp_path,
        config=_config(generations=3),
        clock=clock,
        promote=False,
        use_llm=True,
        cases=synthetic_cases("today"),
        objective=_objective(),
        sleeper=_sleep,
    )
    assert calls["n"] == done
    assert again["tabs"]["today"]["generations"] == 1
    _reset_llm_circuit()


def test_open_circuit_recovers_and_the_night_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three timeouts open the circuit. The lab waits it out instead of scoring blanks."""
    _reset_llm_circuit()
    try:
        _seed(tmp_path)
        _write_lab_env(tmp_path)
        monkeypatch.setenv("LLM_CIRCUIT_FAILS", "3")
        monkeypatch.setenv("LLM_CIRCUIT_COOLDOWN_SECONDS", "300")
        start = datetime(2026, 11, 23, 18, 0, 51, tzinfo=CHICAGO)
        when, mono, clock, sleeper = _moving_window(start)
        _patch_circuit_clock(monkeypatch, mono)
        sleeps: list[float] = []

        def _sleep(seconds: float) -> None:
            sleeps.append(seconds)
            sleeper(seconds)

        timeout = TimeoutError("Request timed out.")
        counter = _install_scripted_client(monkeypatch, [timeout, timeout, timeout, _proposal_reply()])
        report = run_night(
            tmp_path,
            config=_config(generations=1),
            clock=clock,
            promote=False,
            use_llm=True,
            cases=synthetic_cases("today"),
            objective=_objective(),
            sleeper=_sleep,
        )

        assert counter["n"] > 3
        assert max(sleeps) >= 250
        assert report["tokens"] > 0
        assert report["spend_usd"] == round(report["tokens"] / 1000.0 * 0.01, 4)
        assert report.get("status") != "llm_unavailable"
        assert report["stopped_early"] is False
        assert report["orders_touched"] is False
        assert report["tabs"]["today"]["generations"] >= 1
        assert "no_candidates" not in report["tabs"]["today"]["errors"]
        assert when["t"] < start + timedelta(minutes=20)
    finally:
        _reset_llm_circuit()


def test_llm_down_all_window_ends_unavailable_with_zero_spend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The night stays open through the outage and closes at 07:00 as llm_unavailable."""
    _reset_llm_circuit()
    try:
        _seed(tmp_path)
        _write_lab_env(tmp_path)
        monkeypatch.setenv("LLM_CIRCUIT_FAILS", "3")
        monkeypatch.setenv("LLM_CIRCUIT_COOLDOWN_SECONDS", "300")
        start = datetime(2026, 11, 23, 18, 0, 51, tzinfo=CHICAGO)
        when, mono, clock, sleeper = _moving_window(start)
        _patch_circuit_clock(monkeypatch, mono)
        _install_scripted_client(monkeypatch, [TimeoutError("Request timed out.")])
        report = run_night(
            tmp_path,
            config=_config(generations=0),
            clock=clock,
            promote=False,
            use_llm=True,
            cases=synthetic_cases("today"),
            objective=_objective(),
            sleeper=sleeper,
        )
        window_end = datetime(2026, 11, 24, 7, 0, tzinfo=CHICAGO)
        assert when["t"] >= window_end
        assert when["t"] < window_end + timedelta(hours=1)
        assert report["night_id"] == "2026-11-23"
        assert report["status"] == "llm_unavailable"
        assert report["stop_reason"] == "llm_unavailable"
        assert report["stopped_early"] is False
        assert report["tokens"] == 0
        assert report["spend_usd"] == 0.0
        assert report["orders_touched"] is False
        assert report["candidates_tried"] == 0
        assert report["tabs"]["today"]["generations"] == 0
        assert "no_candidates" not in report["tabs"]["today"]["errors"]
        text = (tmp_path / ".tab_agent_lab" / "reports" / "2026-11-23.md").read_text(encoding="utf-8")
        assert "llm_unavailable" in text
        assert "Spend is $0" in text
        state = json.loads((tmp_path / ".tab_agent_lab" / "state.json").read_text(encoding="utf-8"))
        assert state["finished"] is True
    finally:
        _reset_llm_circuit()


def test_retries_do_not_bypass_or_double_count_the_token_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed attempts charge nothing. One success charges once, then the cap stops the night."""
    _reset_llm_circuit()
    _seed(tmp_path)
    _write_lab_env(tmp_path)
    reply = _proposal_reply()
    calls = {"fail": 0, "ok": 0}
    charged = {"tokens": 0}

    def _llm(user: str, system: str, *_args: object, **_kwargs: object) -> str:
        if calls["fail"] < 3:
            calls["fail"] += 1
            raise RuntimeError("HTTP 429 resource-exhausted")
        calls["ok"] += 1
        charged["tokens"] = (len(system) + len(user) + len(reply)) // 4
        return reply

    monkeypatch.setattr("engine_analysis._call_llm", _llm)
    start = datetime(2026, 11, 23, 18, 0, 51, tzinfo=CHICAGO)
    when, _mono, clock, sleeper = _moving_window(start)
    config = _config(generations=5)
    config.token_cap = 1
    config.usd_per_1k = 0.01
    report = run_night(
        tmp_path,
        config=config,
        clock=clock,
        promote=False,
        use_llm=True,
        cases=synthetic_cases("today"),
        objective=_objective(),
        sleeper=sleeper,
    )

    assert calls["fail"] == 3
    assert calls["ok"] == 1
    assert charged["tokens"] > config.token_cap
    assert report["tokens"] == charged["tokens"]
    assert report["token_cap"] == 1
    assert report["spend_usd"] == round(report["tokens"] / 1000.0 * config.usd_per_1k, 4)
    assert report.get("status") != "llm_unavailable"
    assert report["orders_touched"] is False
    assert when["t"] < start + timedelta(hours=1)
    _reset_llm_circuit()


def test_scan_call_still_returns_empty_when_the_circuit_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening the circuit for the lab must not make scan-time calls raise or build a client."""
    import time

    from engine_analysis import _LLM_CIRCUIT, LLMCircuitOpenError, _call_llm

    _reset_llm_circuit()
    try:
        _LLM_CIRCUIT["open_until"] = time.monotonic() + 60
        built = {"n": 0}

        class _Client:
            def __init__(self, **_kwargs: object) -> None:
                built["n"] += 1

        monkeypatch.setattr("openai.OpenAI", _Client)
        assert _call_llm("prompt", "system", {"MIROFISH_API_KEY": "scan-key"}) == ""
        with pytest.raises(LLMCircuitOpenError) as caught:
            _call_llm("prompt", "system", {"MIROFISH_API_KEY": "scan-key"}, raise_on_error=True)
        assert caught.value.retry_after > 0
        assert built["n"] == 0
    finally:
        _reset_llm_circuit()


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


def test_cap_cannot_empty_a_side_and_embargo_holds() -> None:
    """A newest-180 cut would drop the older dates. The date split keeps both sides."""
    from core.tab_agent_lab_plan import plan_split, trading_days_between

    days = _weekdays(date(2026, 1, 5), 80)
    cases: list[LabCase] = []
    for index, day in enumerate(days):
        for copy in range(8):
            cases.append(LabCase(f"{day.isoformat()}-{copy}", day.isoformat(), f"T{copy}", 1.0, source="live"))
        if index == 40:
            cases.append(
                LabCase("rebuild-in-window", days[-3].isoformat(), "ZZZ", 4.0, source="rebuild", era="recent_current")
            )
    newest = sorted(cases, key=lambda case: (case.as_of, case.ticker))[-180:]
    assert all(case.as_of >= days[-30].isoformat() for case in newest if case.source == "live")
    plan = plan_split(
        cases,
        holdout_weeks=6,
        holdout_days=8,
        embargo_days=20,
        train_per_era=300,
        min_train_cases=1,
        min_train_dates=1,
        min_holdout_cases=1,
        min_holdout_dates=1,
    )
    assert plan.train
    assert plan.holdout
    assert all(case.source != "rebuild" for case in plan.holdout)
    assert any(case.source == "rebuild" for case in plan.embargo)
    holdout_start = date.fromisoformat(min(case.as_of for case in plan.holdout))
    for case in plan.train:
        assert trading_days_between(date.fromisoformat(case.as_of), holdout_start) > 20
    for case in plan.embargo:
        if case.source == "live":
            assert trading_days_between(date.fromisoformat(case.as_of), holdout_start) <= 20


def test_trust_gate_spends_nothing_and_names_the_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _count(**_kwargs: object) -> str:
        calls["n"] += 1
        return "nope"

    monkeypatch.setattr("core.tab_agent_lab._model_call", lambda *_a, **_k: calls.__setitem__("n", calls["n"] + 1))
    _write_october_store(tmp_path)
    report = run_night(
        tmp_path,
        config=LabConfig(enabled=True, tabs=("today",), max_generations=3, rebuild=False),
        clock=_evening_clock(),
        promote=False,
        use_llm=True,
        objective=_objective(),
        hooks=(_count, _count),
    )
    assert calls["n"] == 0
    assert report["tokens"] == 0
    assert report["spend_usd"] == 0.0
    assert report["candidates_tried"] == 0
    assert report["orders_touched"] is False
    assert report["stop_reason"] == "trust_gate"
    reason = report["trust_reason"]
    assert "zero spend" in reason
    assert "train 1 cases over 1 dates (2026-07-15..2026-07-15" in reason
    assert "holdout 277 cases over 6 dates (2026-10-06..2026-10-13" in reason
    assert "Need train>=150 cases over >=15 dates and holdout>=200 cases over >=10 dates" in reason
    assert "insufficient_train_cases" in reason
    assert "insufficient_holdout_dates" in reason
    assert "Fixtures excluded: 16" in reason
    text = (tmp_path / ".tab_agent_lab" / "reports" / "2026-11-11.md").read_text(encoding="utf-8")
    assert "zero spend" in text
    from core.tab_agent_digest import write_daily_digest

    payload = write_daily_digest(tmp_path, date(2026, 11, 12))
    digest = Path(payload["markdown_path"]).read_text(encoding="utf-8")
    assert "zero spend" in digest
    assert "holdout 277 cases over 6 dates" in digest


def test_fixture_rows_are_excluded_and_ticker_dates_deduped(tmp_path: Path) -> None:
    from core.tab_agent_lab_score import load_lab_book

    packets = {
        "packets": [
            _labeled_packet("fixture", "AAPL", "2026-05-04", entry=185.5, order_ref="ORD123"),
            _labeled_packet("real", "MSFT", "2026-10-06", entry=420.0, order_ref="999"),
            _labeled_packet("dup", "MSFT", "2026-10-06", entry=420.0, order_ref="1000"),
        ]
    }
    (tmp_path / "decision_packets.json").write_text(json.dumps(packets), encoding="utf-8")
    (tmp_path / ".tab_agent_replay.jsonl").write_text(
        json.dumps(
            {
                "source": "replay",
                "ticker": "AAPL",
                "as_of": "2026-05-04",
                "entry_price": 185.5,
                "order_ref": "ORD123",
                "counterfactual_return_pct": 3.0,
                "scored": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    book = load_lab_book(tmp_path, horizon_days=20)
    assert book.fixtures_excluded >= 1
    assert [(case.ticker, case.as_of) for case in book.cases] == [("MSFT", "2026-10-06")]
    assert book.duplicates_removed >= 1
    assert book.cases[0].payload is not None
    assert "qual_business" not in book.cases[0].payload
    assert "ticker" in book.cases[0].payload


def test_source_slices_use_rebuild_excess_and_pre_cutoff_cannot_win_alone() -> None:
    from core.tab_agent_lab_plan import prepare_scoring_cases, source_views

    cases = [
        LabCase("old", "2022-06-01", "OLD", 12.0, source="rebuild", pre_cutoff=True, signal_score=80),
        LabCase("new", "2024-06-03", "NEW", 1.0, source="rebuild", pre_cutoff=False, signal_score=80),
        LabCase("live", "2026-06-01", "LIVE", 5.0, source="live", signal_score=80),
    ]
    prepared = prepare_scoring_cases(cases)
    assert prepared[0].long_return_pct == pytest.approx(0.0)
    assert prepared[1].long_return_pct == pytest.approx(0.0)
    assert prepared[2].long_return_pct == pytest.approx(5.0)
    views = source_views(cases, ["long", "long", "long"], config=_objective())
    assert views["used_pre_cutoff"] is True
    assert views["slices"]["rebuild_pre_cutoff"]["n"] == 1
    assert views["slices"]["rebuild_post_cutoff"]["n"] == 1
    assert views["slices"]["live"]["n"] == 1
    assert views["slices"]["rebuild_pre_cutoff"]["sortino_adj"] != views["slices"]["live"]["sortino_adj"]
    winner, failures = select_winner(
        [
            {
                "id": "only-old",
                "dropped": None,
                "train": {"sortino_adj": 2.0, "n": 40, "n_longs": 30, "max_drawdown": -0.05},
                "sealed": {"sortino_adj": 1.5, "n": 20, "n_longs": 16, "max_drawdown": -0.04},
                "used_pre_cutoff": True,
                "ex_pre": {"sortino_adj": 0.1, "n": 20, "n_longs": 16, "max_drawdown": -0.04},
            }
        ],
        {"sortino_adj": 0.4, "n": 40, "n_longs": 40, "max_drawdown": -0.05, "ex_pre": {"sortino_adj": 0.4}},
        {"sortino_adj": 0.3, "n": 20, "n_longs": 20, "max_drawdown": -0.05},
        margin=0.05,
        min_train=8,
        min_holdout=8,
        drawdown_limit=0.20,
    )
    assert winner is None
    assert failures["pre_cutoff_only"] == 1


def test_holdout_gate_first_clears_on_nov_17_given_the_october_schedule() -> None:
    from core.tab_agent_lab_plan import project_holdout_readiness

    cohorts = [
        ("2026-10-06", "2026-11-11", 46),
        ("2026-10-07", "2026-11-11", 46),
        ("2026-10-08", "2026-11-11", 46),
        ("2026-10-09", "2026-11-11", 46),
        ("2026-10-12", "2026-11-11", 46),
        ("2026-10-13", "2026-11-11", 47),
        ("2026-10-15", "2026-11-12", 1),
        ("2026-10-16", "2026-11-13", 1),
        ("2026-10-19", "2026-11-16", 1),
        ("2026-10-20", "2026-11-17", 1),
        ("2026-10-21", "2026-11-18", 1),
        ("2026-10-22", "2026-11-19", 221),
    ]
    projected = project_holdout_readiness(cohorts, holdout_weeks=6, min_cases=200, min_dates=10)
    assert projected["holdout_ready_on"] == "2026-11-17"
    assert projected["holdout"]["dates"] == 10
    assert projected["holdout"]["cases"] >= 200


def test_baseline_cache_key_changes_with_prompt_sample_and_model() -> None:
    cases = synthetic_cases("today")
    train, holdout = split_walk_forward(cases, 6)
    sample = select_baseline_sample(train, 4)
    again = select_baseline_sample(train, 4)
    assert [case.case_id for case in sample] == [case.case_id for case in again]
    assert sample[0].as_of == min(case.as_of for case in train)
    assert sample[-1].as_of == max(case.as_of for case in train)
    assert [case.case_id for case in select_baseline_sample(train, 0)] == [case.case_id for case in train]
    assert len(select_baseline_sample(train, 10_000)) == len(train)

    base = baseline_cache_key("today", TODAY, sample, holdout[:4], "grok-4.3", provider="xai")
    assert base == baseline_cache_key("today", TODAY, sample, holdout[:4], "grok-4.3", provider="xai")
    changed_prompt = dict(TODAY)
    changed_prompt["institutional_trend"] = TODAY["institutional_trend"] + " EXTRA."
    changed_case = LabCase(
        sample[0].case_id,
        sample[0].as_of,
        sample[0].ticker,
        sample[0].long_return_pct + 1,
        signal_score=sample[0].signal_score,
    )
    assert base != baseline_cache_key("today", changed_prompt, sample, holdout[:4], "grok-4.3", provider="xai")
    assert base != baseline_cache_key(
        "today", TODAY, [changed_case, *sample[1:]], holdout[:4], "grok-4.3", provider="xai"
    )
    assert base != baseline_cache_key("today", TODAY, sample, holdout[:4], "grok-other", provider="xai")
    assert base != baseline_cache_key(
        "today", TODAY, select_baseline_sample(train, 6), holdout[:4], "grok-4.3", provider="xai"
    )
    assert lab_model_id(LabConfig()) == "default"
    assert lab_model_id(LabConfig(model="grok-4.3")) == "grok-4.3"


def _live_decider(skill_dir: Path, seen: list[str]):
    def decide(*, tab: str, texts: dict[str, str], case: LabCase) -> str:
        if texts == load_live_texts(skill_dir, tab):
            seen.append(case.case_id)
        return offline_decide(tab=tab, texts=texts, case=case)

    return decide


def test_baseline_cache_hits_then_invalidates_on_prompt_and_model(tmp_path: Path) -> None:
    _seed(tmp_path)
    seen: list[str] = []
    decide = _live_decider(tmp_path, seen)
    first = run_night(
        tmp_path,
        config=_config(generations=1),
        clock=_clock_at(datetime(2026, 11, 11, 18, 30, tzinfo=CHICAGO)),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(offline_propose, decide),
    )
    usage = first["usage"]
    assert usage["baseline_cache_misses"] == 1
    assert usage["baseline_cache_hits"] == 0
    assert usage["baseline_calls"] > 0
    assert usage["generation_calls"] > 0
    assert first["spend_usd"] == 0.0
    scored = len(seen)
    assert scored > 0

    second = run_night(
        tmp_path,
        config=_config(generations=1),
        clock=_clock_at(datetime(2026, 11, 12, 18, 30, tzinfo=CHICAGO)),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(offline_propose, decide),
    )
    assert len(seen) == scored
    assert second["usage"]["baseline_cache_hits"] == 1
    assert second["usage"]["baseline_cache_misses"] == 0
    assert second["usage"]["baseline_calls"] == 0
    text = (tmp_path / ".tab_agent_lab" / "reports" / "2026-11-12.md").read_text(encoding="utf-8")
    assert "Baseline cache: hits=1" in text
    assert "spend_usd=0.0" in text

    shifted = _config(generations=1)
    shifted.model = "grok-4.3"
    third = run_night(
        tmp_path,
        config=shifted,
        clock=_clock_at(datetime(2026, 11, 13, 18, 30, tzinfo=CHICAGO)),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(offline_propose, decide),
    )
    assert len(seen) > scored
    assert third["usage"]["baseline_cache_misses"] == 1
    assert third["usage"]["baseline_cache_hits"] == 0
    after_model = len(seen)

    registry = tmp_path / ".tab_agent_registry.json"
    payload = json.loads(registry.read_text(encoding="utf-8"))
    for bucket in payload["tabs"]["today"]["keys"].values():
        for row in bucket["versions"]:
            row["text"] = str(row["text"]) + " EXTRA."
    registry.write_text(json.dumps(payload), encoding="utf-8")
    fourth = run_night(
        tmp_path,
        config=shifted,
        clock=_clock_at(datetime(2026, 11, 14, 18, 30, tzinfo=CHICAGO)),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(offline_propose, decide),
    )
    assert len(seen) > after_model
    assert fourth["usage"]["baseline_cache_misses"] == 1
    assert fourth["tabs"]["today"]["live_train"]["n"] == third["tabs"]["today"]["live_train"]["n"]


def test_partial_baseline_checkpoint_resumes(tmp_path: Path) -> None:
    _seed(tmp_path)
    seen: list[str] = []

    def decide(*, tab: str, texts: dict[str, str], case: LabCase) -> str:
        if texts == load_live_texts(tmp_path, tab):
            seen.append(case.case_id)
            if len(seen) == 4:
                raise SystemExit("crash during baseline")
        return "long"

    with pytest.raises(SystemExit):
        run_night(
            tmp_path,
            config=_config(generations=1),
            clock=_evening_clock(),
            promote=False,
            cases=synthetic_cases("today"),
            objective=_objective(),
            hooks=(offline_propose, decide),
        )
    stored = seen[:3]
    assert len(stored) == 3
    cache = json.loads((tmp_path / ".tab_agent_lab" / "baseline_cache.json").read_text(encoding="utf-8"))
    entry = next(iter(cache["entries"].values()))
    assert entry["complete"] is False
    assert set(entry["decisions"]["train"]) == set(stored)
    state = json.loads((tmp_path / ".tab_agent_lab" / "state.json").read_text(encoding="utf-8"))
    assert state["finished"] is False
    assert "spend_usd" in state

    resumed: list[str] = []

    def resume(*, tab: str, texts: dict[str, str], case: LabCase) -> str:
        if texts == load_live_texts(tmp_path, tab):
            resumed.append(case.case_id)
        return "long"

    report = run_night(
        tmp_path,
        config=_config(generations=1),
        clock=_evening_clock(),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(offline_propose, resume),
    )
    assert stored[0] not in resumed
    assert set(stored).isdisjoint(resumed)
    assert report["usage"]["baseline_cache_resumes"] == 1
    assert report["usage"]["baseline_cache_hits"] == 0
    assert report["tabs"]["today"]["generations"] == 1
    done = json.loads((tmp_path / ".tab_agent_lab" / "baseline_cache.json").read_text(encoding="utf-8"))
    finished = next(iter(done["entries"].values()))
    assert finished["complete"] is True


def test_window_end_stops_during_baseline_and_writes_the_report(tmp_path: Path) -> None:
    _seed(tmp_path)
    calls = {"n": 0}

    def clock() -> datetime:
        if calls["n"] >= 3:
            return datetime(2026, 11, 12, 7, 0, tzinfo=CHICAGO)
        return datetime(2026, 11, 11, 18, 30, tzinfo=CHICAGO)

    def decide(*, tab: str, texts: dict[str, str], case: LabCase) -> str:
        _ = tab, texts, case
        calls["n"] += 1
        return "long"

    report = run_night(
        tmp_path,
        config=_config(generations=5),
        clock=clock,
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(offline_propose, decide),
    )
    assert calls["n"] == 3
    assert report["stop_reason"] == "window_end"
    assert report["stopped_early"] is True
    assert report["tabs"]["today"]["generations"] == 0
    assert report["spend_usd"] == 0.0
    assert report["orders_touched"] is False
    state = json.loads((tmp_path / ".tab_agent_lab" / "state.json").read_text(encoding="utf-8"))
    assert state["finished"] is True
    assert state["spend_usd"] == 0.0
    assert state["tokens"] == 0
    cache = json.loads((tmp_path / ".tab_agent_lab" / "baseline_cache.json").read_text(encoding="utf-8"))
    entry = next(iter(cache["entries"].values()))
    assert entry["complete"] is False
    assert len(entry["decisions"]["train"]) == 3
    text = (tmp_path / ".tab_agent_lab" / "reports" / "2026-11-11.md").read_text(encoding="utf-8")
    assert "window_end" in text
    assert "Baseline cache:" in text


def test_window_end_stops_during_evaluation(tmp_path: Path) -> None:
    """Baseline can finish and the night still stops on the next decide."""
    _seed(tmp_path)
    calls = {"n": 0}

    def clock() -> datetime:
        if calls["n"] >= 5:
            return datetime(2026, 11, 12, 7, 0, tzinfo=CHICAGO)
        return datetime(2026, 11, 11, 18, 30, tzinfo=CHICAGO)

    def decide(*, tab: str, texts: dict[str, str], case: LabCase) -> str:
        _ = tab, texts, case
        calls["n"] += 1
        return "long"

    config = _config(generations=3)
    config.baseline_sample = 2
    report = run_night(
        tmp_path,
        config=config,
        clock=clock,
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
        hooks=(offline_propose, decide),
    )
    assert calls["n"] == 5
    assert report["stop_reason"] == "window_end"
    assert report["tabs"]["today"]["generations"] == 0
    assert report["usage"]["baseline_calls"] == 4
    assert report["usage"]["generation_calls"] == 1
    assert report["orders_touched"] is False


def test_budget_cap_stops_during_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_llm_circuit()
    _seed(tmp_path)
    _write_lab_env(tmp_path)
    calls = {"n": 0}

    def _llm(*_args: object, **_kwargs: object) -> str:
        calls["n"] += 1
        return '{"conviction_score": 80, "continuation_probability": 0.7}'

    monkeypatch.setattr("engine_analysis._call_llm", _llm)
    config = _config(generations=4)
    config.token_cap = 1
    config.usd_per_1k = 0.01
    report = run_night(
        tmp_path,
        config=config,
        clock=_evening_clock(),
        promote=False,
        use_llm=True,
        cases=synthetic_cases("today"),
        objective=_objective(),
    )
    assert calls["n"] == 1
    assert report["stop_reason"] == "budget_cap"
    assert report["tokens"] > config.token_cap
    assert report["spend_usd"] == round(report["tokens"] / 1000.0 * config.usd_per_1k, 4)
    assert report["spend_usd"] > 0
    assert report["tabs"]["today"]["generations"] == 0
    assert report["orders_touched"] is False
    state = json.loads((tmp_path / ".tab_agent_lab" / "state.json").read_text(encoding="utf-8"))
    assert state["finished"] is True
    assert state["tokens"] == report["tokens"]
    assert state["spend_usd"] == report["spend_usd"]
    _reset_llm_circuit()


def test_spend_is_recorded_before_the_next_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_llm_circuit()
    _seed(tmp_path)
    _write_lab_env(tmp_path)
    seen: dict[str, float | int] = {}
    calls = {"n": 0}

    def _llm(*_args: object, **_kwargs: object) -> str:
        calls["n"] += 1
        if calls["n"] == 2:
            state = json.loads((tmp_path / ".tab_agent_lab" / "state.json").read_text(encoding="utf-8"))
            seen["tokens"] = int(state["tokens"])
            seen["spend_usd"] = float(state["spend_usd"])
        return '{"conviction_score": 80, "continuation_probability": 0.7}'

    monkeypatch.setattr("engine_analysis._call_llm", _llm)
    config = _config(generations=1)
    config.baseline_sample = 2
    config.candidates = 2
    config.usd_per_1k = 0.01
    report = run_night(
        tmp_path,
        config=config,
        clock=_evening_clock(),
        promote=False,
        use_llm=True,
        cases=synthetic_cases("today"),
        objective=_objective(),
    )
    assert calls["n"] >= 2
    assert int(seen["tokens"]) > 0
    assert float(seen["spend_usd"]) == round(int(seen["tokens"]) / 1000.0 * 0.01, 4)
    assert report["spend_usd"] >= float(seen["spend_usd"])
    assert report["usage"]["baseline_tokens"] > 0
    assert report["orders_touched"] is False
    _reset_llm_circuit()


def test_candidate_and_baseline_share_the_sample(tmp_path: Path) -> None:
    _seed(tmp_path)
    config = _config(generations=1)
    config.baseline_sample = 4
    report = run_night(
        tmp_path,
        config=config,
        clock=_evening_clock(),
        promote=False,
        cases=synthetic_cases("today"),
        objective=_objective(),
    )
    usage = report["usage"]
    assert usage["baseline_sample"] == 4
    assert len(usage["baseline_sample_ids"]["train"]) == 4
    assert len(usage["baseline_sample_ids"]["holdout"]) == 4
    live_train = report["tabs"]["today"]["live_train"]
    live_holdout = report["tabs"]["today"]["live_holdout"]
    assert live_train["n"] == 4
    assert live_holdout["n"] == 4
    assert report["tabs"]["today"]["best_train"]["n"] == 4
    assert report["tabs"]["today"]["best_holdout"]["n"] == 4
    assert report["tabs"]["today"]["generations"] == 1


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


def _weekdays(start: date, count: int) -> list[date]:
    rows: list[date] = []
    cursor = start
    while len(rows) < count:
        if cursor.weekday() < 5:
            rows.append(cursor)
        cursor += timedelta(days=1)
    return rows


def _write_october_store(root: Path) -> None:
    """294 labeled packets: 277 names on Oct 6-13, 16 AAPL fixtures, one SNA."""
    counts = {
        "2026-10-06": 46,
        "2026-10-07": 46,
        "2026-10-08": 46,
        "2026-10-09": 46,
        "2026-10-12": 46,
        "2026-10-13": 47,
    }
    packets = [_labeled_packet("SNA", "SNA", "2026-07-15", entry=210.0, order_ref="LIVE")]
    for index, day in enumerate(
        ("2026-05-04", "2026-06-01", "2026-06-15", "2026-07-01", "2026-08-03", "2026-08-17", "2026-09-01", "2026-09-14")
    ):
        packets.append(_labeled_packet(f"fx{index}a", "AAPL", day, entry=185.5, order_ref="ORD123"))
        packets.append(_labeled_packet(f"fx{index}b", "AAPL", day, entry=185.5, order_ref="ORD123"))
    for as_of, count in counts.items():
        for index in range(count):
            packets.append(
                _labeled_packet(f"{as_of}-{index}", f"N{index}", as_of, entry=50.0 + index, order_ref="LIVE")
            )
    (root / "decision_packets.json").write_text(json.dumps({"packets": packets}), encoding="utf-8")


def _labeled_packet(packet_id: str, ticker: str, as_of: str, *, entry: float, order_ref: str) -> dict:
    return {
        "packet_id": packet_id,
        "ticker": ticker,
        "created_at": f"{as_of}T15:00:00+00:00",
        "entry_price": entry,
        "signal_score": 70,
        "refs": {"order_ref": order_ref, "qual_business": 0.4, "stated_confidence": 0.8},
        "outcome": {"label": "win", "realized_return_pct": 4.0, "horizon_days": 20},
    }


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
    _write_env_text(skill_dir, "MIROFISH_API_KEY=sk-lab-test-key\n")


_ROUTING_ENV_KEYS = (
    "MIROFISH_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_KEY",
    "XAI_API_KEY",
    "LLM_MODEL_NAME",
    "LLM_BASE_URL",
    "TAB_AGENT_LAB_MODEL",
    "TAB_AGENT_LAB_BASE_URL",
    "TAB_AGENT_LAB_PROVIDER",
)


def _clear_routing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ROUTING_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _write_env_text(skill_dir: Path, text: str) -> Path:
    from config import clear_env_cache

    path = skill_dir / ".env"
    path.write_text(text, encoding="utf-8")
    clear_env_cache()
    return path


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
    return _clock_at(datetime(2026, 11, 11, 18, 30, tzinfo=CHICAGO))


def _clock_at(when: datetime):
    def clock() -> datetime:
        return when

    return clock


class _RetryAfter(RuntimeError):
    def __init__(self, message: str, retry_after: str = "9") -> None:
        super().__init__(message)
        self.response = type("Resp", (), {"headers": {"Retry-After": retry_after}})()


def _proposal_reply() -> str:
    body = (
        TODAY["institutional_trend"]
        + " "
        + TODAY["mean_reversion"]
        + " "
        + TODAY["retail_fomo"]
        + " Stay in cash when the setup is weak."
    )
    return json.dumps({"text": body, "rationale": "Kept the contract after the model returned."})


def _moving_window(start: datetime):
    """Lab clock and the LLM circuit clock. Sleep moves both, so a missed recovery still ends."""
    when = {"t": start}
    mono = {"t": 1_000.0}

    def clock() -> datetime:
        return when["t"]

    def sleeper(seconds: float) -> None:
        step = float(seconds)
        mono["t"] += step
        when["t"] = when["t"] + timedelta(seconds=step)

    return when, mono, clock, sleeper


def _patch_circuit_clock(monkeypatch: pytest.MonkeyPatch, mono: dict[str, float]) -> None:
    monkeypatch.setattr("engine_analysis.time.monotonic", lambda: mono["t"])


def _install_scripted_client(monkeypatch: pytest.MonkeyPatch, replies: list[BaseException | str]) -> dict[str, int]:
    import openai

    counter = {"n": 0}

    class _Completions:
        def create(self, **_kwargs: object) -> object:
            counter["n"] += 1
            item = replies[min(counter["n"] - 1, len(replies) - 1)]
            if isinstance(item, BaseException):
                raise item.__class__(str(item))
            message = type("Msg", (), {"content": item})()
            choice = type("Choice", (), {"message": message})()
            return type("Resp", (), {"choices": [choice]})()

    class _Client:
        def __init__(self, **_kwargs: object) -> None:
            self.chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setattr(openai, "OpenAI", _Client)
    return counter
