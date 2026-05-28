"""Phase 0 cockpit scaffolding: contracts, providers, catalog, observability."""

from __future__ import annotations

from core import endpoint_catalog as cat
from core import observability as obs
from core.contracts import (
    ExecutionState,
    MarketSnapshot,
    PortfolioRiskState,
    Provenance,
    SymbolDecisionCard,
)
from core.providers import (
    ExecutionProvider,
    MarketContextProvider,
    PortfolioProvider,
    SymbolIntelProvider,
)
from core.providers.execution_provider import normalize_state


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_provenance_schwab_primary_is_high_confidence() -> None:
    p = Provenance.from_lineage({"provider": "schwab", "used_fallback": False, "data_quality": "ok"})
    assert p.source == "schwab"
    assert p.confidence == "high"
    assert p.is_stale is False


def test_provenance_fallback_is_medium_confidence() -> None:
    p = Provenance.from_lineage({"provider": "yfinance", "used_fallback": True, "fallback_reason": "HTTPError"})
    assert p.source == "yfinance"
    assert p.confidence == "medium"
    assert p.stale_reason == "HTTPError"


def test_provenance_conflict_is_low_and_stale() -> None:
    p = Provenance.from_lineage({"provider": "schwab", "data_quality": "conflict"})
    assert p.confidence == "low"
    assert p.is_stale is True


def test_provenance_fallback_provider_wins_over_provider() -> None:
    p = Provenance.from_lineage({"provider": "schwab", "fallback_provider": "polygon", "used_fallback": True})
    assert p.source == "polygon"


# --------------------------------------------------------------------------- #
# SymbolIntelProvider
# --------------------------------------------------------------------------- #
def _signal_fixture() -> dict:
    return {
        "ticker": "aapl",
        "price": 195.12,
        "rank_score": 81.4,
        "composite_score": 78.0,
        "signal_score": 72.0,
        "edge_score": 70.0,
        "reliability_score": 88.0,
        "execution_score": 90.0,
        "p_up_calibrated": 0.61,
        "ev_10d": 0.0123,
        "rank_basis": "high_level_v1",
        "sector_etf": "XLK",
        "breakout_confirmed": True,
        "sma_50": 180.0,
        "sma_200": 165.0,
        "score_components": {"stage2": True},
        "advisory": {"confidence_bucket": "high", "expected_move_10d": 0.05},
        "strategy_attribution": {"top_live": "trend_breakout"},
        "mirofish_conviction": 0.7,
        "sec_risk_tag": "low",
        "forensic_flags": [],
        "pead_beat": True,
        "pead_surprise_pct": 8.2,
        "reliability_reasons": ["schwab_primary", "stage2_confirmed"],
        "data_provider": "schwab",
        "used_fallback_data": False,
        "_data_quality": "ok",
        "_filter_status": "kept",
    }


def test_symbol_normalize_signal_maps_core_fields() -> None:
    card = SymbolIntelProvider.normalize_signal(_signal_fixture())
    assert isinstance(card, SymbolDecisionCard)
    assert card.ticker == "AAPL"
    assert card.price == 195.12
    assert card.rank.rank_score == 81.4
    assert card.setup.stage2 is True
    assert card.setup.breakout_confirmed is True
    assert card.setup.strategy_top_live == "trend_breakout"
    assert card.confidence.bucket == "high"
    assert card.gate_status.disposition == "kept"
    assert card.provenance.confidence == "high"
    assert "schwab_primary" in card.key_reasons


def test_symbol_unknown_disposition_falls_back() -> None:
    sig = _signal_fixture()
    sig["_filter_status"] = "totally_made_up"
    card = SymbolIntelProvider.normalize_signal(sig)
    assert card.gate_status.disposition == "unknown"


def test_symbol_apply_decision_card_merges_trade_plan() -> None:
    card = SymbolIntelProvider.normalize_signal(_signal_fixture())
    dc = {
        "entry_zone": [194.0, 196.0],
        "stop_invalidation": 188.5,
        "size": {"qty": 10, "usd": 1951.2},
        "key_reasons": ["tight VCP", "sector leader"],
        "block_reason": None,
    }
    card = SymbolIntelProvider.apply_decision_card(card, dc)
    assert card.trade_plan.size_qty == 10
    assert card.trade_plan.stop_invalidation == 188.5
    assert card.key_reasons[0] == "tight VCP"


