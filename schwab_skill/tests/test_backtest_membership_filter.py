from __future__ import annotations

import sys
import types

import pandas as pd

import backtest


def _sample_history() -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=420, freq="D")
    df = pd.DataFrame(
        {
            "open": [100.0] * len(idx),
            "high": [101.0] * len(idx),
            "low": [99.0] * len(idx),
            "close": [100.0] * len(idx),
            "volume": [1_000_000.0] * len(idx),
        },
        index=idx,
    )
    df.index.name = "date"
    return df


def test_prepare_context_applies_membership_as_of_start_date(monkeypatch) -> None:
    history = _sample_history()
    monkeypatch.setattr(
        backtest,
        "_fetch_history_with_meta",
        lambda *_args, **_kwargs: (
            history.copy(),
            {"provider": "schwab", "reason": "ok", "used_fallback": False},
        ),
    )
    monkeypatch.setattr(backtest, "_load_watchlist", lambda _sd: ["AAPL", "MSFT", "TSLA"])
    monkeypatch.setitem(
        sys.modules,
        "sector_strength",
        types.SimpleNamespace(
            SECTOR_ETFS=["XLK"],
            get_ticker_sector_etf=lambda _ticker, skill_dir=None: "XLK",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sp1500_membership",
        types.SimpleNamespace(tickers_as_of=lambda as_of: frozenset({"AAPL", "TSLA"})),
    )

    ctx1 = backtest._prepare_context("2024-01-02", "2024-12-31", watchlist=None, skill_dir=backtest.SKILL_DIR)
    ctx2 = backtest._prepare_context("2024-01-02", "2024-12-31", watchlist=None, skill_dir=backtest.SKILL_DIR)

    assert ctx1.watchlist == ["AAPL", "TSLA"]
    assert ctx2.watchlist == ["AAPL", "TSLA"]
    assert ctx1.data_integrity["membership_filter_mode"] == "historical_membership_start_date"
    assert ctx1.data_integrity["membership_as_of_date"] == "2024-01-02"
    assert ctx1.data_integrity["membership_filtered_out"] == 1


def test_prepare_context_falls_back_when_membership_missing(monkeypatch) -> None:
    history = _sample_history()
    monkeypatch.setattr(
        backtest,
        "_fetch_history_with_meta",
        lambda *_args, **_kwargs: (
            history.copy(),
            {"provider": "schwab", "reason": "ok", "used_fallback": False},
        ),
    )
    monkeypatch.setattr(backtest, "_load_watchlist", lambda _sd: ["AAPL", "MSFT"])
    monkeypatch.setitem(
        sys.modules,
        "sector_strength",
        types.SimpleNamespace(
            SECTOR_ETFS=["XLK"],
            get_ticker_sector_etf=lambda _ticker, skill_dir=None: "XLK",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sp1500_membership",
        types.SimpleNamespace(tickers_as_of=lambda _as_of: None),
    )

    ctx = backtest._prepare_context("2024-01-02", "2024-12-31", watchlist=None, skill_dir=backtest.SKILL_DIR)
    assert ctx.watchlist == ["AAPL", "MSFT"]
    assert ctx.data_integrity["membership_filter_mode"] == "membership_missing_live_fallback"
    assert ctx.data_integrity["membership_file_present"] is False
