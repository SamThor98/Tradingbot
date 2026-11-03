"""Causal fast-average slopes used by the research screen."""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.fast_slope import (
    AVWAP_LOOKBACK,
    FEATURE_COLUMNS,
    MIN_BARS,
    base_anchor_index,
    compute_fast_slope_frame,
    ema,
    promotion_verdict,
    rolling_ols_slope,
    volume_weighted_average,
    wilder_ema,
)
from stage_analysis import _slope_per_step


def _ohlcv(close: np.ndarray, *, volume: np.ndarray | None = None, low: np.ndarray | None = None) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    n = len(close)
    idx = pd.bdate_range("2016-01-04", periods=n)
    vol = np.ones(n) if volume is None else np.asarray(volume, dtype=float)
    lows = close.copy() if low is None else np.asarray(low, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": np.maximum(close, lows),
            "low": lows,
            "close": close,
            "volume": vol,
        },
        index=idx,
    )


def test_rolling_slope_matches_stage_analysis_values() -> None:
    values = pd.Series([3.0, 1.0, 4.0, 1.5, 5.0, 9.0, 2.0, 6.0, 5.0, 3.5, 8.0])
    got = rolling_ols_slope(values, 5)
    for i in range(4, len(values)):
        expect = _slope_per_step(values.iloc[i - 4 : i + 1])
        assert abs(got[i] - expect) < 1e-9
    assert np.isnan(got[:4]).all()


def test_linear_close_has_unit_slope_after_warmup() -> None:
    close = 100.0 + np.arange(400, dtype=float)
    frame = compute_fast_slope_frame(_ohlcv(close))
    last = frame.iloc[-1]
    price = close[-1]
    # EMA of a straight line keeps the same slope once it is warmed up.
    # Wilder (alpha 0.1) needs a longer ramp than span-8 before the lag is gone.
    assert abs(last["ema8_slope_10"] - (1.0 / price)) < 1e-6
    assert abs(last["wema10_slope_10"] - (1.0 / price)) < 1e-6
    assert abs(last["sma20_slope_10"] - (1.0 / price)) < 1e-6
    assert abs(last["ema8_minus_sma20_slope_10"]) < 1e-6


def test_wilder_is_slower_than_span_ema_on_a_step() -> None:
    close = pd.Series([10.0] * 30 + [20.0] * 5)
    fast = ema(close, 8)
    wilder = wilder_ema(close, 10)
    # After the jump, the span-8 EMA (alpha 2/9) has moved farther than Wilder (alpha 0.1).
    assert float(fast.iloc[-1]) > float(wilder.iloc[-1]) > 10.0


def test_vwma_follows_the_high_volume_print() -> None:
    close = np.array([10.0, 10.0, 10.0, 10.0, 30.0])
    volume = np.array([1.0, 1.0, 1.0, 1.0, 100.0])
    df = _ohlcv(close, volume=volume)
    got = volume_weighted_average(df, 5)
    # Typical price equals close here. The last bar dominates.
    assert float(got.iloc[-1]) > 29.0


def test_anchor_ignores_the_breakout_low_and_keeps_the_earlier_tie() -> None:
    n = MIN_BARS
    low = np.full(n, 50.0)
    index = n - 1
    start = index - AVWAP_LOOKBACK
    low[start + 4] = 5.0
    low[start + 10] = 5.0
    low[index - 2] = 1.0  # inside the excluded recent window
    anchor = base_anchor_index(low, index)
    assert anchor == start + 4


def test_features_do_not_look_ahead() -> None:
    rng = np.random.default_rng(7)
    close = 50.0 + np.cumsum(rng.normal(0.1, 1.0, size=MIN_BARS + 40))
    full = _ohlcv(np.abs(close) + 5.0)
    asof = full.index[MIN_BARS + 10]
    whole = compute_fast_slope_frame(full)
    truncated = compute_fast_slope_frame(full.loc[:asof])
    for col in FEATURE_COLUMNS:
        left = whole.loc[asof, col]
        right = truncated.loc[asof, col]
        if pd.isna(left) and pd.isna(right):
            continue
        assert abs(float(left) - float(right)) < 1e-9


def test_short_history_is_all_null() -> None:
    frame = compute_fast_slope_frame(_ohlcv(np.arange(MIN_BARS - 1, dtype=float) + 10.0))
    assert frame.isna().all().all()


def test_promotion_verdict_requires_gate_and_lift() -> None:
    assert promotion_verdict(1.25, 1.05, 1.20) == "candidate"
    assert promotion_verdict(1.25, 1.05, 1.24) == "reject"
    assert promotion_verdict(1.10, 1.05, 1.00) == "reject"
    assert promotion_verdict(1.25, 0.90, 1.00) == "reject"
    assert promotion_verdict(None, 1.05, 1.00) == "inconclusive"
