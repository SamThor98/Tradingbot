"""Expansion volume is the entry bar against the prior 50 sessions, and it is not a gate."""

from __future__ import annotations

import pandas as pd

from backtest import _score_fields_for_trade
from stage_analysis import add_indicators, expansion_volume_ratio, expansion_volume_ratio_by_date


def _bars(volumes: list[float]) -> pd.DataFrame:
    n = len(volumes)
    frame = pd.DataFrame(
        {
            "open": [10.0] * n,
            "high": [11.0] * n,
            "low": [9.0] * n,
            "close": [10.0] * n,
            "volume": volumes,
        }
    )
    return add_indicators(frame)


def test_expansion_ratio_uses_the_average_before_the_entry_bar() -> None:
    frame = _bars([100.0] * 59 + [250.0])
    assert expansion_volume_ratio(frame) == 2.5
    included = float(frame["volume"].iloc[-1]) / float(frame["avg_vol_50"].iloc[-1])
    assert included < 2.5


def test_expansion_ratio_needs_a_full_prior_window() -> None:
    assert expansion_volume_ratio(_bars([100.0] * 40)) is None


def test_dated_series_matches_the_entry_bar_ratio() -> None:
    volumes = [100.0] * 59 + [250.0]
    frame = _bars(volumes)
    frame.index = pd.bdate_range("2020-01-02", periods=len(frame))
    series = expansion_volume_ratio_by_date(frame)
    assert float(series.iloc[-1]) == expansion_volume_ratio(add_indicators(frame.reset_index(drop=True)))
    assert pd.isna(series.iloc[49])
    assert float(series.iloc[50]) == 1.0


def test_trade_tape_keeps_the_expansion_ratio() -> None:
    fields = _score_fields_for_trade({"expansion_volume_ratio": 1.4, "score_components": {}})
    assert fields["expansion_volume_ratio"] == 1.4
