"""Tests for multi-sleeve constitution modules (Phases 0–8 scaffolding)."""

from __future__ import annotations

from pathlib import Path

from core.portfolio_allocator import detect_crash_mode, evaluate_allocator
from core.r7_promotion import r7_may_promote
from core.sleeve_attribution import (
    build_attribution_snapshot,
    filled_share_attribution,
    split_costs_by_abs_delta_w,
)
from core.sleeve_research_chips import apply_r3_capacity_to_rows, r4_pead_sleeve_score, r6_vol_damper
from core.sleeve_weights import apply_hysteresis, build_multi_sleeve_targets, net_exposure_book
from hypothesis_ledger import append_hypothesis, record_from_signal, summarize_scored_hypotheses


def test_crash_and_asymmetric_regime() -> None:
    assert detect_crash_mode([100, 99, 98, 97, 96, 95, 94, 93, 92, 90]) is True
    d = evaluate_allocator(mode="shadow", regime_bear=True)
    assert d.s0_new_risk_mult == 0.0
    assert d.s1_new_risk_mult == 0.5
    d2 = evaluate_allocator(mode="shadow", crash_active=True)
    assert d2.allow_new_risk is False
    assert d2.s1_new_risk_mult == 0.0


def test_s0_top5_vol_weights_and_hysteresis() -> None:
    rows = [
        {"ticker": f"T{i}", "signal_score": 100 - i, "realized_vol": 0.02 if i % 2 == 0 else 0.04}
        for i in range(8)
    ]
    decision = evaluate_allocator(mode="shadow")
    pack = build_multi_sleeve_targets(s0_rows=rows, s1_rows=None, decision=decision)
    assert len(pack["sleeves"]["S0"]) == 5
    assert pack["net_book"]["gross"] <= 0.80 + 1e-6
    cur = dict(pack["net_book"]["weights"])
    tgt = dict(cur)
    k = next(iter(tgt))
    tgt[k] = tgt[k] + 0.004
    held = apply_hysteresis(tgt, cur, band=0.01)
    assert abs(held[k] - cur[k]) < 1e-9


def test_net_book_overlap() -> None:
    book = net_exposure_book(
        {
            "S0": {"AAA": 0.10, "BBB": 0.10},
            "S1": {"AAA": 0.05},
        }
    )
    # Name cap 8% clips AAA (0.15) and BBB (0.10)
    assert abs(book["weights"]["AAA"] - 0.08) < 1e-9
    assert abs(book["weights"]["BBB"] - 0.08) < 1e-9


def test_attribution_d() -> None:
    snap = build_attribution_snapshot(
        sleeve_weights={"S0": {"AAA": 0.1}, "S1": {"AAA": 0.05}},
        returns={"AAA": 0.10},
        fill_notional_by_ticker={"AAA": 1500.0},
        prev_sleeve_weights={"S0": {"AAA": 0.0}, "S1": {"AAA": 0.0}},
        total_cost=15.0,
    )
    assert abs(snap["counterfactual"]["S0"]["pnl"] - 0.01) < 1e-9
    filled = filled_share_attribution(
        {"S0": {"AAA": 0.1}, "S1": {"AAA": 0.05}},
        {"AAA": 1500.0},
    )
    assert abs(filled["S0"]["AAA"] - 1000.0) < 1e-6
    assert abs(filled["S1"]["AAA"] - 500.0) < 1e-6
    costs = split_costs_by_abs_delta_w({"S0": 0.1, "S1": 0.05}, 15.0)
    assert abs(costs["S0"] - 10.0) < 1e-6


def test_r7_veto_and_pass() -> None:
    bad = [{"sleeve_id": "S0", "outcomes": {"5": {"thesis_hit": False, "return_pct": -0.5}}} for _ in range(40)]
    assert r7_may_promote(bad, sleeve_id="S0", offline_floors_ok=True)["ok"] is False
    good = [{"sleeve_id": "S0", "outcomes": {"5": {"thesis_hit": True, "return_pct": 1.0}}} for _ in range(40)]
    assert r7_may_promote(good, sleeve_id="S0", offline_floors_ok=True)["ok"] is True
    assert r7_may_promote(good, sleeve_id="S0", offline_floors_ok=False)["ok"] is False


def test_ledger_sleeve_id(tmp_path: Path) -> None:
    rec = record_from_signal({"ticker": "XYZ", "price": 10.0, "signal_score": 1}, skill_dir=tmp_path)
    assert rec["sleeve_id"] == "S0"
    append_hypothesis(
        {
            **rec,
            "outcomes": {"5": {"thesis_hit": True, "return_pct": 2.0}},
        },
        skill_dir=tmp_path,
    )
    s = summarize_scored_hypotheses(tmp_path)
    assert "S0" in s["by_sleeve"]


def test_research_chips() -> None:
    rows = apply_r3_capacity_to_rows([{"ticker": "AAA", "signal_score": 100, "adv_usd": 100_000}])
    assert rows[0]["signal_score_r3"] < 100
    assert r4_pead_sleeve_score({"edge_score": 10, "pead_surprise_pct": 5, "adv_usd": 20_000_000}) > 0
    assert r6_vol_damper(0.15, book_vol=0.03, baseline_vol=0.01) < 0.15
