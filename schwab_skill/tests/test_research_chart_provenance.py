"""Chart and quick-check payloads must carry ticker identity and as-of provenance."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from webapp.routes.research import _chart_payload, _history_as_of


def test_chart_payload_includes_provider_and_as_of() -> None:
    idx = pd.date_range("2026-08-20", periods=3, freq="B")
    df = pd.DataFrame(
        {
            "open": [1.0, 2.0, 3.0],
            "high": [1.0, 2.0, 3.0],
            "low": [1.0, 2.0, 3.0],
            "close": [1.0, 2.0, 3.0],
            "volume": [10, 20, 30],
        },
        index=idx,
    )
    candles = [
        {"time": int(ts.timestamp()), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 10} for ts in idx
    ]
    meta = {
        "provider": "schwab",
        "used_fallback": False,
        "fallback_reason": None,
        "history_price_basis": "schwab_vendor_daily",
    }
    payload = _chart_payload("aapl", df, meta, candles)
    assert payload["ticker"] == "AAPL"
    assert payload["provider"] == "schwab"
    assert payload["used_fallback"] is False
    assert payload["history_price_basis"] == "schwab_vendor_daily"
    assert payload["as_of"] == datetime.fromtimestamp(candles[-1]["time"], tz=UTC).date().isoformat()
    assert len(payload["candles"]) == 3


def test_history_as_of_from_dataframe_index() -> None:
    df = pd.DataFrame({"close": [10.0]}, index=pd.DatetimeIndex(["2026-08-21"]))
    assert _history_as_of(df) == "2026-08-21"
    assert _history_as_of(pd.DataFrame()) is None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import market_data
    from webapp import main as webapp_main
    from webapp.routes import research

    idx = pd.date_range("2026-08-18", periods=2, freq="B")
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1_000, 1_100],
        },
        index=idx,
    )
    meta = {
        "provider": "yfinance",
        "used_fallback": True,
        "fallback_reason": "Timeout",
        "history_price_basis": "yfinance_adjusted",
    }
    monkeypatch.setattr(market_data, "get_daily_history_with_meta", lambda *a, **k: (df, meta))
    monkeypatch.setattr(research, "DualSchwabAuth", lambda **kwargs: object())
    app = webapp_main.app
    with TestClient(app) as test_client:
        yield test_client


def test_chart_api_success_includes_provider_and_as_of(client: TestClient) -> None:
    resp = client.get("/api/chart/MSFT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["ticker"] == "MSFT"
    assert data["provider"] == "yfinance"
    assert data["used_fallback"] is True
    assert data["history_price_basis"] == "yfinance_adjusted"
    assert data["as_of"]
    assert data["candles"]
