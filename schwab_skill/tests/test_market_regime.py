from __future__ import annotations

import sys
import types
from typing import Any

import pandas as pd
import pytest

from sector_strength import _REGIME_SPY_HISTORY_CALENDAR_DAYS, is_market_regime_bullish


def _spy_df(n: int, *, close: float = 500.0, sma_200: float = 480.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": [close] * n,
            "high": [close + 1] * n,
            "low": [close - 1] * n,
            "close": [close] * n,
            "volume": [1_000_000.0] * n,
            "sma_200": [sma_200] * n,
        },
        index=idx,
    )


def test_regime_history_uses_enough_calendar_days(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_history(*_a: Any, days: int = 0, **_k: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
        captured["days"] = days
        df = _spy_df(250)
        return df, {"provider": "schwab", "used_fallback": False, "rows": 250}

    monkeypatch.setitem(
        sys.modules,
        "market_data",
        types.SimpleNamespace(get_daily_history_with_meta=_fake_history),
    )
    monkeypatch.setitem(
        sys.modules,
        "stage_analysis",
        types.SimpleNamespace(add_indicators=lambda df: df),
    )

    bullish, ctx = is_market_regime_bullish(None, None)
    assert captured["days"] == _REGIME_SPY_HISTORY_CALENDAR_DAYS
    assert bullish is True
    assert ctx["spy_price"] == 500.0
    assert ctx["spy_sma_200"] == 480.0
    assert ctx["data_unavailable"] is False


def test_regime_short_primary_history_fails_closed_without_yfinance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _short_history(*_a: Any, **_k: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
        return pd.DataFrame(), {"provider": "schwab", "used_fallback": False, "rows": 0}

    monkeypatch.setitem(
        sys.modules,
        "market_data",
        types.SimpleNamespace(get_daily_history_with_meta=_short_history),
    )
    monkeypatch.setitem(
        sys.modules,
        "stage_analysis",
        types.SimpleNamespace(add_indicators=lambda df: df),
    )
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=lambda *_a: (_ for _ in ()).throw(RuntimeError("no yf"))))

    bullish, ctx = is_market_regime_bullish(None, None)
    assert bullish is False
    assert ctx["data_unavailable"] is True
    assert ctx["spy_price"] is None


def test_regime_price_below_sma_is_bearish(monkeypatch: pytest.MonkeyPatch) -> None:
    def _history(*_a: Any, **_k: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
        return _spy_df(250, close=450.0, sma_200=480.0), {"provider": "schwab", "used_fallback": False, "rows": 250}

    monkeypatch.setitem(
        sys.modules,
        "market_data",
        types.SimpleNamespace(get_daily_history_with_meta=_history),
    )
    monkeypatch.setitem(
        sys.modules,
        "stage_analysis",
        types.SimpleNamespace(add_indicators=lambda df: df),
    )

    bullish, ctx = is_market_regime_bullish(None, None)
    assert bullish is False
    assert ctx["data_unavailable"] is False
    assert ctx["spy_price"] == 450.0
    assert ctx["spy_sma_200"] == 480.0
