#!/usr/bin/env python3
"""Validate multi-sleeve constitution modules + config defaults."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    from config import get_r7_min_n, get_r7_primary_horizon, get_s1_live_enabled
    from core.multi_sleeve_constitution import CAP_S0, CAP_S1, RESEARCH_WAVE
    from core.portfolio_allocator import (
        crash_clear,
        detect_crash_mode,
        drawdown_tier,
        evaluate_allocator,
    )
    from core.r7_promotion import r7_may_promote
    from core.sleeve_attribution import build_attribution_snapshot
    from core.sleeve_research_chips import apply_r3_capacity_to_rows, research_wave_order
    from core.sleeve_weights import apply_hysteresis, build_multi_sleeve_targets

    errors: list[str] = []

    if CAP_S0 != 0.80 or CAP_S1 != 0.15:
        errors.append("cap constants drifted")
    if research_wave_order() != RESEARCH_WAVE:
        errors.append("research wave order drifted")
    if int(get_r7_min_n(SKILL_DIR)) < 1:
        errors.append("r7 min n invalid")
    if not str(get_r7_primary_horizon(SKILL_DIR)):
        errors.append("r7 horizon empty")
    if get_s1_live_enabled(SKILL_DIR) not in (True, False):
        errors.append("s1 live flag invalid")

    if not detect_crash_mode([100, 99, 98, 97, 96, 95, 94, 93, 92, 90]):
        errors.append("crash detect false negative")
    if detect_crash_mode([100, 100.1, 100.2, 100.3]):
        errors.append("crash detect false positive")
    if drawdown_tier(0.19) != "hard":
        errors.append("dd tier hard expected")
    if drawdown_tier(0.11) != "ok":
        errors.append("dd tier ok expected")

    d = evaluate_allocator(mode="shadow", crash_active=True)
    if d.allow_new_risk or d.s1_new_risk_mult != 0.0:
        errors.append("crash should zero new risk")

    d_bear = evaluate_allocator(mode="shadow", regime_bear=True)
    if d_bear.s0_new_risk_mult != 0.0 or d_bear.s1_new_risk_mult != 0.5:
        errors.append("asymmetric bear regime failed")

    rows = [
        {"ticker": "AAA", "signal_score": 90, "realized_vol": 0.02},
        {"ticker": "BBB", "signal_score": 80, "realized_vol": 0.04},
        {"ticker": "CCC", "signal_score": 70, "realized_vol": 0.02},
        {"ticker": "DDD", "signal_score": 60, "realized_vol": 0.02},
        {"ticker": "EEE", "signal_score": 50, "realized_vol": 0.02},
        {"ticker": "FFF", "signal_score": 40, "realized_vol": 0.02},
    ]
    decision = evaluate_allocator(mode="shadow")
    targets = build_multi_sleeve_targets(s0_rows=rows, s1_rows=None, decision=decision)
    if len(targets["sleeves"]["S0"]) != 5:
        errors.append("S0 top-5 expected")

    cur = dict(targets["net_book"]["weights"])
    tgt = dict(cur)
    if tgt:
        k = next(iter(tgt))
        tgt[k] = tgt[k] + 0.005
        held = apply_hysteresis(tgt, cur, band=0.01)
        if abs(held.get(k, 0) - cur.get(k, 0)) > 1e-9:
            errors.append("hysteresis should hold on 0.5% drift")

    attr = build_attribution_snapshot(
        sleeve_weights={"S0": {"AAA": 0.1}},
        returns={"AAA": 0.02},
        total_cost=1.0,
        prev_sleeve_weights={"S0": {"AAA": 0.05}},
    )
    if attr["method"] != "D":
        errors.append("attribution method")

    r3 = apply_r3_capacity_to_rows(
        [{"ticker": "AAA", "signal_score": 100, "adv_usd": 500_000}],
    )
    if r3[0]["signal_score_r3"] >= 100:
        errors.append("r3 should penalize thin ADV")

    records = [
        {
            "sleeve_id": "S0",
            "outcomes": {"5": {"thesis_hit": False, "return_pct": -1.0}},
        }
        for _ in range(40)
    ]
    gate = r7_may_promote(records, sleeve_id="S0", offline_floors_ok=True)
    if gate["ok"]:
        errors.append("r7 should veto non-positive expectancy")

    if not isinstance(crash_clear([90] * 15 + [91, 92, 93, 94, 95] + [96, 97, 98]), bool):
        errors.append("crash_clear type")

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        return 1
    print("OK: multi-sleeve constitution validators passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
