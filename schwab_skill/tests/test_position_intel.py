"""Position Intel analytics + endpoint contract tests.

Covers conviction scoring edges, HV20/GARCH (with EWMA fallback), the
0.40-delta long-call and covered-call selection rules, the lognormal
probability math, the orchestrator's data_quality behaviour, and the
``GET /api/position-intel`` response shape (compute monkeypatched — no
network).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core.position_intel import (
    _prob_above,
    build_position_intel,
    conviction_row,
    garch_forecast,
    hv_annualized,
    select_covered_call,
    select_long_call,
    volatility_row,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _frame(closes: np.ndarray, volumes: np.ndarray | None = None) -> pd.DataFrame:
    n = len(closes)
    if volumes is None:
        volumes = np.full(n, 1_000_000.0)
    return pd.DataFrame(
        {
            "close": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "volume": volumes,
        },
        index=pd.date_range("2024-01-02", periods=n, freq="B"),
    )


def _trending_frame(n: int = 420, drift: float = 0.004, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.015, n)
    return _frame(100 * np.exp(np.cumsum(rets)))


def _contract(strike, delta, dte, *, iv=45.0, oi=800, bid=1.0, ask=1.06):
    return {
        "bid": bid,
        "ask": ask,
        "delta": delta,
        "volatility": iv,
        "daysToExpiration": dte,
        "openInterest": oi,
    }


def _chain(spot: float = 100.0) -> dict:
    return {
        "underlyingPrice": spot,
        "callExpDateMap": {
            "2026-08-10:14": {
                "102.0": [_contract(102.0, 0.42, 14, bid=2.90, ask=3.00)],
                "106.0": [_contract(106.0, 0.28, 14, bid=1.45, ask=1.55)],
                "110.0": [_contract(110.0, 0.17, 14, bid=0.70, ask=0.78)],
            },
            "2026-08-28:32": {
                "104.0": [_contract(104.0, 0.38, 32, bid=3.40, ask=3.55)],
            },
        },
    }


# --------------------------------------------------------------------------- #
# Conviction scorecard
# --------------------------------------------------------------------------- #


def test_conviction_uptrend_scores_buy() -> None:
    row = conviction_row("UP", _trending_frame(drift=0.004))
    assert row["signal"] == "BUY"
    assert row["composite"] >= 1.0
    assert row["votes"]["trend"] == 1


def test_conviction_downtrend_scores_bearish() -> None:
    # Deterministic monotone decline: every indicator that can vote goes bearish.
    n = 420
    closes = 100 * np.exp(np.cumsum(np.full(n, -0.004)))
    row = conviction_row("DOWN", _frame(closes))
    assert row["signal"] in ("SELL", "LEAN BEAR")
    assert row["composite"] <= -0.5
    assert row["votes"]["trend"] == -1


def test_conviction_short_history_returns_none_cells() -> None:
    row = conviction_row("SHORT", _frame(np.linspace(100, 101, 10)))
    assert row["composite"] is None
    assert row["signal"] is None
    assert row["votes"]["trend"] is None


def test_conviction_empty_frame_is_safe() -> None:
    row = conviction_row("EMPTY", pd.DataFrame())
    assert row["composite"] is None
    assert row["rsi"] is None


def test_composite_bounded_to_plus_minus_two() -> None:
    for drift in (0.01, -0.01):
        row = conviction_row("X", _trending_frame(drift=drift))
        assert row["composite"] is not None
        assert -2.0 <= row["composite"] <= 2.0


# --------------------------------------------------------------------------- #
# Volatility: HV20 + GARCH/EWMA
# --------------------------------------------------------------------------- #


def test_hv_annualized_matches_manual_calc() -> None:
    rng = np.random.default_rng(1)
    rets = rng.normal(0, 0.02, 60)
    hv = hv_annualized(rets)
    manual = float(np.std(rets[-20:], ddof=1)) * math.sqrt(252)
    assert hv == pytest.approx(manual)


def test_hv_requires_full_window() -> None:
    assert hv_annualized(np.zeros(10)) is None


def test_garch_forecast_fits_on_long_series() -> None:
    rng = np.random.default_rng(2)
    rets = rng.normal(0, 0.02, 400)
    out = garch_forecast(rets)
    assert out["method"] == "garch"
    assert out["vol"] is not None and 0.05 < out["vol"] < 2.0
    assert out["alpha"] is not None and out["beta"] is not None


def test_garch_falls_back_to_ewma_on_short_series() -> None:
    rng = np.random.default_rng(4)
    rets = rng.normal(0, 0.02, 40)  # below the 60-obs GARCH floor
    out = garch_forecast(rets)
    assert out["method"] == "ewma"
    assert out["vol"] is not None and out["vol"] > 0


def test_garch_no_data_returns_none() -> None:
    out = garch_forecast(np.empty(0))
    assert out["vol"] is None
    assert out["method"] is None


def test_volatility_row_expansion_and_signal_classes() -> None:
    row = volatility_row("VOL", _trending_frame())
    assert row["hv20"] is not None and row["garch"] is not None
    assert row["expansion_pct"] == pytest.approx((row["garch"] - row["hv20"]) / row["hv20"] * 100.0, abs=0.1)
    assert row["signal"] in ("SIGNAL", "BASE", "WEAK")
    assert row["regime"] in ("LOW", "NORMAL", "HIGH", "EXTREME")


# --------------------------------------------------------------------------- #
# Lognormal probability
# --------------------------------------------------------------------------- #


def test_prob_above_monotone_in_level() -> None:
    lo = _prob_above(100.0, 95.0, 0.5, 14)
    hi = _prob_above(100.0, 120.0, 0.5, 14)
    assert lo > 0.5 > hi


def test_prob_above_invalid_inputs_return_none() -> None:
    assert _prob_above(0.0, 100.0, 0.5, 14) is None
    assert _prob_above(100.0, 100.0, 0.0, 14) is None
    assert _prob_above(100.0, 100.0, 0.5, 0) is None


# --------------------------------------------------------------------------- #
# Long-call selection
# --------------------------------------------------------------------------- #


def test_long_call_picks_strike_nearest_forty_delta() -> None:
    out = select_long_call(_chain(), 100.0, garch_vol=0.60)
    assert out is not None
    assert out["strike"] == 102.0
    assert out["dte"] == 14
    assert out["delta"] == 0.42
    # B/E = (102 + 2.95 - 100) / 100 = 4.95%, rounded to one decimal
    assert out["breakeven_pct"] == pytest.approx(4.95, abs=0.06)
    # EDGE = (0.60 - 0.45) / 0.45
    assert out["edge_pct"] == pytest.approx(33.3, abs=0.1)
    assert 0.0 < out["p_win"] < 1.0


def test_long_call_falls_back_to_wider_dte_window() -> None:
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2026-08-28:32": {"104.0": [_contract(104.0, 0.38, 32, bid=3.40, ask=3.55)]}},
    }
    out = select_long_call(chain, 100.0, garch_vol=0.60)
    assert out is not None
    assert out["dte"] == 32


def test_long_call_skips_untradeable_contracts() -> None:
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {
            "2026-08-10:14": {
                "102.0": [_contract(102.0, 0.42, 14, oi=3)],  # OI below floor
                "106.0": [_contract(106.0, 0.30, 14, bid=0.0, ask=0.5)],  # no bid
            }
        },
    }
    assert select_long_call(chain, 100.0, garch_vol=0.60) is None


def test_long_call_none_without_chain_or_spot() -> None:
    assert select_long_call(None, 100.0, 0.5) is None
    assert select_long_call(_chain(), None, 0.5) is None


# --------------------------------------------------------------------------- #
# Covered-call selection
# --------------------------------------------------------------------------- #


def test_covered_call_otm_delta_band_and_annualized_yield() -> None:
    out = select_covered_call(_chain(), 100.0, garch_vol=0.60)
    assert out is not None
    assert out["strike"] > 100.0
    assert 0.15 <= out["delta"] <= 0.40
    assert out["yield_pct"] == pytest.approx(out["mid"] / 100.0 * 100.0, abs=0.01)
    assert out["annualized_pct"] == pytest.approx(out["yield_pct"] * 365.0 / out["dte"], abs=0.1)
    assert 0.0 < out["p_keep"] < 1.0


def test_covered_call_excludes_itm_strikes() -> None:
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {"2026-08-10:14": {"95.0": [_contract(95.0, 0.70, 14, bid=6.0, ask=6.2)]}},
    }
    assert select_covered_call(chain, 100.0, garch_vol=0.60) is None


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


def test_build_position_intel_full_payload() -> None:
    df = _trending_frame()
    spot = float(df["close"].iloc[-1])
    chain = _chain(spot)
    chain["callExpDateMap"] = {
        "2026-08-10:14": {
            str(round(spot * 1.02, 1)): [_contract(round(spot * 1.02, 1), 0.42, 14, bid=2.9, ask=3.0)],
            str(round(spot * 1.08, 1)): [_contract(round(spot * 1.08, 1), 0.25, 14, bid=1.2, ask=1.3)],
        }
    }
    payload = build_position_intel(
        [{"symbol": "TEST", "qty": 150, "last": spot}],
        history_fetcher=lambda t: df,
        chain_fetcher=lambda t: chain,
    )
    assert payload["summary"]["positions"] == 1
    assert len(payload["conviction"]) == 1
    assert len(payload["volatility"]) == 1
    assert len(payload["options_setup"]) == 1
    assert len(payload["covered_calls"]) == 1
    assert payload["covered_calls"][0]["shares"] == 150


def test_build_position_intel_skips_option_symbols() -> None:
    payload = build_position_intel(
        [{"symbol": "MXCT  270115C00002500", "qty": 1, "last": 1.0}],
        history_fetcher=lambda t: pd.DataFrame(),
        chain_fetcher=lambda t: None,
    )
    assert payload["summary"]["positions"] == 0
    assert payload["conviction"] == []


def test_build_position_intel_reports_degraded_data_verbatim() -> None:
    payload = build_position_intel(
        [{"symbol": "NODATA", "qty": 10, "last": 50.0}],
        history_fetcher=lambda t: pd.DataFrame(),
        chain_fetcher=lambda t: None,
    )
    notes = payload["data_quality"]
    assert any("price history unavailable" in n for n in notes)
    assert any("option chain unavailable" in n for n in notes)
    row = payload["conviction"][0]
    assert row["composite"] is None  # never fabricated


def test_build_position_intel_survives_fetcher_exceptions() -> None:
    def _boom(_t: str):
        raise RuntimeError("provider down")

    payload = build_position_intel(
        [{"symbol": "BOOM", "qty": 5, "last": 10.0}],
        history_fetcher=_boom,
        chain_fetcher=_boom,
    )
    assert payload["summary"]["positions"] == 1
    assert any("BOOM" in n for n in payload["data_quality"])


# --------------------------------------------------------------------------- #
# Endpoint contract
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("WEB_API_KEY", "test-key-123")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    from webapp import main as webapp_main

    with TestClient(webapp_main.app) as c:
        yield c


def _fake_payload() -> dict:
    return {
        "conviction": [{"ticker": "TEST", "rsi": 61.0, "votes": {}, "composite": 1.33, "signal": "BUY"}],
        "volatility": [],
        "options_setup": [],
        "covered_calls": [],
        "summary": {"positions": 1},
        "garch_params": {},
        "data_quality": [],
        "meta": {"computed_at": "2026-07-27T00:00:00+00:00", "cache_ttl_sec": 300, "positions_total": 1},
    }


def test_position_intel_endpoint_shape_and_cache(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from webapp.routes import position_intel as pi

    calls = {"n": 0}

    def _fake_compute() -> dict:
        calls["n"] += 1
        return _fake_payload()

    monkeypatch.setattr(pi, "_compute_payload", _fake_compute)
    monkeypatch.setitem(pi._cache, "payload", None)
    monkeypatch.setitem(pi._cache, "at", 0.0)

    headers = {"X-API-Key": "test-key-123"}
    first = client.get("/api/position-intel", headers=headers).json()
    assert first["ok"] is True
    data = first["data"]
    for key in ("conviction", "volatility", "options_setup", "covered_calls", "summary", "data_quality", "meta"):
        assert key in data
    assert data["meta"]["cache_hit"] is False
    assert calls["n"] == 1

    second = client.get("/api/position-intel", headers=headers).json()
    assert second["data"]["meta"]["cache_hit"] is True
    assert calls["n"] == 1  # served from TTL cache

    third = client.get("/api/position-intel?refresh=1", headers=headers).json()
    assert third["data"]["meta"]["cache_hit"] is False
    assert calls["n"] == 2  # refresh forces recompute


def test_position_intel_endpoint_surfaces_errors(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from webapp.routes import position_intel as pi

    def _fail() -> dict:
        raise RuntimeError("Schwab account status unavailable")

    monkeypatch.setattr(pi, "_compute_payload", _fail)
    monkeypatch.setitem(pi._cache, "payload", None)
    monkeypatch.setitem(pi._cache, "at", 0.0)

    out = client.get("/api/position-intel?refresh=1", headers={"X-API-Key": "test-key-123"}).json()
    assert out["ok"] is False
    assert "unavailable" in (out["error"] or "").lower()


def test_position_intel_lookup_validates_ticker(client: TestClient) -> None:
    headers = {"X-API-Key": "test-key-123"}
    bad = client.get("/api/position-intel/lookup", params={"ticker": "!!!"}, headers=headers).json()
    assert bad["ok"] is False
    assert "invalid ticker" in (bad["error"] or "").lower()


def test_position_intel_lookup_shape_and_cache(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from webapp.routes import position_intel as pi

    calls: list[str] = []

    def _fake_lookup(ticker: str) -> dict:
        calls.append(ticker)
        payload = _fake_payload()
        payload["meta"] = {
            "computed_at": "2026-07-27T00:00:00+00:00",
            "cache_ttl_sec": 300,
            "mode": "lookup",
            "ticker": ticker,
            "held": False,
            "shares": 0,
        }
        return payload

    monkeypatch.setattr(pi, "_compute_lookup_payload", _fake_lookup)
    with pi._lookup_lock:
        pi._lookup_cache.clear()

    headers = {"X-API-Key": "test-key-123"}
    first = client.get("/api/position-intel/lookup", params={"ticker": "aapl"}, headers=headers).json()
    assert first["ok"] is True
    assert first["data"]["meta"]["ticker"] == "AAPL"
    assert first["data"]["meta"]["mode"] == "lookup"
    assert first["data"]["meta"]["cache_hit"] is False
    assert calls == ["AAPL"]

    second = client.get("/api/position-intel/lookup", params={"ticker": "AAPL"}, headers=headers).json()
    assert second["data"]["meta"]["cache_hit"] is True
    assert calls == ["AAPL"]  # cache hit

    third = client.get(
        "/api/position-intel/lookup",
        params={"ticker": "AAPL", "refresh": 1},
        headers=headers,
    ).json()
    assert third["data"]["meta"]["cache_hit"] is False
    assert calls == ["AAPL", "AAPL"]


def test_normalize_ticker_accepts_common_forms() -> None:
    from webapp.routes.position_intel import _normalize_ticker

    assert _normalize_ticker(" aapl ") == "AAPL"
    assert _normalize_ticker("BRK.B") == "BRK.B"
    assert _normalize_ticker("") is None
    assert _normalize_ticker("TOO_LONG_TICKER_NAME") is None
    assert _normalize_ticker("123") is None
