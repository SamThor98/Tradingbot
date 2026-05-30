from __future__ import annotations

import pandas as pd

import config
import kronos_client


def _make_df(rows: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=rows, freq="B")
    base = 100.0
    data = {
        "open": [base + i * 0.1 for i in range(rows)],
        "high": [base + i * 0.1 + 0.5 for i in range(rows)],
        "low": [base + i * 0.1 - 0.5 for i in range(rows)],
        "close": [base + i * 0.1 + 0.2 for i in range(rows)],
        "volume": [1_000_000 + i for i in range(rows)],
    }
    return pd.DataFrame(data, index=idx)


def test_kronos_mode_defaults_off(monkeypatch):
    monkeypatch.delenv("KRONOS_MODE", raising=False)
    config.clear_env_cache()
    assert config.get_kronos_mode() == "off"
    assert config.get_kronos_enabled() is False


def test_kronos_mode_shadow(monkeypatch):
    monkeypatch.setenv("KRONOS_MODE", "shadow")
    config.clear_env_cache()
    assert config.get_kronos_mode() == "shadow"
    assert config.get_kronos_enabled() is True


def test_forecast_returns_none_when_service_down(monkeypatch):
    """Service unreachable -> graceful degradation (None), never raises."""
    import requests

    def _boom(*_args, **_kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "post", _boom)
    out = kronos_client.forecast("AAPL", _make_df(60))
    assert out is None


def test_forecast_returns_none_on_insufficient_history():
    out = kronos_client.forecast("AAPL", _make_df(10))
    assert out is None


def test_forecast_parses_ok_response(monkeypatch):
    import requests

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "data": {
                    "symbol": "AAPL",
                    "model_id": "NeoQuasar/Kronos-small",
                    "pred_len": 24,
                    "direction": "up",
                    "expected_return_pct": 3.2,
                    "confidence": 0.8,
                    "last_close": 110.0,
                    "final_close": 113.5,
                    "forecast_candles": [{"time": 1, "open": 110, "high": 111, "low": 109, "close": 110.5}],
                },
            }

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    out = kronos_client.forecast("AAPL", _make_df(60))
    assert out is not None
    assert out.direction == "up"
    assert out.confidence_bucket == "high"
    payload = out.to_dict()
    assert payload["source"] == "kronos"
    assert payload["forecast_candles"]


def test_forecast_returns_none_on_error_body(monkeypatch):
    import requests

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": False, "error": "model_not_loaded"}

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    assert kronos_client.forecast("AAPL", _make_df(60)) is None
