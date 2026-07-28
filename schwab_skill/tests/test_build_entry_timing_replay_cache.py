"""Unit tests for per-ticker entry-timing cache builder helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.build_entry_timing_replay_cache import _metrics_through_entry  # noqa: E402


def _hist(n: int = 220) -> pd.DataFrame:
    idx = pd.date_range("2015-01-01", periods=n, freq="B")
    close = pd.Series([100.0 + i * 0.1 for i in range(n)], index=idx)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": [1_000_000.0] * n,
        },
        index=idx,
    )


def test_metrics_through_entry_computes_breakout_buffer() -> None:
    hist = _hist()
    entry = hist.index[-1]
    # Prior high is previous bar high = close[-2] * 1.01
    metrics = _metrics_through_entry(hist, entry)
    assert metrics is not None
    assert metrics.get("breakout_buffer_pct") is not None
    price = float(hist["close"].iloc[-1])
    prior_high = float(hist["high"].iloc[-2])
    expected = round((price - prior_high) / prior_high, 4)
    assert float(metrics["breakout_buffer_pct"]) == expected


def test_metrics_through_entry_rejects_empty_or_single_bar() -> None:
    assert _metrics_through_entry(pd.DataFrame(), pd.Timestamp("2015-06-01")) is None
    hist = _hist(n=1)
    assert _metrics_through_entry(hist, hist.index[-1]) is None


def test_metrics_through_entry_slices_to_entry_date() -> None:
    hist = _hist()
    entry = hist.index[100]
    metrics = _metrics_through_entry(hist, entry)
    assert metrics is not None
    # Buffer uses bars through entry only (not future highs).
    price = float(hist.loc[entry, "close"])
    prior_high = float(hist["high"].iloc[99])
    expected = round((price - prior_high) / prior_high, 4)
    assert float(metrics["breakout_buffer_pct"]) == expected
