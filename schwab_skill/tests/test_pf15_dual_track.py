"""Tests for PF 1.50 dual-track helpers (entry family + early-stop gate)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from stage_analysis import (  # noqa: E402
    early_stop_gate_blocks_stage_a,
    evaluate_early_stop_gate,
    is_pullback_entry,
)


def _trend_pullback_df(*, dist_to_sma50: float = -0.02) -> pd.DataFrame:
    n = 220
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    # Upward 200 SMA: price rises slowly then pulls back to SMA50.
    close = pd.Series([50.0 + i * 0.05 for i in range(n)], index=idx)
    sma200 = close.rolling(200, min_periods=200).mean()
    sma50 = close.rolling(50, min_periods=50).mean()
    # Force last bar into pullback zone relative to SMA50 while above SMA200.
    last_sma50 = float(sma50.iloc[-1])
    last_sma200 = float(sma200.iloc[-1])
    close.iloc[-1] = last_sma50 * (1.0 + dist_to_sma50)
    # Keep SMAs coherent on last row after close tweak.
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": [1_000_000.0] * n,
            "avg_vol_50": [1_000_000.0] * n,
            "sma_50": sma50,
            "sma_150": sma200,
            "sma_200": sma200,
        },
        index=idx,
    )
    df.loc[df.index[-1], "sma_50"] = last_sma50
    df.loc[df.index[-1], "sma_200"] = min(last_sma200, last_sma50 * 0.98)
    df.loc[df.index[-1], "sma_150"] = (last_sma50 + float(df.loc[df.index[-1], "sma_200"])) / 2.0
    return df


def test_is_pullback_entry_triggers_in_zone() -> None:
    df = _trend_pullback_df(dist_to_sma50=-0.02)
    assert is_pullback_entry(df) is True


def test_is_pullback_entry_rejects_extended() -> None:
    df = _trend_pullback_df(dist_to_sma50=0.08)
    assert is_pullback_entry(df) is False


def test_early_stop_gate_shadow_never_blocks(monkeypatch) -> None:
    monkeypatch.setenv("EARLY_STOP_GATE_MODE", "shadow")
    monkeypatch.setenv("EARLY_STOP_GATE_PTS_52W_MAX", "35")
    monkeypatch.setenv("EARLY_STOP_GATE_BREAKOUT_BUFFER_MIN", "0.01")
    gate = evaluate_early_stop_gate(pts_52w=38.0, breakout_buffer_pct=0.005, skill_dir=SKILL_DIR)
    assert gate["would_filter"] is True
    assert early_stop_gate_blocks_stage_a(gate, SKILL_DIR) is False


def test_early_stop_gate_live_blocks(monkeypatch) -> None:
    monkeypatch.setenv("EARLY_STOP_GATE_MODE", "live")
    monkeypatch.setenv("EARLY_STOP_GATE_PTS_52W_MAX", "35")
    monkeypatch.setenv("EARLY_STOP_GATE_BREAKOUT_BUFFER_MIN", "0.01")
    gate = evaluate_early_stop_gate(pts_52w=38.0, breakout_buffer_pct=0.005, skill_dir=SKILL_DIR)
    assert gate["would_filter"] is True
    assert early_stop_gate_blocks_stage_a(gate, SKILL_DIR) is True


def test_backtest_entry_family_config(monkeypatch) -> None:
    from config import get_backtest_entry_family

    monkeypatch.setenv("BACKTEST_ENTRY_FAMILY", "pullback")
    assert get_backtest_entry_family(SKILL_DIR) == "pullback"
    monkeypatch.setenv("BACKTEST_ENTRY_FAMILY", "pead_primary")
    assert get_backtest_entry_family(SKILL_DIR) == "pead_primary"
    monkeypatch.setenv("BACKTEST_ENTRY_FAMILY", "nope")
    assert get_backtest_entry_family(SKILL_DIR) == "stage2"
