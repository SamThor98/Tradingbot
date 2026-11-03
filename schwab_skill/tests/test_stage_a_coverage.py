"""Stage A coverage: waves finish the universe, and the funnel does not count timeouts as passes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from core.horizon_strategies import (
    EXECUTABLE_STAGE_A_HISTORY_DAYS,
    STAGE_A_DEFAULT_HISTORY_DAYS,
    executable_stage_a_history_days,
    stage_a_history_days,
)
from core.stage_a_coverage import (
    coverage_note,
    load_checkpoint,
    merge_horizon_candidates,
    result_is_reusable,
    reusable_from_checkpoint,
    run_stage_a_waves,
    save_checkpoint,
    stage_a_funnel_counts,
    universe_key,
)


def test_executable_history_covers_52_week_high() -> None:
    assert executable_stage_a_history_days() == EXECUTABLE_STAGE_A_HISTORY_DAYS
    assert EXECUTABLE_STAGE_A_HISTORY_DAYS >= int(252 * 7 / 5)
    assert EXECUTABLE_STAGE_A_HISTORY_DAYS > STAGE_A_DEFAULT_HISTORY_DAYS
    assert stage_a_history_days(["tsmom_12m"]) > executable_stage_a_history_days()


def test_funnel_does_not_count_timeouts_as_stage2_passes() -> None:
    counts = stage_a_funnel_counts(
        {
            "watchlist_size": 10,
            "stage2_fail": 3,
            "df_empty": 1,
            "too_few_candles": 0,
            "bars_stale": 0,
            "stage_a_timeouts": 4,
            "exceptions": 6,
            "stage_b_exceptions": 2,
            "stage_a_task_exceptions": 0,
        }
    )
    assert counts["evaluated"] == 6
    assert counts["unresolved"] == 4
    assert counts["stage2_pass"] == 2


def test_funnel_separates_task_exceptions_from_stage_b() -> None:
    counts = stage_a_funnel_counts(
        {
            "watchlist_size": 5,
            "stage2_fail": 1,
            "stage_a_timeouts": 0,
            "stage_a_task_exceptions": 1,
            "exceptions": 3,
        }
    )
    assert counts["evaluated"] == 4
    assert counts["stage2_pass"] == 3


def test_coverage_note_distinguishes_live_book_from_research() -> None:
    partial = coverage_note(
        watchlist=10,
        executable_requested=True,
        executable_unresolved=4,
        horizon_requested=True,
        horizon_unresolved=10,
    )
    assert partial.startswith("Scan partial:")
    research = coverage_note(
        watchlist=10,
        executable_requested=True,
        executable_unresolved=0,
        horizon_requested=True,
        horizon_unresolved=3,
    )
    assert research.startswith("Live book covered")
    done = coverage_note(
        watchlist=10,
        executable_requested=True,
        executable_unresolved=0,
        horizon_requested=False,
        horizon_unresolved=0,
    )
    assert done == ""


def test_merge_keeps_live_rejection_and_adds_horizon_only() -> None:
    executable = {
        "AAA": {"ok": False, "reason": "entry_timing_blocked"},
        "BBB": {"ok": False, "reason": "stage2_fail"},
        "CCC": {
            "ok": True,
            "candidate": {"ticker": "CCC", "entry_family": "stage2", "df": ["short"]},
        },
    }
    horizon = {
        "AAA": {
            "ok": True,
            "candidate": {
                "ticker": "AAA",
                "entry_family": "horizon",
                "horizon_hits": ["pullback"],
                "df": ["long"],
            },
        },
        "BBB": {
            "ok": True,
            "candidate": {"ticker": "BBB", "entry_family": "horizon", "horizon_hits": ["tsmom_12m"]},
        },
        "CCC": {
            "ok": True,
            "candidate": {
                "ticker": "CCC",
                "entry_family": "horizon",
                "horizon_hits": ["weekly_swing"],
                "df": ["long", "extra"],
            },
        },
    }
    rows = {row["ticker"]: row for row in merge_horizon_candidates(executable, horizon)}
    assert "AAA" not in rows
    assert rows["BBB"]["entry_family"] == "horizon"
    assert rows["CCC"]["entry_family"] == "stage2"
    assert rows["CCC"]["horizon_hits"] == ["weekly_swing"]
    assert rows["CCC"]["df"] == ["long", "extra"]


class _Done:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value

    def done(self) -> bool:
        return True


class _Pending:
    def result(self):
        raise RuntimeError("pending")

    def done(self) -> bool:
        return False


class _StallExec:
    def __init__(self, max_workers=1):
        self.max_workers = max_workers

    def submit(self, _fn, _ticker):
        return _Pending()


def test_waves_stop_after_stalls_and_label_unresolved() -> None:
    def _as_completed(_futures, timeout=None):
        raise TimeoutError()

    report = run_stage_a_waves(
        ["AAPL", "MSFT"],
        lambda ticker: {"ok": True, "candidate": {"ticker": ticker}},
        max_workers=1,
        wall_budget_sec=5,
        task_timeout_sec=1,
        max_stalls=2,
        executor_cls=_StallExec,
        as_completed=_as_completed,
        timeout_error=TimeoutError,
    )
    assert report["waves"] == 2
    assert report["unresolved"] == ["AAPL", "MSFT"]
    assert report["results"]["AAPL"]["reason"] == "stage_a_timeout"


def test_waves_continue_while_a_wave_finishes_names() -> None:
    def _eval(ticker: str) -> dict:
        return {"ok": False, "reason": "stage2_fail"}

    calls = {"n": 0}

    class _Exec:
        def __init__(self, max_workers=1):
            self.max_workers = max_workers

        def submit(self, fn, ticker):
            if ticker == "ZZ" and calls["n"] == 0:
                return _Pending()
            return _Done(fn(ticker))

    def _as_completed(futures, timeout=None):
        calls["n"] += 1
        done = [fut for fut in futures if fut.done()]
        if not done:
            raise TimeoutError()
        return iter(done)

    report = run_stage_a_waves(
        ["AA", "ZZ"],
        _eval,
        max_workers=2,
        wall_budget_sec=30,
        task_timeout_sec=5,
        max_stalls=2,
        executor_cls=_Exec,
        as_completed=_as_completed,
        timeout_error=TimeoutError,
    )
    assert report["unresolved"] == []
    assert report["results"]["AA"]["reason"] == "stage2_fail"
    assert report["results"]["ZZ"]["reason"] == "stage2_fail"
    assert report["waves"] >= 2


def test_checkpoint_reuses_stage2_fail_and_refreshes_stale_quotes(tmp_path) -> None:
    now = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    path = tmp_path / ".stage_a_checkpoint.json"
    frame = pd.DataFrame(
        {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10.0]},
        index=pd.to_datetime(["2026-10-07"]),
    )
    frame.index.name = "date"
    key = universe_key(
        tickers=["AAA", "BBB", "CCC"],
        strategy_ids=["trend_breakout"],
        executable_days=420,
        horizon_days=0,
        want_stage2=True,
        want_pead=False,
        want_horizon=False,
    )
    save_checkpoint(
        path,
        universe_key_value=key,
        session_date="2026-10-08",
        executable={
            "AAA": {"ok": False, "reason": "stage2_fail"},
            "BBB": {"ok": False, "reason": "stage_a_timeout"},
            "CCC": {"ok": True, "candidate": {"ticker": "CCC", "entry_family": "stage2", "df": frame}},
        },
        horizon={},
        horizon_started=False,
        now=now - timedelta(hours=2),
    )
    payload = load_checkpoint(path)
    executable, _horizon, started = reusable_from_checkpoint(
        payload,
        universe_key_value=key,
        session_date="2026-10-08",
        now=now,
    )
    assert started is False
    assert "AAA" in executable
    assert "BBB" not in executable
    assert "CCC" not in executable
    fresh = result_is_reusable({"ok": True, "candidate": {}}, saved_at=now.isoformat(), now=now)
    assert fresh is True
    thawed = executable["AAA"]
    assert thawed["reason"] == "stage2_fail"


def test_server_funnel_excludes_timeouts_from_stage2() -> None:
    from webapp.main import _build_funnel_stages

    funnel = _build_funnel_stages(
        {
            "watchlist_size": 10,
            "stage2_fail": 3,
            "df_empty": 1,
            "stage_a_timeouts": 4,
            "stage_a_candidates": 2,
            "coverage_note": "Scan partial: 4 of 10 names have no Stage A decision.",
        },
        2,
    )
    by_key = {stage["key"]: stage for stage in funnel["stages"]}
    assert by_key["evaluated"]["value"] == 6
    assert by_key["evaluated"]["filtered"] == 4
    assert by_key["stage2"]["value"] == 2
