"""Backtest history uses Schwab first and only marks Yahoo as a fallback."""

from __future__ import annotations

import pandas as pd

import backtest


def _frame() -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=3, freq="D")
    df = pd.DataFrame(
        {
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.0],
            "low": [1.0, 1.0, 1.0],
            "close": [1.0, 1.0, 1.0],
            "volume": [1.0, 1.0, 1.0],
        },
        index=idx,
    )
    df.index.name = "date"
    return df


def test_schwab_history_is_not_a_fallback(monkeypatch) -> None:
    def fake(*_args, **_kwargs):
        return _frame(), {
            "provider": "schwab",
            "used_fallback": False,
            "fallback_reason": None,
            "rows": 3,
        }

    monkeypatch.setattr("market_data.get_daily_history_with_meta", fake)
    _out, meta = backtest._fetch_history_with_meta("AAPL", "2024-01-01", "2024-06-01")
    assert meta["provider"] == "schwab"
    assert meta["used_fallback"] is False
    assert meta["reason"] == "schwab_ok"


def test_yahoo_history_is_a_fallback(monkeypatch) -> None:
    def fake(*_args, **_kwargs):
        return _frame(), {
            "provider": "yfinance",
            "used_fallback": True,
            "fallback_reason": "HTTPError|yfinance_ok",
            "rows": 3,
        }

    monkeypatch.setattr("market_data.get_daily_history_with_meta", fake)
    _out, meta = backtest._fetch_history_with_meta("AAPL", "2024-01-01", "2024-06-01")
    assert meta["provider"] == "yfinance"
    assert meta["used_fallback"] is True
    assert meta["reason"] == "HTTPError|yfinance_ok"
