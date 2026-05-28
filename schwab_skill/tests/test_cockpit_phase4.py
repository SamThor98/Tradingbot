"""Phase 4: decision packets, weekly diagnostics, advisory tuning feedback."""

from __future__ import annotations

from core import decision_packet, trade_review, weight_feedback
from core.contracts import DecisionPacket


# --------------------------------------------------------------------------- #
# DecisionPacket build + store
# --------------------------------------------------------------------------- #
def test_build_packet_from_context() -> None:
    pkt = decision_packet.build_packet(
        ticker="aapl",
        kind="approved",
        signal={
            "rank_score": 80.0,
            "edge_score": 70.0,
            "p_up_calibrated": 0.6,
            "strategy_attribution": {"top_live": "trend_breakout"},
            "_filter_status": "kept",
        },
        market={"regime_state": "bullish", "regime_score": 75.0, "volatility_state": "low"},
        execution={
            "state": "filled",
            "intent": {"policy_id": "exec_policy_v1"},
            "quality": {"realized_slippage_bps": 8.0},
        },
    )
    assert isinstance(pkt, DecisionPacket)
    assert pkt.ticker == "AAPL"
    assert pkt.setup_type == "trend_breakout"
    assert pkt.regime_state == "bullish"
    assert pkt.volatility_state == "low"
    assert pkt.policy_id == "exec_policy_v1"
    assert pkt.outcome.label == "pending"


def test_record_load_and_backfill(tmp_path) -> None:
    pkt = decision_packet.build_packet(ticker="MSFT", signal={"rank_score": 70.0})
    assert decision_packet.record_packet(tmp_path, pkt) is True
    loaded = decision_packet.load_packets(tmp_path)
    assert len(loaded) == 1
    assert loaded[0]["ticker"] == "MSFT"

    ok = decision_packet.backfill_outcome(
        tmp_path, pkt.packet_id, label="win", realized_return_pct=4.2, horizon_days=10
    )
    assert ok is True
    reloaded = decision_packet.load_packets(tmp_path)
    assert reloaded[0]["outcome"]["label"] == "win"
    assert reloaded[0]["outcome"]["realized_return_pct"] == 4.2


def test_load_packets_limit(tmp_path) -> None:
    for i in range(5):
        decision_packet.record_packet(tmp_path, decision_packet.build_packet(ticker=f"T{i}", signal={}))
    assert len(decision_packet.load_packets(tmp_path, limit=2)) == 2


# --------------------------------------------------------------------------- #
# Weekly diagnostics
# --------------------------------------------------------------------------- #
def _packet(regime, setup, vol, edge, realized=None, slip=None, label="pending"):
    return {
        "regime_state": regime,
        "setup_type": setup,
        "volatility_state": vol,
        "edge_score": edge,
        "expected_slippage_bps": slip,
        "outcome": {
            "label": label,
            "realized_return_pct": realized,
            "realized_slippage_bps": slip,
        },
    }


def test_false_positives_by_regime() -> None:
    packets = [
        _packet("bullish", "a", "low", 70, label="win"),
        _packet("bullish", "a", "low", 70, label="loss"),
        _packet("bearish", "a", "low", 70, label="loss"),
        _packet("neutral", "a", "low", 70, label="pending"),  # excluded
    ]
    fp = trade_review.false_positives_by_regime(packets)
    assert fp["bullish"]["resolved"] == 2
    assert fp["bullish"]["fp_rate"] == 0.5
    assert fp["bearish"]["fp_rate"] == 1.0
    assert "neutral" not in fp


def test_edge_decay_by_setup() -> None:
    packets = [
        _packet("bullish", "breakout", "low", 80.0, realized=2.0, label="win"),
        _packet("bullish", "breakout", "low", 80.0, realized=2.0, label="win"),
    ]
    decay = trade_review.edge_decay_by_setup(packets)
    assert decay["breakout"]["avg_edge_score"] == 80.0
    assert decay["breakout"]["avg_realized_return_pct"] == 2.0
    assert decay["breakout"]["edge_decay"] == round(0.80 - 0.02, 4)


def test_execution_drag_by_condition() -> None:
    packets = [
        _packet("bullish", "a", "elevated", 70, slip=40.0, label="loss"),
        _packet("bullish", "a", "elevated", 70, slip=20.0, label="win"),
    ]
    drag = trade_review.execution_drag_by_condition(packets)
    assert drag["elevated"]["avg_slippage_bps"] == 30.0
    assert drag["elevated"]["samples"] == 2


def test_execution_drag_preserves_realized_zero_slippage() -> None:
    # A perfect fill (realized 0.0) must NOT fall through to the expected estimate.
    pkt = {
        "volatility_state": "low",
        "expected_slippage_bps": 25.0,
        "outcome": {"label": "win", "realized_slippage_bps": 0.0},
    }
    drag = trade_review.execution_drag_by_condition([pkt])
    assert drag["low"]["avg_slippage_bps"] == 0.0  # not 25.0
    assert drag["low"]["samples"] == 1


def test_execution_drag_uses_expected_only_when_realized_missing() -> None:
    pkt = {
        "volatility_state": "low",
        "expected_slippage_bps": 25.0,
        "outcome": {"label": "win"},  # no realized
    }
    drag = trade_review.execution_drag_by_condition([pkt])
    assert drag["low"]["avg_slippage_bps"] == 25.0


def test_weekly_report_coverage() -> None:
    packets = [_packet("bullish", "a", "low", 70, label="win"), _packet("bullish", "a", "low", 70, label="pending")]
    rep = trade_review.weekly_report(packets)
    assert rep["total_packets"] == 2
    assert rep["resolved_packets"] == 1
    assert rep["coverage_pct"] == 50.0


# --------------------------------------------------------------------------- #
# Feedback proposals
# --------------------------------------------------------------------------- #
def test_propose_tightens_on_high_fp_regime() -> None:
    # 25 resolved bearish decisions, all losses -> fp_rate 1.0 >= threshold
    packets = [_packet("bearish", "a", "low", 70, label="loss") for _ in range(25)]
    report = trade_review.weekly_report(packets)
    out = weight_feedback.propose(report)
    assert out["count"] >= 1
    assert any(p["target"] == "QUALITY_MIN_SIGNAL_SCORE" and p["direction"] == "increase" for p in out["proposals"])


def test_propose_empty_when_insufficient_samples() -> None:
    packets = [_packet("bearish", "a", "low", 70, label="loss") for _ in range(3)]
    report = trade_review.weekly_report(packets)
    out = weight_feedback.propose(report)
    assert out["count"] == 0


def test_propose_reduces_weight_on_edge_decay() -> None:
    packets = [_packet("bullish", "laggard", "low", 90.0, realized=1.0, label="win") for _ in range(25)]
    report = trade_review.weekly_report(packets)
    out = weight_feedback.propose(report)
    assert any(p["kind"] == "scanner_weight" and p["direction"] == "decrease" for p in out["proposals"])
