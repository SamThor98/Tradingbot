"""ManualPortfolioProvider: user rows -> priced PortfolioRiskState (offline)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

import market_data
from core.providers.manual_portfolio_provider import (
    MAX_MANUAL_POSITIONS,
    ManualPortfolioError,
    ManualPortfolioProvider,
    _clean_rows,
)

PRICES = {"AAPL": 200.0, "MSFT": 400.0, "NVDA": 120.0}


def _fake_history(ticker: str, days: int = 10, auth: Any = None, skill_dir: Any = None):
    price = PRICES.get(ticker)
    if price is None:
        return pd.DataFrame(), {"provider": "test", "reason": "no_data"}
    return pd.DataFrame({"close": [price * 0.99, price]}), {"provider": "test"}


@pytest.fixture(autouse=True)
def _patch_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_data, "get_daily_history_with_meta", _fake_history)


class TestCleanRows:
    def test_dedupes_and_merges_qty(self) -> None:
        rows = _clean_rows([{"ticker": "aapl", "qty": 5}, {"ticker": "AAPL ", "qty": 3}])
        assert rows == [("AAPL", 8.0)]

    def test_rejects_zero_and_negative_qty(self) -> None:
        with pytest.raises(ManualPortfolioError, match="positive"):
            _clean_rows([{"ticker": "AAPL", "qty": 0}])
        with pytest.raises(ManualPortfolioError, match="positive"):
            _clean_rows([{"ticker": "AAPL", "qty": -5}])

    def test_rejects_invalid_symbol(self) -> None:
        with pytest.raises(ManualPortfolioError, match="not a valid ticker"):
            _clean_rows([{"ticker": "<script>", "qty": 1}])

    def test_rejects_empty_book(self) -> None:
        with pytest.raises(ManualPortfolioError, match="At least one position"):
            _clean_rows([])

    def test_caps_distinct_tickers(self) -> None:
        rows = [{"ticker": f"T{i}", "qty": 1} for i in range(MAX_MANUAL_POSITIONS + 1)]
        with pytest.raises(ManualPortfolioError, match="capped"):
            _clean_rows(rows)


class TestPriceRows:
    def test_prices_book(self) -> None:
        priced = ManualPortfolioProvider.price_rows([{"ticker": "AAPL", "qty": 10}])
        assert priced == [
            {"symbol": "AAPL", "qty": 10.0, "last": 200.0, "market_value": 2000.0, "price_provider": "test"}
        ]

    def test_fail_closed_lists_all_unpriced(self) -> None:
        rows = [{"ticker": "AAPL", "qty": 1}, {"ticker": "ZZZFAKE", "qty": 1}, {"ticker": "QQFAKE", "qty": 1}]
        with pytest.raises(ManualPortfolioError) as exc_info:
            ManualPortfolioProvider.price_rows(rows)
        assert exc_info.value.unpriced == ["ZZZFAKE", "QQFAKE"]


class TestBuild:
    def test_state_and_summary_with_cash(self) -> None:
        state, summary = ManualPortfolioProvider.build(
            [{"ticker": "AAPL", "qty": 10}, {"ticker": "MSFT", "qty": 5}],
            cash=2000.0,
        )
        # AAPL 2000 + MSFT 2000 stocks; equity includes cash.
        assert summary["total_market_value"] == 4000.0
        assert state.equity == 6000.0
        assert state.cash == 2000.0
        assert state.provenance.source == "manual"
        aapl = next(p for p in state.positions if p.ticker == "AAPL")
        assert aapl.weight_pct == pytest.approx(2000.0 / 6000.0 * 100, abs=0.01)
        # Summary matches build_portfolio_summary shape (day P/L zeroed for manual).
        top = summary["positions"][0]
        assert set(top) == {"symbol", "qty", "market_value", "day_pl", "avg_cost", "last", "pl_pct"}
        assert top["day_pl"] == 0.0
        assert summary["source"] == "manual"

    def test_no_cash_defaults_to_stocks_only_equity(self) -> None:
        state, summary = ManualPortfolioProvider.build([{"ticker": "NVDA", "qty": 10}])
        assert state.equity == 1200.0
        assert state.cash == 0.0
        assert summary["positions_count"] == 1
