"""Public manual-portfolio endpoints: pricing snapshot + risk dashboard build."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import market_data
import sector_strength
from webapp import main

PRICES = {"AAPL": 200.0, "MSFT": 400.0}
ACQUIRED = "2024-06-01"


def _fake_history(ticker: str, days: int = 10, auth: Any = None, skill_dir: Any = None, **kwargs: Any):
    price = PRICES.get(ticker)
    if price is None:
        return pd.DataFrame(), {"provider": "test", "reason": "no_data"}
    return pd.DataFrame({"close": [price * 0.99, price]}), {"provider": "test"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_data, "get_daily_history_with_meta", _fake_history)
    monkeypatch.setattr(sector_strength, "get_ticker_sector_etf", lambda t, skill_dir=None: "XLK")
    monkeypatch.setattr(main, "get_shared_auth", lambda: (_ for _ in ()).throw(RuntimeError("no tokens")))
    # Reset per-process rate limiter and cache so tests are order-independent.
    main._manual_build_last_by_ip.clear()
    main._manual_risk_cache.update({"key": None, "at": 0.0, "payload": None})


def _pos(ticker: str, qty: float, *, avg_cost: float = 100.0, acquired_at: str = ACQUIRED) -> dict[str, Any]:
    return {"ticker": ticker, "qty": qty, "acquired_at": acquired_at, "avg_cost": avg_cost}


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "positions": [_pos("AAPL", 10, avg_cost=150.0), _pos("MSFT", 5, avg_cost=350.0)],
    }
    body.update(overrides)
    return body


def test_manual_positions_snapshot_ok() -> None:
    with TestClient(main.app) as client:
        resp = client.post("/api/portfolio/manual/positions", json=_body(cash=1000))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["total_market_value"] == 4000.0
    assert data["equity"] == 5000.0
    assert data["source"] == "manual"
    weights = {p["symbol"]: p["weight_pct"] for p in data["positions"]}
    assert weights["AAPL"] == pytest.approx(40.0, abs=0.1)
    aapl = next(p for p in data["positions"] if p["symbol"] == "AAPL")
    assert aapl["avg_cost"] == 150.0
    assert aapl["pl_pct"] == pytest.approx((200.0 - 150.0) / 150.0 * 100.0)
    assert aapl["acquired_at"] == ACQUIRED


def test_manual_positions_fail_closed_on_unpriced() -> None:
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/portfolio/manual/positions",
            json={"positions": [_pos("AAPL", 1), _pos("ZZZFAKE", 1)]},
        )
    payload = resp.json()
    assert payload["ok"] is False
    assert "ZZZFAKE" in payload["error"]
    assert payload["data"]["unpriced_tickers"] == ["ZZZFAKE"]


def test_manual_positions_validation_422() -> None:
    with TestClient(main.app) as client:
        bad_qty = client.post("/api/portfolio/manual/positions", json={"positions": [_pos("AAPL", 0)]})
        too_many = client.post(
            "/api/portfolio/manual/positions",
            json={"positions": [_pos(f"T{i}", 1) for i in range(16)]},
        )
        empty = client.post("/api/portfolio/manual/positions", json={"positions": []})
        missing_date = client.post(
            "/api/portfolio/manual/positions",
            json={"positions": [{"ticker": "AAPL", "qty": 1, "avg_cost": 10.0}]},
        )
        future_date = client.post(
            "/api/portfolio/manual/positions",
            json={"positions": [_pos("AAPL", 1, acquired_at="2099-01-01")]},
        )
        bad_cost = client.post(
            "/api/portfolio/manual/positions",
            json={"positions": [{"ticker": "AAPL", "qty": 1, "acquired_at": ACQUIRED, "avg_cost": 0}]},
        )
    assert bad_qty.status_code == 422
    assert too_many.status_code == 422
    assert empty.status_code == 422
    assert missing_date.status_code == 422
    assert future_date.status_code == 422
    assert bad_cost.status_code == 422


def test_manual_cache_key_includes_acquired_and_cost() -> None:
    from webapp.schemas import ManualPortfolioBody

    a = ManualPortfolioBody.model_validate(_body())
    b = ManualPortfolioBody.model_validate(_body(positions=[_pos("AAPL", 10, avg_cost=151.0), _pos("MSFT", 5, avg_cost=350.0)]))
    c = ManualPortfolioBody.model_validate(
        _body(positions=[_pos("AAPL", 10, avg_cost=150.0, acquired_at="2023-01-01"), _pos("MSFT", 5, avg_cost=350.0)])
    )
    assert main._manual_cache_key(a) != main._manual_cache_key(b)
    assert main._manual_cache_key(a) != main._manual_cache_key(c)


def test_manual_risk_dashboard_build_cache_and_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import portfolio_analytics_service
    from core.contracts.portfolio import PortfolioRiskDashboardPack

    calls: list[dict[str, Any]] = []

    def fake_build(state, summary, **kwargs):
        calls.append({"equity": state.equity, "lookback": kwargs.get("lookback_days")})
        return PortfolioRiskDashboardPack(equity=state.equity, position_count=len(state.positions))

    monkeypatch.setattr(portfolio_analytics_service, "build_portfolio_risk_dashboard", fake_build)

    with TestClient(main.app) as client:
        first = client.post("/api/portfolio/risk-dashboard/manual", json=_body(cash=1000, lookback_days=60))
        cached = client.post("/api/portfolio/risk-dashboard/manual", json=_body(cash=1000, lookback_days=60))
        limited = client.post("/api/portfolio/risk-dashboard/manual", json=_body(cash=999, lookback_days=60))

    p1 = first.json()
    assert p1["ok"] is True
    assert p1["data"]["source"] == "manual"
    assert p1["data"]["equity"] == 5000.0
    assert p1["data"]["cache_hit"] is False
    assert calls == [{"equity": 5000.0, "lookback": 60}]

    # Identical payload within TTL: served from cache, no second build.
    p2 = cached.json()
    assert p2["ok"] is True
    assert p2["data"]["cache_hit"] is True
    assert len(calls) == 1

    # Different payload within the per-IP interval: rate limited, no build.
    p3 = limited.json()
    assert p3["ok"] is False
    assert "Rate limited" in p3["error"]
    assert p3["data"]["retry_after_sec"] >= 1
    assert len(calls) == 1


def test_manual_risk_dashboard_fail_closed_on_unpriced() -> None:
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/portfolio/risk-dashboard/manual",
            json={"positions": [_pos("ZZZFAKE", 3)]},
        )
    payload = resp.json()
    assert payload["ok"] is False
    assert payload["data"]["unpriced_tickers"] == ["ZZZFAKE"]
