"""Phase 1: cockpit service lane builders, pre-trade gates, order-intent preview."""

from __future__ import annotations

from core import cockpit_service, pretrade_gates


def _signal(ticker: str = "AAPL", rank: float = 80.0, **extra) -> dict:
    base = {
        "ticker": ticker,
        "price": 100.0,
        "rank_score": rank,
        "composite_score": rank - 2,
        "signal_score": rank - 5,
        "avg_vol_50": 5_000_000,
        "breakout_confirmed": True,
        "strategy_attribution": {"top_live": "trend_breakout"},
        "advisory": {"confidence_bucket": "high"},
        "data_provider": "schwab",
        "_data_quality": "ok",
        "_filter_status": "kept",
    }
    base.update(extra)
    return base


# --------------------------------------------------------------------------- #
# Pre-trade gates
# --------------------------------------------------------------------------- #
def test_spread_bps_basic() -> None:
    assert pretrade_gates.spread_bps(99.9, 100.1) == 20.0
    assert pretrade_gates.spread_bps(None, 100.0) is None
    assert pretrade_gates.spread_bps(101.0, 100.0) is None  # crossed


def test_compute_checks_all_clear() -> None:
    checks = pretrade_gates.compute_checks(price=100.0, bid=99.95, ask=100.05, quote_age_sec=5.0, avg_vol_50=5_000_000)
    assert checks.tradeable is True
    assert checks.quote_fresh is True
    assert checks.liquidity_ok is True
    assert checks.event_risk == "none"
    assert checks.blockers == []


def test_compute_checks_flags_blockers() -> None:
    checks = pretrade_gates.compute_checks(
        price=1.0,
        bid=1.0,
        ask=1.5,  # huge spread
        quote_age_sec=9999.0,  # stale
        avg_vol_50=10.0,  # illiquid
        event_risk={"flagged": True, "reasons": ["earnings"]},
    )
    assert checks.tradeable is False
    assert any("spread_too_wide" in b for b in checks.blockers)
    assert any("quote_stale" in b for b in checks.blockers)
    assert "low_liquidity" in checks.blockers
    assert any("event_risk" in b for b in checks.blockers)


def test_from_signal_has_no_spread_but_liquidity() -> None:
    checks = pretrade_gates.from_signal(_signal())
    assert checks.spread_bps is None
    assert checks.liquidity_ok is True


# --------------------------------------------------------------------------- #
# Lane builders
# --------------------------------------------------------------------------- #
def test_build_market_from_diagnostics() -> None:
    out = cockpit_service.build_market(
        {"regime_bullish": True, "spy_price": 500.0, "spy_sma_200": 460.0, "scan_blocked": 0}
    )
    assert out["is_regime_bullish"] is True
    assert out["regime_state"] in {"bullish", "neutral"}
    assert "provenance" in out


def test_build_opportunities_sorted_with_pretrade() -> None:
    cards = cockpit_service.build_opportunities(
        [_signal("AAPL", 60.0), _signal("MSFT", 90.0)],
    )
    assert [c["ticker"] for c in cards] == ["MSFT", "AAPL"]
    assert cards[0]["pre_trade"]["liquidity_ok"] is True
    assert cards[0]["rank"]["rank_score"] == 90.0


def test_build_opportunities_includes_filtered_shortlist() -> None:
    cards = cockpit_service.build_opportunities(
        [_signal("AAPL", 80.0)],
        shortlist=[
            _signal("NVDA", 70.0, _filter_status="filtered_quality_gates", _filter_reasons=["low_signal_score"])
        ],
    )
    tickers = {c["ticker"] for c in cards}
    assert tickers == {"AAPL", "NVDA"}
    nvda = next(c for c in cards if c["ticker"] == "NVDA")
    assert nvda["gate_status"]["disposition"] == "filtered_quality_gates"


def test_build_opportunities_respects_limit() -> None:
    cards = cockpit_service.build_opportunities(
        [_signal("AAPL", 80.0), _signal("MSFT", 90.0), _signal("NVDA", 70.0)],
        limit=2,
    )
    assert len(cards) == 2
    assert cards[0]["ticker"] == "MSFT"


def test_build_portfolio() -> None:
    out = cockpit_service.build_portfolio(
        {"accounts": [{"securitiesAccount": {"currentBalances": {"liquidationValue": 1000.0}, "positions": []}}]}
    )
    assert out["equity"] == 1000.0
    assert out["provenance"]["source"] == "schwab"


def test_build_blotter() -> None:
    out = cockpit_service.build_blotter(
        [{"id": "t1", "ticker": "AAPL", "side": "BUY", "qty": 5, "status": "pending", "price": 100.0}]
    )
    assert len(out) == 1
    assert out[0]["state"] == "pending_approval"


# --------------------------------------------------------------------------- #
# Order-intent preview
# --------------------------------------------------------------------------- #
def test_order_intent_preview_clear() -> None:
    out = cockpit_service.build_order_intent_preview(
        ticker="aapl",
        qty=10,
        price=100.0,
        signal=_signal(),
        bid=99.95,
        ask=100.05,
        quote_age_sec=2.0,
    )
    assert out["ticker"] == "AAPL"
    assert out["state"] == "staged"
    assert out["shadow"] is True
    assert out["quality"]["expected_price"] == 100.0
    assert out["pre_trade"]["tradeable"] is True
    assert out["reason"] is None


def test_order_intent_preview_gated_reports_reason() -> None:
    out = cockpit_service.build_order_intent_preview(
        ticker="XYZ",
        qty=10,
        price=1.0,
        signal=_signal("XYZ", avg_vol_50=1.0),
        bid=1.0,
        ask=1.4,
        quote_age_sec=99999.0,
    )
    assert out["pre_trade"]["tradeable"] is False
    assert out["reason"]
    assert "gates_mode" in out