def test_symbol_normalize_many_skips_non_dict() -> None:
    cards = SymbolIntelProvider.normalize_many([_signal_fixture(), None, "bad", {"ticker": "msft"}])
    assert [c.ticker for c in cards] == ["AAPL", "MSFT"]


# --------------------------------------------------------------------------- #
# PortfolioProvider
# --------------------------------------------------------------------------- #
def _account_fixture() -> dict:
    return {
        "accounts": [
            {
                "securitiesAccount": {
                    "currentBalances": {
                        "liquidationValue": 100000.0,
                        "cashBalance": 20000.0,
                        "buyingPower": 40000.0,
                    },
                    "positions": [
                        {
                            "instrument": {"symbol": "AAPL"},
                            "longQuantity": 100,
                            "marketValue": 20000.0,
                            "averagePrice": 180.0,
                            "currentDayProfitLoss": 500.0,
                        },
                        {
                            "instrument": {"symbol": "MSFT"},
                            "longQuantity": 50,
                            "marketValue": 30000.0,
                            "averagePrice": 400.0,
                            "currentDayProfitLoss": -250.0,
                        },
                    ],
                }
            }
        ]
    }


def test_portfolio_normalize_account_balances_and_positions() -> None:
    state = PortfolioProvider.normalize_account(_account_fixture())
    assert isinstance(state, PortfolioRiskState)
    assert state.equity == 100000.0
    assert state.cash == 20000.0
    assert len(state.positions) == 2
    aapl = next(p for p in state.positions if p.ticker == "AAPL")
    assert aapl.qty == 100
    assert aapl.weight_pct == 20.0
    # gross = 50k of 100k equity
    assert state.exposure.gross_pct == 50.0
    assert state.concentration.top1_pct == 30.0  # MSFT 30k
    assert state.concentration.top5_pct == 50.0
    assert state.provenance.source == "schwab"


def test_portfolio_sector_lookup_populates_exposure() -> None:
    lookup = {"AAPL": "XLK", "MSFT": "XLK"}.get
    state = PortfolioProvider.normalize_account(_account_fixture(), sector_lookup=lookup)
    assert state.exposure.by_sector.get("XLK") == 50.0


def test_portfolio_error_string_is_low_confidence() -> None:
    state = PortfolioProvider.normalize_account({"accounts": []})
    assert state.equity is None
    assert state.positions == []


# --------------------------------------------------------------------------- #
# MarketContextProvider
# --------------------------------------------------------------------------- #
def test_market_normalize_bullish_high_bucket() -> None:
    snap = MarketContextProvider.normalize(
        regime_ctx={"bullish": True, "price": 500.0, "sma_200": 450.0},
        regime_v2={"score": 80.0, "bucket": "high"},
        winning_sectors=[{"etf": "XLK", "is_winning": True}, "XLF"],
        vix_level=12.0,
    )
    assert isinstance(snap, MarketSnapshot)
    assert snap.regime_state == "bullish"
    assert snap.is_regime_bullish is True
    assert snap.volatility_state == "low"
    assert snap.sector_breadth[0].etf == "XLK"
    assert snap.sector_breadth[1].etf == "XLF"


def test_market_bearish_when_not_bullish() -> None:
    snap = MarketContextProvider.normalize(
        regime_ctx={"bullish": False, "price": 400.0, "sma_200": 450.0},
        vix_level=35.0,
    )
    assert snap.regime_state == "bearish"
    assert snap.volatility_state == "extreme"


def test_market_from_diagnostics() -> None:
    snap = MarketContextProvider.from_diagnostics(
        {"regime_bullish": True, "spy_price": 500.0, "spy_sma_200": 460.0, "scan_blocked": 0}
    )
    assert snap.is_regime_bullish is True
    assert snap.spy_price == 500.0
    assert snap.scan_blocked_by_regime is False


