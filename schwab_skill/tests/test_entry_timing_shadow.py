from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stage_analysis import evaluate_entry_timing_shadow


@pytest.fixture
def entry_df() -> pd.DataFrame:
    n = 260
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = np.linspace(95.0, 100.0, n)
    high = close + 0.8
    low = close - 0.8
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
            "sma_50": close - 0.2,
            "sma_150": close - 1.0,
            "sma_200": close - 2.0,
            "avg_vol_50": np.full(n, 1_500_000.0),
        },
        index=idx,
    )


def test_entry_timing_shadow_flags_low_sma50_cushion(entry_df: pd.DataFrame, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_TIMING_SHADOW_MODE", "shadow")
    monkeypatch.setenv("ENTRY_SHADOW_DISABLE_SMA50_FILTERS", "false")
    monkeypatch.setenv("ENTRY_SHADOW_MIN_PCT_ABOVE_SMA50", "0.05")
    import stage_analysis as sa

    monkeypatch.setattr(sa, "add_indicators", lambda df: df)
    result = evaluate_entry_timing_shadow(entry_df, tmp_path)
    assert result["mode"] == "shadow"
    assert result["would_filter"] is True
    assert "sma50_cushion_low" in result["would_filter_reasons"]


def test_entry_timing_shadow_breakout_buffer_only(entry_df: pd.DataFrame, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_TIMING_SHADOW_MODE", "shadow")
    monkeypatch.setenv("ENTRY_SHADOW_DISABLE_SMA50_FILTERS", "true")
    monkeypatch.setenv("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT", "0.05")
    import stage_analysis as sa

    monkeypatch.setattr(sa, "add_indicators", lambda df: df)
    df = entry_df.copy()
    df.loc[df.index[-1], "close"] = 100.0
    df.loc[df.index[-1], "high"] = 100.5
    df.loc[df.index[-2], "high"] = 100.4
    result = evaluate_entry_timing_shadow(df, tmp_path)
    assert result["sma50_filters_disabled"] is True
    assert "sma50_cushion_low" not in result["would_filter_reasons"]
    assert result["would_filter"] is True
    assert "breakout_buffer_low" in result["would_filter_reasons"]


def test_entry_timing_shadow_profile_name(tmp_path, monkeypatch) -> None:
    from config import get_entry_timing_experiment_readiness, get_entry_timing_shadow_profile

    monkeypatch.setenv("ENTRY_TIMING_SHADOW_MODE", "shadow")
    monkeypatch.setenv("ENTRY_SHADOW_DISABLE_SMA50_FILTERS", "true")
    monkeypatch.setenv("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT", "0.01")
    assert get_entry_timing_shadow_profile(tmp_path) == "breakout_buffer_only_0.010"
    readiness = get_entry_timing_experiment_readiness(tmp_path)
    assert readiness["ready"] is True
    assert readiness["missing_env"] == []


def test_entry_timing_experiment_readiness_missing_env(tmp_path, monkeypatch) -> None:
    from config import get_entry_timing_experiment_readiness

    monkeypatch.setenv("ENTRY_TIMING_SHADOW_MODE", "shadow")
    monkeypatch.setenv("ENTRY_SHADOW_DISABLE_SMA50_FILTERS", "false")
    monkeypatch.setenv("ENTRY_SHADOW_MIN_BREAKOUT_BUFFER_PCT", "0.002")
    readiness = get_entry_timing_experiment_readiness(tmp_path)
    assert readiness["ready"] is False
    assert "ENTRY_SHADOW_DISABLE_SMA50_FILTERS=true" in readiness["missing_env"]


def test_entry_timing_shadow_off_skips_filter(entry_df: pd.DataFrame, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_TIMING_SHADOW_MODE", "off")
    import stage_analysis as sa

    monkeypatch.setattr(sa, "add_indicators", lambda df: df)
    result = evaluate_entry_timing_shadow(entry_df, tmp_path)
    assert result["mode"] == "off"
    assert result["would_filter"] is False
    assert result["would_filter_reasons"] == []
