"""ManualPortfolioProvider: user rows -> priced PortfolioRiskState (offline)."""

from __future__ import annotations

from datetime import date
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
ACQUIRED = "2024-06-01"
COST = {"AAPL": 150.0, "MSFT": 350.0, "NVDA": 100.0}


def _row(ticker: str, qty: float, *, acquired_at: str = ACQUIRED, avg_cost: float | None = None) -> dict[str, Any]:
    key = ticker.upper().strip()
    return {
        "ticker": ticker,
        "qty": qty,
        "acquired_at": acquired_at,
        "avg_cost": COST.get(key, 10.0) if avg_cost is None else avg_cost,
    }


def _fake_history(ticker: str, days: int = 10, auth: Any = None, skill_dir: Any = None, **kwargs: Any):
    price = PRICES.get(ticker)
    if price is None:
        return pd.DataFrame(), {"provider": "test", "reason": "no_data"}
    return pd.DataFrame({"close": [price * 0.99, price]}), {"provider": "test"}


@pytest.fixture(autouse=True)
def _patch_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_data, "get_daily_history_with_meta", _fake_history)


class TestCleanRows:
    def test_dedupes_and_merges_qty(self) -> None:
        rows = _clean_rows([_row("aapl", 5), _row("AAPL ", 3, avg_cost=180.0)])
        assert len(rows) == 1
        ticker, qty, acquired, avg_cost = rows[0]
        assert ticker == "AAPL"
        assert qty == 8.0
        assert acquired == date(2024, 6, 1)
        # qty-weighted: (150*5 + 180*3) / 8
        assert avg_cost == pytest.approx((150.0 * 5 + 180.0 * 3) / 8.0)

    def test_rejects_zero_and_negative_qty(self) -> None:
        with pytest.raises(ManualPortfolioError, match="positive"):
            _clean_rows([_row("AAPL", 0)])
        with pytest.raises(ManualPortfolioError, match="positive"):
            _clean_rows([_row("AAPL", -5)])

    def test_rejects_invalid_symbol(self) -> None:
        with pytest.raises(ManualPortfolioError, match="not a valid ticker"):
            _clean_rows([_row("<script>", 1, avg_cost=10.0)])

    def test_rejects_empty_book(self) -> None:
        with pytest.raises(ManualPortfolioError, match="At least one position"):
            _clean_rows([])

    def test_caps_distinct_tickers(self) -> None:
        rows = [{"ticker": f"T{i}", "qty": 1, "acquired_at": ACQUIRED, "avg_cost": 10.0} for i in range(MAX_MANUAL_POSITIONS + 1)]
        with pytest.raises(ManualPortfolioError, match="capped"):
            _clean_rows(rows)

    def test_requires_acquired_and_cost(self) -> None:
        with pytest.raises(ManualPortfolioError, match="Ownership start date"):
            _clean_rows([{"ticker": "AAPL", "qty": 1, "avg_cost": 10.0}])
        with pytest.raises(ManualPortfolioError, match="Avg cost"):
            _clean_rows([{"ticker": "AAPL", "qty": 1, "acquired_at": ACQUIRED, "avg_cost": 0}])

    def test_rejects_future_acquired(self) -> None:
        with pytest.raises(ManualPortfolioError, match="future"):
            _clean_rows([_row("AAPL", 1, acquired_at="2099-01-01")])


class TestPriceRows:
    def test_prices_book_with_pl(self) -> None:
        priced = ManualPortfolioProvider.price_rows([_row("AAPL", 10)])
        assert priced[0]["symbol"] == "AAPL"
        assert priced[0]["qty"] == 10.0
        assert priced[0]["last"] == 200.0
        assert priced[0]["market_value"] == 2000.0
        assert priced[0]["avg_cost"] == 150.0
        assert priced[0]["pl_pct"] == pytest.approx((200.0 - 150.0) / 150.0 * 100.0)
        assert priced[0]["unrealized_pnl"] == pytest.approx(500.0)
        assert priced[0]["acquired_at"] == date(2024, 6, 1)

    def test_fail_closed_lists_all_unpriced(self) -> None:
        rows = [_row("AAPL", 1), {"ticker": "ZZZFAKE", "qty": 1, "acquired_at": ACQUIRED, "avg_cost": 10.0}, {"ticker": "QQFAKE", "qty": 1, "acquired_at": ACQUIRED, "avg_cost": 10.0}]
        with pytest.raises(ManualPortfolioError) as exc_info:
            ManualPortfolioProvider.price_rows(rows)
        assert exc_info.value.unpriced == ["ZZZFAKE", "QQFAKE"]


class TestBuild:
    def test_state_and_summary_with_cash(self) -> None:
        state, summary = ManualPortfolioProvider.build(
            [_row("AAPL", 10), _row("MSFT", 5)],
            cash=2000.0,
        )
        # AAPL 2000 + MSFT 2000 stocks; equity includes cash.
        assert summary["total_market_value"] == 4000.0
        assert state.equity == 6000.0
        assert state.cash == 2000.0
        assert state.provenance.source == "manual"
        aapl = next(p for p in state.positions if p.ticker == "AAPL")
        assert aapl.weight_pct == pytest.approx(2000.0 / 6000.0 * 100, abs=0.01)
        assert aapl.avg_price == 150.0
        assert aapl.acquired_at == date(2024, 6, 1)
        assert aapl.unrealized_pnl == pytest.approx(500.0)
        top = summary["positions"][0]
        assert "acquired_at" in top
        assert top["day_pl"] == 0.0
        assert top["pl_pct"] != 0.0
        assert summary["source"] == "manual"

    def test_no_cash_defaults_to_stocks_only_equity(self) -> None:
        state, summary = ManualPortfolioProvider.build([_row("NVDA", 10)])
        assert state.equity == 1200.0
        assert state.cash == 0.0
        assert summary["positions_count"] == 1
        assert summary["positions"][0]["pl_pct"] == pytest.approx(20.0)
