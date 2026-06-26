from __future__ import annotations

import pandas as pd

from advisory_model import _future_labels


def test_future_labels_include_40d_horizon() -> None:
    idx = pd.date_range("2020-01-01", periods=300, freq="B")
    close = pd.Series(range(100, 400), index=idx, dtype=float)
    df = pd.DataFrame({"close": close, "high": close, "volume": 1_000_000})
    labels = _future_labels(df, 200)
    assert labels is not None
    assert "y_up_40d" in labels
    assert "ret_40d_fwd" in labels
    assert labels["ret_40d_fwd"] > 0


def test_future_labels_requires_40_bars_ahead() -> None:
    idx = pd.date_range("2020-01-01", periods=220, freq="B")
    close = pd.Series(range(100, 320), index=idx, dtype=float)
    df = pd.DataFrame({"close": close, "high": close, "volume": 1_000_000})
    assert _future_labels(df, 200) is None
