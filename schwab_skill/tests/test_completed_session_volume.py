"""Partial-day volume is labeled. It does not change the scored ratio."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from stage_analysis import AVG_VOL_50, completed_session_volume, expansion_volume_ratio


def _frame() -> pd.DataFrame:
    index = pd.bdate_range("2026-01-02", periods=60)
    volume = [1_000_000.0] * 59 + [290_000.0]
    return pd.DataFrame({"volume": volume, AVG_VOL_50: [1_000_000.0] * 60}, index=index)


def test_partial_session_quotes_the_prior_bar() -> None:
    frame = _frame()
    last = frame.index[-1].to_pydatetime().replace(tzinfo=ZoneInfo("America/New_York"))
    clock = last.replace(hour=10, minute=52)
    note = completed_session_volume(frame, now=clock)
    assert note["volume_bar"] == "partial"
    assert expansion_volume_ratio(frame) == 0.29
    assert note["expansion_volume_ratio_completed"] == 1.0


def test_after_the_close_the_last_bar_is_the_session() -> None:
    frame = _frame()
    last = frame.index[-1].to_pydatetime().replace(tzinfo=ZoneInfo("America/New_York"))
    clock = last.replace(hour=16, minute=5)
    note = completed_session_volume(frame, now=clock)
    assert note["volume_bar"] == "complete"
    assert note["expansion_volume_ratio_completed"] == expansion_volume_ratio(frame)


def test_a_prior_day_bar_is_complete_even_in_the_morning() -> None:
    frame = _frame()
    clock = datetime(2026, 11, 16, 10, 52, tzinfo=ZoneInfo("America/New_York"))
    note = completed_session_volume(frame, now=clock)
    assert note["volume_bar"] == "complete"
