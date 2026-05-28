"""Phase 2: scan deltas, adaptive watchlists, movers + options normalizers."""

from __future__ import annotations

from core import cockpit_service, scan_delta
from core.providers import MarketContextProvider, OptionsProvider


def _sig(ticker, rank, **extra):
    base = {"ticker": ticker, "rank_score": rank, "_filter_status": "kept"}
    base.update(extra)
    return base


# --------------------------------------------------------------------------- #
# scan_delta
# --------------------------------------------------------------------------- #
def test_compute_delta_new_dropped_and_moves() -> None:
    prev = [_sig("AAPL", 70), _sig("MSFT", 60), _sig("OLD", 50)]
    curr = [_sig("AAPL", 80), _sig("MSFT", 58), _sig("NEW", 90)]
    d = scan_delta.compute_delta(prev, curr)
    assert d["new_tickers"] == ["NEW"]
    assert d["dropped_tickers"] == ["OLD"]
    assert d["has_prior"] is True
    moves = {m["ticker"]: m["delta"] for m in d["rank_moves"]}
    assert moves["AAPL"] == 10.0
    assert moves["MSFT"] == -2.0
    # biggest absolute move sorts first
    assert d["rank_moves"][0]["ticker"] == "AAPL"


def test_compute_delta_gate_flips() -> None:
    prev = [_sig("AAPL", 70, _filter_status="kept")]
    curr = [_sig("AAPL", 70, _filter_status="filtered_quality_gates")]
    d = scan_delta.compute_delta(prev, curr)
    assert d["gate_flips"] == [{"ticker": "AAPL", "from": "kept", "to": "filtered_quality_gates"}]


def test_compute_delta_no_prior() -> None:
    d = scan_delta.compute_delta([], [_sig("AAPL", 80)])
    assert d["has_prior"] is False
    assert d["new_tickers"] == ["AAPL"]


def test_watchlist_breaking_out_now() -> None:
    prev = [_sig("AAPL", 70, breakout_confirmed=False)]
    curr = [_sig("AAPL", 75, breakout_confirmed=True), _sig("NEW", 80, breakout_confirmed=True)]
    wl = scan_delta.adaptive_watchlists(prev, curr)
    tickers = {r["ticker"] for r in wl["breaking_out_now"]}
    assert tickers == {"AAPL", "NEW"}
    new_row = next(r for r in wl["breaking_out_now"] if r["ticker"] == "NEW")
    assert new_row["new"] is True


def test_watchlist_setup_improving_threshold() -> None:
    prev = [_sig("AAPL", 70), _sig("MSFT", 70)]
    curr = [_sig("AAPL", 80), _sig("MSFT", 72)]  # AAPL +10, MSFT +2
    wl = scan_delta.adaptive_watchlists(prev, curr)  # default improve_min=5
    improving = {r["ticker"] for r in wl["setup_improving"]}
    assert improving == {"AAPL"}


def test_watchlist_risk_rising() -> None:
    prev = [_sig("AAPL", 70, sec_risk_tag="low", forensic_flags=[])]
    curr = [_sig("AAPL", 70, sec_risk_tag="high", forensic_flags=["beneish_manipulator"])]
    wl = scan_delta.adaptive_watchlists(prev, curr)
    assert wl["risk_rising"]
    reasons = wl["risk_rising"][0]["reasons"]
    assert any("sec_risk" in r for r in reasons)
    assert any("forensic" in r for r in reasons)


# --------------------------------------------------------------------------- #
# cockpit_service wrappers
# --------------------------------------------------------------------------- #
def test_service_build_deltas_and_watchlists() -> None:
    prev = {"signals": [_sig("AAPL", 70)]}
    curr = {"signals": [_sig("AAPL", 80), _sig("NEW", 90)]}
    d = cockpit_service.build_deltas(prev, curr)
    assert d["new_tickers"] == ["NEW"]
    wl = cockpit_service.build_watchlists(prev, curr)
    assert "setup_improving" in wl


# --------------------------------------------------------------------------- #
# Movers normalizer
# --------------------------------------------------------------------------- #
def test_normalize_movers() -> None:
    payload = {
        "screeners": [
            {"symbol": "AAA", "netPercentChange": 5.0, "volume": 1000},
            {"symbol": "BBB", "netPercentChange": -3.0, "volume": 9000},
            {"symbol": "CCC", "netPercentChange": 1.0, "volume": 5000},
        ]
    }
    out = MarketContextProvider.normalize_movers(payload)
    assert out["gainers"][0] == "AAA"
    assert out["losers"][0] == "BBB"
    assert out["most_active"][0] == "BBB"


def test_normalize_movers_empty() -> None:
    out = MarketContextProvider.normalize_movers(None)
    assert out == {"gainers": [], "losers": [], "most_active": []}


# --------------------------------------------------------------------------- #
# Options normalizer
# --------------------------------------------------------------------------- #
def test_normalize_chain_computes_iv_skew_move() -> None:
    chain = {
        "underlyingPrice": 100.0,
        "callExpDateMap": {
            "2026-06-19:30": {
                "100.0": [{"volatility": 30.0, "mark": 3.0}],
                "105.0": [{"volatility": 28.0, "mark": 1.0}],
            }
        },
        "putExpDateMap": {
            "2026-06-19:30": {
                "100.0": [{"volatility": 34.0, "mark": 2.5}],
            }
        },
    }
    intel = OptionsProvider.normalize_chain(chain)
    assert intel.atm_iv == round(((30.0 + 34.0) / 2) / 100, 4)
    assert intel.put_call_skew == round((34.0 - 30.0) / 100, 4)
    assert intel.expected_move_pct == round((3.0 + 2.5) / 100.0 * 100.0, 3)
    assert intel.nearest_expiry == "2026-06-19"


def test_normalize_chain_empty_is_safe() -> None:
    intel = OptionsProvider.normalize_chain(None)
    assert intel.atm_iv is None
    assert intel.expected_move_pct is None


def test_build_symbol_options_via_service() -> None:
    out = cockpit_service.build_symbol_options({"underlyingPrice": 50.0, "callExpDateMap": {}, "putExpDateMap": {}})
    assert out["atm_iv"] is None
