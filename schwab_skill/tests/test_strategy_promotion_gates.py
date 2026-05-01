"""Boundary tests for the strategy promotion gates.

These tests exercise the pure helpers in ``scripts/_strategy_gates.py``
without invoking the full backtest. They cover:

- Anti-dead-run gate: zero-trade era hard rejection.
- Per-era throughput floor (balanced-throughput policy).
- Aggregate ``trades_min`` floor.
- PF / expectancy / OOS PF / drawdown gate boundaries (passing right at
  the threshold and failing one tick below).
- Bucket low-confidence annotation for diagnostics sample-size discipline.

Tests intentionally use synthetic profile dictionaries (the same shape
``validate_pf_robustness._run_profile`` emits) so they're fast and don't
depend on market data fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Make ``scripts/`` importable for the pure-helper module (mirrors how
# scripts import each other inside this repo).
SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _strategy_gates import (  # noqa: E402
    DEFAULT_LOW_CONFIDENCE_N,
    PromotionGates,
    annotate_bucket_confidence,
    detect_sparse_eras,
    detect_zero_trade_eras,
    evaluate_promotion_gates,
)


def _aggregates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pf_vals = [float(r["profit_factor_net"]) for r in rows]
    ex_vals = [float(r["expectancy_net_pct"]) for r in rows]
    dd_vals = [float(r["max_drawdown_net_pct"]) for r in rows]
    trade_vals = [int(r["total_trades"]) for r in rows]
    oos = rows[-1]
    return {
        "pf_net_mean": round(sum(pf_vals) / len(pf_vals), 4),
        "expectancy_net_mean": round(sum(ex_vals) / len(ex_vals), 4),
        "max_drawdown_net_worst": round(min(dd_vals), 4),
        "trades_min": int(min(trade_vals)),
        "oos_pf_net": round(float(oos["profit_factor_net"]), 4),
        "oos_expectancy_net_pct": round(float(oos["expectancy_net_pct"]), 4),
    }


def _profile(
    *,
    name: str = "challenger",
    pfs: tuple[float, float, float] = (1.20, 1.19, 1.18),
    trades: tuple[int, int, int] = (90, 88, 75),
    drawdowns: tuple[float, float, float] = (-15.0, -16.0, -17.0),
    expectancies: tuple[float, float, float] = (0.22, 0.20, 0.17),
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for i, era in enumerate(("long_20", "mid_20", "recent_20")):
        rows.append(
            {
                "name": era,
                "start_date": f"20{18 + 2 * i}-01-01",
                "ticker_count": 20,
                "total_trades": int(trades[i]),
                "win_rate_net": 50.0,
                "profit_factor_net": float(pfs[i]),
                "expectancy_net_pct": float(expectancies[i]),
                "max_drawdown_net_pct": float(drawdowns[i]),
            }
        )
    return {"name": name, "windows": rows, "aggregates": _aggregates(rows)}


def _baseline_champion() -> dict[str, Any]:
    return _profile(
        name="champion",
        pfs=(1.16, 1.15, 1.14),
        trades=(90, 88, 75),
        drawdowns=(-14.0, -15.0, -16.0),
        expectancies=(0.20, 0.18, 0.16),
    )


def test_zero_trade_era_is_hard_rejected_even_with_strong_aggregates() -> None:
    champion = _baseline_champion()
    challenger = _profile(
        pfs=(1.40, 1.38, 1.36),
        trades=(120, 0, 110),  # mid era is dead
        drawdowns=(-10.0, -11.0, -12.0),
        expectancies=(0.30, 0.28, 0.26),
    )
    result = evaluate_promotion_gates(champion=champion, challenger=challenger, gates=PromotionGates())
    assert result.passed is False
    assert any(r.startswith("zero_trade_era_detected") for r in result.reasons)
    # No-trade era list should mention 'mid_20'.
    assert "mid_20" in [row["name"] for row in result.per_era if row["is_zero_trade"]]


def test_detect_zero_trade_eras_returns_names() -> None:
    rows = [
        {"name": "a", "total_trades": 50},
        {"name": "b", "total_trades": 0},
        {"name": "c", "total_trades": 20},
    ]
    assert detect_zero_trade_eras(rows) == ["b"]


def test_per_era_min_trades_rejects_sparse_window() -> None:
    champion = _baseline_champion()
    challenger = _profile(
        pfs=(1.40, 1.38, 1.36),
        trades=(120, 18, 110),  # mid era below per-era floor of 20
        drawdowns=(-10.0, -11.0, -12.0),
        expectancies=(0.30, 0.28, 0.26),
    )
    result = evaluate_promotion_gates(
        champion=champion,
        challenger=challenger,
        gates=PromotionGates(min_trades_per_era=20, min_trades_threshold=10),
    )
    assert result.passed is False
    assert any(r.startswith("era_trade_count_too_low:mid_20") for r in result.reasons)


def test_sparse_era_helper_does_not_flag_zero_or_at_threshold() -> None:
    rows = [
        {"name": "ok", "total_trades": 25},
        {"name": "boundary", "total_trades": 20},  # equal to floor → not sparse
        {"name": "low", "total_trades": 19},
        {"name": "dead", "total_trades": 0},  # zero handled by detect_zero_trade_eras
    ]
    sparse = detect_sparse_eras(rows, min_trades_per_era=20)
    names = [row["name"] for row in sparse]
    assert names == ["low"]


def test_aggregate_min_trades_threshold_enforced() -> None:
    champion = _baseline_champion()
    challenger = _profile(
        pfs=(1.40, 1.38, 1.36),
        trades=(120, 110, 30),  # min 30, below default 35
        drawdowns=(-10.0, -11.0, -12.0),
        expectancies=(0.30, 0.28, 0.26),
    )
    result = evaluate_promotion_gates(
        champion=champion,
        challenger=challenger,
        gates=PromotionGates(min_trades_per_era=20),
    )
    assert result.passed is False
    assert any(r.startswith("trades_min_too_low:30<35") for r in result.reasons)


def test_promotion_passes_at_locked_default_thresholds() -> None:
    """A canonical 'just barely good enough' challenger should clear all
    locked-default gates. Used as a regression baseline."""
    champion = _baseline_champion()
    # Champion PF mean = 1.15, expectancy mean = 0.18, OOS PF = 1.14,
    # worst drawdown = -16.0
    challenger = _profile(
        pfs=(1.18, 1.17, 1.16),  # PF mean 1.17 -> +0.02 delta (= min_pf_delta)
        trades=(90, 88, 75),
        drawdowns=(-15.0, -16.0, -17.0),  # worst -17 vs champion -16 → +1% degrade
        expectancies=(0.22, 0.20, 0.18),  # expectancy mean 0.20 → +0.02
    )
    result = evaluate_promotion_gates(champion=champion, challenger=challenger, gates=PromotionGates())
    assert result.passed is True
    assert "challenger_meets_walkforward_promotion_gates" in result.reasons


def test_promotion_rejects_when_pf_delta_below_threshold() -> None:
    champion = _baseline_champion()
    # Identical PFs (delta = 0) should fail the +0.02 floor.
    challenger = _profile(
        pfs=(1.16, 1.15, 1.14),
        trades=(90, 88, 75),
        drawdowns=(-14.0, -15.0, -16.0),
        expectancies=(0.20, 0.18, 0.16),
    )
    result = evaluate_promotion_gates(champion=champion, challenger=challenger, gates=PromotionGates())
    assert result.passed is False
    assert any(r.startswith("pf_delta_too_small") for r in result.reasons)


def test_promotion_rejects_when_oos_pf_below_floor() -> None:
    champion = _baseline_champion()
    challenger = _profile(
        pfs=(1.30, 1.20, 1.10),  # PF mean ok, but OOS = 1.10 < 1.15
        trades=(90, 88, 75),
        drawdowns=(-14.0, -15.0, -16.0),
        expectancies=(0.20, 0.18, 0.16),
    )
    result = evaluate_promotion_gates(champion=champion, challenger=challenger, gates=PromotionGates())
    assert result.passed is False
    assert any(r.startswith("oos_pf_below_floor") for r in result.reasons)


def test_promotion_rejects_when_drawdown_degrades_beyond_cap() -> None:
    champion = _baseline_champion()  # worst -16.0
    challenger = _profile(
        pfs=(1.20, 1.19, 1.18),
        trades=(90, 88, 75),
        drawdowns=(-14.0, -15.0, -19.0),  # worst -19 → +3% degrade > 2 cap
        expectancies=(0.22, 0.20, 0.18),
    )
    result = evaluate_promotion_gates(champion=champion, challenger=challenger, gates=PromotionGates())
    assert result.passed is False
    assert any(r.startswith("drawdown_degraded_too_much") for r in result.reasons)


def test_promotion_passes_at_drawdown_cap_boundary() -> None:
    """16% drawdown is the user-stated tolerance. Champion at -14, challenger
    at exactly -16 should pass with default +2% cap.
    """
    champion = _profile(
        name="champion",
        pfs=(1.16, 1.15, 1.14),
        trades=(90, 88, 75),
        drawdowns=(-12.0, -13.0, -14.0),  # worst -14
        expectancies=(0.20, 0.18, 0.16),
    )
    challenger = _profile(
        pfs=(1.18, 1.17, 1.16),
        trades=(90, 88, 75),
        drawdowns=(-14.0, -15.0, -16.0),  # worst -16 → +2% exactly
        expectancies=(0.22, 0.20, 0.18),
    )
    result = evaluate_promotion_gates(champion=champion, challenger=challenger, gates=PromotionGates())
    assert result.passed is True


def test_failures_accumulate_rather_than_short_circuit() -> None:
    """Operators want to see every failing gate, not just the first."""
    champion = _baseline_champion()
    challenger = _profile(
        pfs=(1.10, 1.09, 1.08),  # below champion → pf_delta + oos_pf both fail
        trades=(15, 18, 12),  # zero-trade-free but below per-era and aggregate floors
        drawdowns=(-30.0, -31.0, -32.0),  # huge drawdown blowout
        expectancies=(0.05, 0.04, 0.03),  # expectancy down
    )
    result = evaluate_promotion_gates(champion=champion, challenger=challenger, gates=PromotionGates())
    assert result.passed is False
    failure_kinds = {r.split(":", 1)[0] for r in result.reasons}
    # Expect multiple distinct gates triggered, not just one.
    assert "pf_delta_too_small" in failure_kinds
    assert "oos_pf_below_floor" in failure_kinds
    assert "drawdown_degraded_too_much" in failure_kinds
    assert "era_trade_count_too_low" in failure_kinds
    assert "trades_min_too_low" in failure_kinds


def test_annotate_bucket_confidence_marks_low_n() -> None:
    rows = [
        {"bucket": "<50", "count": 5, "win_rate_pct": 60.0},
        {"bucket": "50-59.99", "count": DEFAULT_LOW_CONFIDENCE_N + 5, "win_rate_pct": 55.0},
        {"bucket": "60-69.99", "count": DEFAULT_LOW_CONFIDENCE_N - 1, "win_rate_pct": 50.0},
    ]
    annotate_bucket_confidence(rows)
    assert rows[0]["low_confidence"] is True
    assert rows[1]["low_confidence"] is False
    assert rows[2]["low_confidence"] is True
    assert rows[0]["confidence_metadata"]["min_n"] == DEFAULT_LOW_CONFIDENCE_N
    assert rows[1]["confidence_metadata"]["n"] == DEFAULT_LOW_CONFIDENCE_N + 5
