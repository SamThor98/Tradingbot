from __future__ import annotations

from pathlib import Path

import pandas as pd

import backtest


def _price_df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    return pd.DataFrame({"close": closes}, index=idx)


def test_trailing_grace_defers_ratchet_stop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL", "9")
    closes = [100.0, 110.0, 103.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0]
    df = _price_df(closes)
    stop_pct = 0.05

    immediate = backtest._simulate_exit(
        df,
        entry_idx=0,
        hold_days=9,
        stop_pct=stop_pct,
        min_hold_before_trail=0,
    )
    delayed = backtest._simulate_exit(
        df,
        entry_idx=0,
        hold_days=9,
        stop_pct=stop_pct,
        min_hold_before_trail=9,
    )

    assert immediate[2] == "trailing_stop"
    assert immediate[1] == df.index[2]
    assert delayed[2] == "time_exit"
    assert delayed[1] == df.index[9]


def test_evaluate_position_exit_defers_soft_exits_during_grace(monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_MIN_HOLD_DEFER_SOFT_EXITS", "true")
    closes = [100.0] * 12
    df = _price_df(closes)
    df["sma_50"] = 101.0

    reason = backtest._evaluate_position_exit(
        px=99.0,
        entry_price=100.0,
        entry_idx=0,
        idx=3,
        stop_pct=0.05,
        highest_close=100.0,
        hold_days=20,
        min_hold_before_trail=10,
        defer_soft_exits=True,
        window=df.iloc[:4],
        skill_dir=Path(backtest.SKILL_DIR),
    )
    assert reason is None

    reason_after_grace = backtest._evaluate_position_exit(
        px=99.0,
        entry_price=100.0,
        entry_idx=0,
        idx=11,
        stop_pct=0.05,
        highest_close=100.0,
        hold_days=20,
        min_hold_before_trail=10,
        defer_soft_exits=True,
        window=df.iloc[:12],
        skill_dir=Path(backtest.SKILL_DIR),
    )
    assert reason_after_grace == "sma50_break"
