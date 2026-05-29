"""Phase 4: decision packets, weekly diagnostics, advisory tuning feedback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from core import decision_packet, outcome_backfill, trade_review, weight_feedback
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


def _history(start: str, closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(closes), freq="D")
    return pd.DataFrame({"close": closes}, index=idx)


def test_outcome_backfill_resolves_matured_win() -> None:
    created = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    packet: dict[str, Any] = {
        "ticker": "AAPL",
        "created_at": created,
        "entry_price": 100.0,
        "outcome": {"label": "pending"},
    }
    # 12 bars from the entry date: entry 100 -> exit (idx+10) 110 => +10%
    closes = [100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110.0, 111]
    hist = _history(packet["created_at"][:10], closes)
    out = outcome_backfill.compute_outcome(packet, history_provider=lambda t: hist, horizon_days=10)
    assert out is not None
    assert out["label"] == "win"
    assert out["realized_return_pct"] == 10.0
    assert out["horizon_days"] == 10


def test_outcome_backfill_skips_unmatured() -> None:
    created = datetime.now(timezone.utc).isoformat()
    packet = {"ticker": "AAPL", "created_at": created, "entry_price": 100.0, "outcome": {"label": "pending"}}
    hist = _history(created[:10], [100.0, 101.0, 102.0])  # < horizon+1 bars
    out = outcome_backfill.compute_outcome(packet, history_provider=lambda t: hist, horizon_days=10)
    assert out is None


def test_outcome_backfill_already_resolved_is_skipped() -> None:
    packet = {"ticker": "AAPL", "created_at": "2026-01-01T00:00:00+00:00", "outcome": {"label": "win"}}
    out = outcome_backfill.compute_outcome(packet, history_provider=lambda t: _history("2026-01-01", [1.0] * 20))
    assert out is None


def test_backfill_packets_then_review_has_coverage() -> None:
    created = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    packets: list[dict[str, Any]] = [
        {
            "ticker": "AAPL",
            "created_at": created,
            "entry_price": 100.0,
            "regime_state": "bullish",
            "setup_type": "breakout",
            "volatility_state": "low",
            "edge_score": 80.0,
            "outcome": {"label": "pending"},
        },
    ]
    closes = [100.0, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90.0, 89]  # -10% => loss
    n = outcome_backfill.backfill_packets(
        packets, history_provider=lambda t: _history(created[:10], closes), horizon_days=10
    )
    assert n == 1
    assert packets[0]["outcome"]["label"] == "loss"
    rep = trade_review.weekly_report(packets)
    assert rep["resolved_packets"] == 1
    assert rep["false_positives_by_regime"]["bullish"]["fp_rate"] == 1.0


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
