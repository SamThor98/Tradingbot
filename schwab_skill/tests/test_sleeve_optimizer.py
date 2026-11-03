"""Sleeve optimizer: 3-ticker fixture, no Stage A admission change."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.portfolio_allocator import evaluate_allocator  # noqa: E402
from core.sleeve_optimizer import optimize_net_weights  # noqa: E402
from core.sleeve_weights import build_multi_sleeve_targets, select_top_n  # noqa: E402


def test_three_ticker_corr_and_cash_residual() -> None:
    desired = {
        "S0": {"AAA": 0.40, "BBB": 0.40, "CCC": 0.10},
    }
    corr = {("AAA", "BBB"): 0.95, ("BBB", "AAA"): 0.95}
    out = optimize_net_weights(
        desired,
        corr=corr,
        tickers=["AAA", "BBB", "CCC"],
        name_cap=0.50,
        cluster_cap=0.25,
        corr_cap=0.70,
        mode="live",
        current_weights={"AAA": 0.10, "BBB": 0.10, "CCC": 0.05},
    )
    assert out["places_orders"] is False
    assert out["invents_entries"] is False
    w = out["weights"]
    assert w.get("AAA", 0.0) + w.get("BBB", 0.0) <= 0.25 + 1e-6
    assert out["s3_cash"] >= 0.0
    sleeves = {p["sleeve"] for p in out["policies"]}
    assert sleeves == {"S0", "S1", "S3", "allocator-only"}


def test_shadow_logs_desired_does_not_replace_weights() -> None:
    desired = {"S0": {"AAA": 0.20, "BBB": 0.20, "CCC": 0.10}}
    out = optimize_net_weights(desired, mode="shadow", name_cap=0.50, cluster_cap=0.80)
    assert out["weights"] == out["desired"]
    assert out["trade_targets"] == {}


def test_max_dd_flattens_to_s3() -> None:
    desired = {"S0": {"AAA": 0.20, "BBB": 0.20, "CCC": 0.10}}
    out = optimize_net_weights(
        desired,
        drawdown_from_peak=0.22,
        max_dd=0.18,
        mode="live",
        name_cap=0.50,
        cluster_cap=0.80,
    )
    assert out["gross"] == pytest.approx(0.0)
    assert out["cash_weight"] == pytest.approx(1.0)


def test_build_targets_off_does_not_change_stage_a_select() -> None:
    rows = [
        {"ticker": "AAA", "signal_score": 90, "realized_vol": 0.02},
        {"ticker": "BBB", "signal_score": 80, "realized_vol": 0.02},
        {"ticker": "CCC", "signal_score": 70, "realized_vol": 0.02},
        {"ticker": "DDD", "signal_score": 60, "realized_vol": 0.02},
        {"ticker": "EEE", "signal_score": 50, "realized_vol": 0.02},
        {"ticker": "FFF", "signal_score": 40, "realized_vol": 0.02},
    ]
    top = select_top_n(rows, n=5)
    assert [r["ticker"] for r in top] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    decision = evaluate_allocator(mode="off")
    pack = build_multi_sleeve_targets(s0_rows=rows, s1_rows=None, decision=decision, optimizer_mode="off")
    assert "optimizer" not in pack
    assert len(pack["sleeves"]["S0"]) == 5