# --------------------------------------------------------------------------- #
# ExecutionProvider
# --------------------------------------------------------------------------- #
def test_normalize_state_mapping() -> None:
    assert normalize_state("FILLED") == "filled"
    assert normalize_state("Working") == "working"
    assert normalize_state("CANCELED") == "cancelled"
    assert normalize_state("pending") == "pending_approval"
    assert normalize_state("queued") == "queued"
    assert normalize_state(None) == "unknown"
    assert normalize_state("nonsense") == "unknown"


def test_execution_from_pending_trade() -> None:
    state = ExecutionProvider.from_pending_trade(
        {"id": 42, "ticker": "tsla", "side": "buy", "qty": 5, "status": "pending", "price": 250.0}
    )
    assert isinstance(state, ExecutionState)
    assert state.ticker == "TSLA"
    assert state.state == "pending_approval"
    assert state.intent.limit_price == 250.0
    assert state.is_terminal is False


def test_execution_from_order_result_with_quality() -> None:
    state = ExecutionProvider.from_order_result(
        {
            "order_id": "X1",
            "ticker": "NVDA",
            "side": "BUY",
            "qty": 3,
            "status": "filled",
            "fill_price": 120.5,
            "_execution_quality": {
                "expected_price": 120.0,
                "realized_slippage_bps": 41.6,
                "spread_bps": 5.0,
                "reprice_count": 2,
                "latency_ms": 350.0,
            },
        }
    )
    assert state.state == "filled"
    assert state.is_terminal is True
    assert state.fills.avg_fill_price == 120.5
    assert state.fills.filled_qty == 3
    assert state.quality.realized_slippage_bps == 41.6
    assert state.quality.reprice_count == 2


# --------------------------------------------------------------------------- #
# Endpoint catalog
# --------------------------------------------------------------------------- #
def test_catalog_has_live_and_gap_entries() -> None:
    assert "marketdata.quotes" in cat.live_keys()
    assert "marketdata.options.chains" in cat.gap_keys()
    assert cat.get_endpoint("trader.orders.place").engine == "execution"


def test_catalog_coverage_summary_shape() -> None:
    summary = cat.coverage_summary()
    assert set(summary.keys()) <= {
        "market_context",
        "symbol_intel",
        "portfolio_risk",
        "execution",
        "reliability",
    }
    for counts in summary.values():
        assert "live" in counts and "gap" in counts


def test_catalog_keys_unique() -> None:
    keys = [e.key for e in cat.all_endpoints()]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #
def test_observability_emit_and_summarize(tmp_path) -> None:
    obs.record_request_latency(tmp_path, "marketdata.quotes", "market", 123.0)
    obs.record_request_latency(tmp_path, "marketdata.quotes", "market", 77.0)
    obs.record_request_error(tmp_path, "marketdata.quotes", 429)
    obs.record_fallback(tmp_path, "yfinance", "HTTPError")
    obs.record_provider_confidence(tmp_path, "market", "high")
    obs.set_stale_ratio(tmp_path, "market", 0.05)

    summary = obs.get_observability_summary(tmp_path, days=1)
    lat_key = "schwab_request_latency_ms{endpoint=marketdata.quotes,session=market}"
    assert summary["latency_avg_ms"][lat_key] == 100.0
    assert summary["latency_max_ms"][lat_key] == 123.0
    assert summary["counters"]["schwab_request_errors_total{endpoint=marketdata.quotes,http_status=429}"] == 1
    assert summary["counters"]["data_fallback_total{provider=yfinance,reason=HTTPError}"] == 1
    assert summary["gauges"]["data_stale_ratio{domain=market}"] == 0.05


def test_observability_observe_lineage(tmp_path) -> None:
    obs.observe_lineage(
        tmp_path,
        "market",
        {"provider": "yfinance", "used_fallback": True, "fallback_reason": "timeout"},
    )
    summary = obs.get_observability_summary(tmp_path, days=1)
    assert summary["counters"]["data_fallback_total{provider=yfinance,reason=timeout}"] == 1
    assert summary["counters"]["provider_confidence_total{confidence=medium,domain=market}"] == 1


def test_timed_request_records_latency(tmp_path) -> None:
    with obs.timed_request(tmp_path, "trader.orders", "account"):
        pass
    summary = obs.get_observability_summary(tmp_path, days=1)
    keys = list(summary["latency_avg_ms"].keys())
    assert any("trader.orders" in k for k in keys)
