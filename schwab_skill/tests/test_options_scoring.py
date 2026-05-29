"""Options-chain -> scan scoring overlay (shadow by default, live re-ranks)."""

from __future__ import annotations

from core import options_scoring


# --------------------------------------------------------------------------- #
# Pure delta math
# --------------------------------------------------------------------------- #
def test_delta_neutral_when_empty() -> None:
    out = options_scoring.compute_options_score_delta({}, {})
    assert out["delta"] == 0.0
    assert out["reasons"] == []


def test_delta_penalizes_rich_iv_and_put_skew() -> None:
    intel = {"atm_iv": 0.95, "put_call_skew": 0.05}
    out = options_scoring.compute_options_score_delta(intel, {})
    assert out["delta"] < 0
    assert "rich_atm_iv:95.0%" in out["reasons"]
    assert "defensive_put_skew" in out["reasons"]


def test_delta_rewards_calm_iv_and_call_skew() -> None:
    intel = {"atm_iv": 0.20, "put_call_skew": -0.05}
    out = options_scoring.compute_options_score_delta(intel, {})
    assert out["delta"] > 0
    assert "calm_atm_iv" in out["reasons"]
    assert "call_skew_bullish" in out["reasons"]


def test_delta_is_bounded() -> None:
    intel = {"atm_iv": 5.0, "put_call_skew": 1.0, "expected_move_pct": 999.0}
    out = options_scoring.compute_options_score_delta(intel, {"advisory": {"expected_move_10d": 0.01}})
    assert out["delta"] >= -5.0  # clamped


def test_delta_flags_options_imply_larger_move() -> None:
    intel = {"expected_move_pct": 30.0}
    out = options_scoring.compute_options_score_delta(intel, {"advisory": {"expected_move_10d": 0.05}})
    assert "options_imply_larger_move" in out["reasons"]


# --------------------------------------------------------------------------- #
# Overlay application
# --------------------------------------------------------------------------- #
def _chain(underlying=100.0, call_iv=30.0, put_iv=34.0):
    return {
        "underlyingPrice": underlying,
        "callExpDateMap": {"2026-06-19:30": {"100.0": [{"volatility": call_iv, "mark": 3.0}]}},
        "putExpDateMap": {"2026-06-19:30": {"100.0": [{"volatility": put_iv, "mark": 2.5}]}},
    }


def test_overlay_off_is_noop(monkeypatch) -> None:
    monkeypatch.setenv("OPTIONS_SCORING_MODE", "off")
    signals = [{"ticker": "AAPL", "rank_score": 80.0}]
    out = options_scoring.apply_options_scoring(signals, {}, chain_fetcher=lambda t: _chain())
    assert "options_intel" not in out[0]


def test_overlay_shadow_attaches_but_does_not_rerank(monkeypatch) -> None:
    monkeypatch.setenv("OPTIONS_SCORING_MODE", "shadow")
    monkeypatch.setenv("OPTIONS_INTEL_MODE", "live")
    diag = {}
    signals = [{"ticker": "AAPL", "rank_score": 80.0}]
    out = options_scoring.apply_options_scoring(signals, diag, chain_fetcher=lambda t: _chain())
    assert out[0]["rank_score"] == 80.0  # unchanged in shadow
    assert "options_score_delta" in out[0]
    assert "options_intel" in out[0]
    assert diag["options_scoring"]["mode"] == "shadow"
    assert diag["options_scoring"]["evaluated"] == 1
    assert diag["options_scoring"]["applied"] == 0


def test_overlay_live_applies_delta_and_reranks(monkeypatch) -> None:
    monkeypatch.setenv("OPTIONS_SCORING_MODE", "live")
    monkeypatch.setenv("OPTIONS_INTEL_MODE", "live")
    diag = {}
    # AAPL rich IV+put skew (penalty) starts above MSFT; after penalty it may drop.
    signals = [
        {"ticker": "AAPL", "rank_score": 80.0},
        {"ticker": "MSFT", "rank_score": 78.0},
    ]
    fetch = {"AAPL": _chain(call_iv=95.0, put_iv=99.0), "MSFT": _chain(call_iv=20.0, put_iv=18.0)}.get
    out = options_scoring.apply_options_scoring(signals, diag, chain_fetcher=lambda t: fetch(t))
    aapl = next(s for s in out if s["ticker"] == "AAPL")
    msft = next(s for s in out if s["ticker"] == "MSFT")
    assert aapl["rank_score"] < 80.0  # penalized
    assert msft["rank_score"] > 78.0  # rewarded
    assert aapl.get("options_score_applied") is True
    assert diag["options_scoring"]["applied"] >= 1
    # re-sorted: MSFT now ranks ahead of AAPL
    assert out[0]["ticker"] == "MSFT"


def test_overlay_disabled_when_intel_off(monkeypatch) -> None:
    monkeypatch.setenv("OPTIONS_SCORING_MODE", "shadow")
    monkeypatch.setenv("OPTIONS_INTEL_MODE", "off")
    diag = {}
    signals = [{"ticker": "AAPL", "rank_score": 80.0}]
    out = options_scoring.apply_options_scoring(signals, diag, chain_fetcher=lambda t: _chain())
    assert "options_intel" not in out[0]
    assert diag["options_scoring"]["evaluated"] == 0
