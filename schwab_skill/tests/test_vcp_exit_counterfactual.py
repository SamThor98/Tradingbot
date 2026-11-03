"""VCP skip is one replay policy. The default exit path still invalidates."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import backtest
from research.vcp_exit import interpretable_sample, vcp_exit_verdict
from scripts.replay_exit_overlay import PROFILES, _replay_exit_on_df


def _history_frame() -> tuple[pd.DataFrame, int]:
    n = 120
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    entry_idx = 70
    # Flat volume is not a contraction, so a long enough window fails the VCP check.
    volume = [1000.0] * n
    frame = pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": volume,
        },
        index=idx,
    )
    return frame, entry_idx


def test_skip_vcp_reaches_time_exit_when_history_is_present() -> None:
    frame, entry_idx = _history_frame()
    profile = PROFILES["exit_grace_t15_h40"]
    skill_dir = Path(backtest.SKILL_DIR)
    _px, _ts, reason = _replay_exit_on_df(
        frame,
        profile=profile,
        stop_pct=0.08,
        skill_dir=skill_dir,
        entry_idx=entry_idx,
        skip_vcp_invalidation=False,
    )
    _px2, ts2, reason2 = _replay_exit_on_df(
        frame,
        profile=profile,
        stop_pct=0.08,
        skill_dir=skill_dir,
        entry_idx=entry_idx,
        skip_vcp_invalidation=True,
    )
    assert reason == "vcp_invalidation"
    assert reason2 == "time_exit"
    assert ts2 == frame.index[entry_idx + profile.hold_days]


def test_default_exit_still_invalidates_vcp() -> None:
    frame, entry_idx = _history_frame()
    window = frame.iloc[: entry_idx + 20]
    reason = backtest._evaluate_position_exit(
        px=100.0,
        entry_price=100.0,
        entry_idx=entry_idx,
        idx=entry_idx + 19,
        stop_pct=0.08,
        highest_close=100.0,
        hold_days=40,
        min_hold_before_trail=15,
        defer_soft_exits=True,
        window=window,
        skill_dir=Path(backtest.SKILL_DIR),
    )
    assert reason == "vcp_invalidation"


def test_vcp_exit_verdict_requires_lift_and_sample() -> None:
    grace = {"pf_mean": 1.30, "clears_floors": True}
    skip = {"pf_mean": 1.34, "clears_floors": True}
    assert vcp_exit_verdict(grace, skip, interpretable=True) == "shadow_ready_exit_not_promoted"
    assert vcp_exit_verdict(grace, {"pf_mean": 1.31, "clears_floors": True}, interpretable=True) == (
        "do_not_change_vcp_exit"
    )
    assert vcp_exit_verdict(grace, skip, interpretable=False) == "not_interpretable"
    assert interpretable_sample(n_trades=1000, n_with_history=600) is True
    assert interpretable_sample(n_trades=1000, n_with_history=100) is False
