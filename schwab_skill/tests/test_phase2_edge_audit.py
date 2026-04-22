"""Unit tests for Phase 2 Stage 1 edge audit and shared common helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.phase2_common import (  # noqa: E402
    Trade,
    equity_curve,
    expectancy,
    load_trades,
    max_drawdown_pct,
    per_era_stats,
    profit_factor,
    win_rate,
)
from scripts.phase2_edge_audit import (  # noqa: E402
    ITERATE_PF_MEAN,
    ITERATE_WORST,
    MIN_ERAS_FOR_VERDICT,
    OVERLAYS_MATERIAL_DELTA,
    PROCEED_PF_MEAN,
    PROCEED_WORST,
    RunSummary,
    _aligned_pf_means,
    _overlay_finding,
    _summarise,
    _verdict,
)


def _make_trade(era: str, ret: float, day: int) -> Trade:
    entry = pd.Timestamp("2020-01-01") + pd.Timedelta(days=day)
    exit_ = entry + pd.Timedelta(days=10)
    return Trade(
        era=era,
        entry_date=entry,
        exit_date=exit_,
        ret=ret,
        net_ret=ret,
        stop_pct=0.05,
    )


def test_profit_factor_all_winners_returns_inf() -> None:
    trades = [_make_trade("late_bull", 0.05, i) for i in range(3)]
    assert profit_factor(trades) == float("inf")


def test_profit_factor_balanced() -> None:
    trades = [
        _make_trade("late_bull", 0.10, 1),
        _make_trade("late_bull", -0.05, 2),
    ]
    assert profit_factor(trades) == pytest.approx(2.0)


def test_profit_factor_empty_returns_none() -> None:
    assert profit_factor([]) is None


def test_win_rate_and_expectancy() -> None:
    trades = [
        _make_trade("late_bull", 0.10, 1),
        _make_trade("late_bull", -0.05, 2),
        _make_trade("late_bull", 0.02, 3),
    ]
    assert win_rate(trades) == pytest.approx(2 / 3)
    assert expectancy(trades) == pytest.approx((0.10 - 0.05 + 0.02) / 3)


def test_equity_curve_compounds_then_drawdown() -> None:
    trades = [
        _make_trade("late_bull", 0.10, 1),
        _make_trade("late_bull", -0.20, 2),
    ]
    curve = equity_curve(trades, starting_equity=100_000.0, position_pct=1.0)
    # Trade 1: +10% on full equity -> 110_000
    assert curve[0]["equity"] == pytest.approx(110_000.0)
    # Trade 2: -20% on full equity (110_000) -> 88_000
    assert curve[1]["equity"] == pytest.approx(88_000.0)
    # Peak was 110_000 -> drawdown = (110-88)/110 = 20%
    assert curve[1]["drawdown_pct"] == pytest.approx(20.0, abs=1e-3)
    assert max_drawdown_pct(trades, position_pct=1.0) == pytest.approx(20.0, abs=1e-3)


def test_per_era_stats_filters_empty_eras() -> None:
    trades = [
        _make_trade("late_bull", 0.05, 1),
        _make_trade("crash_recovery", -0.02, 2),
    ]
    stats = per_era_stats(trades)
    assert {s.era for s in stats} == {"late_bull", "crash_recovery"}
    assert all(s.n == 1 for s in stats)


def _summary(run_id: str, era_pfs: dict[str, float], n_per_era: int = 100) -> RunSummary:
    """Build a RunSummary directly without loading from disk."""
    from scripts.phase2_common import EraStats

    eras = [
        EraStats(
            era=e,
            n=n_per_era,
            pf=pf,
            win_rate=0.5,
            expectancy=0.01,
            avg_hold_days=20.0,
            median_stop_pct=0.05,
            max_dd_pct=10.0,
            total_return_pct=5.0,
        )
        for e, pf in era_pfs.items()
    ]
    finite_pfs = [s.pf for s in eras if s.pf is not None and s.pf != float("inf")]
    return RunSummary(
        run_id=run_id,
        eras=eras,
        pf_mean=sum(finite_pfs) / len(finite_pfs) if finite_pfs else None,
        worst_era_pf=min(finite_pfs) if finite_pfs else None,
        n_total=sum(s.n for s in eras),
    )


def test_verdict_proceed_when_thresholds_met() -> None:
    s = _summary(
        "stage2_only",
        {"late_bull": 1.5, "volatility_chop": 1.3, "crash_recovery": 1.2, "bear_rates": 1.1, "recent_current": 1.4},
    )
    assert s.pf_mean is not None and s.pf_mean >= PROCEED_PF_MEAN
    assert s.worst_era_pf is not None and s.worst_era_pf >= PROCEED_WORST
    assert _verdict(s) == "proceed"


def test_verdict_iterate_when_only_iterate_thresholds_met() -> None:
    s = _summary(
        "stage2_only",
        {"late_bull": 1.4, "volatility_chop": 1.1, "crash_recovery": 1.0, "bear_rates": 0.9, "recent_current": 1.2},
    )
    assert s.pf_mean is not None and ITERATE_PF_MEAN <= s.pf_mean < PROCEED_PF_MEAN
    assert s.worst_era_pf is not None and s.worst_era_pf >= ITERATE_WORST
    assert _verdict(s) == "iterate_with_caution"


def test_verdict_halt_when_worst_era_collapses() -> None:
    s = _summary(
        "stage2_only",
        {"late_bull": 1.5, "volatility_chop": 1.4, "crash_recovery": 1.3, "bear_rates": 0.5, "recent_current": 1.4},
    )
    assert _verdict(s) == "halt_fix_signal_first"


def test_verdict_halt_when_pf_mean_too_low() -> None:
    s = _summary(
        "stage2_only",
        {"late_bull": 1.0, "volatility_chop": 1.0, "crash_recovery": 0.9, "bear_rates": 1.0, "recent_current": 1.0},
    )
    assert _verdict(s) == "halt_fix_signal_first"


def test_verdict_insufficient_when_no_data() -> None:
    s = RunSummary(run_id="x", eras=[], pf_mean=None, worst_era_pf=None, n_total=0)
    assert _verdict(s) == "halt_insufficient_data"


def test_overlay_finding_helping() -> None:
    bare = _summary("stage2_only", {"late_bull": 1.0})
    control = _summary("control_legacy", {"late_bull": 1.0 + 2 * OVERLAYS_MATERIAL_DELTA})
    assert _overlay_finding(bare, control) == "overlays_helping"


def test_overlay_finding_hurting() -> None:
    bare = _summary("stage2_only", {"late_bull": 1.5})
    control = _summary("control_legacy", {"late_bull": 1.5 - 2 * OVERLAYS_MATERIAL_DELTA})
    assert _overlay_finding(bare, control) == "overlays_hurting"


def test_overlay_finding_neutral() -> None:
    bare = _summary("stage2_only", {"late_bull": 1.3})
    control = _summary("control_legacy", {"late_bull": 1.3 + OVERLAYS_MATERIAL_DELTA / 2})
    assert _overlay_finding(bare, control) == "overlays_neutral"


def test_load_trades_from_disk(tmp_path: Path) -> None:
    """Round-trip: write a tiny chunk, load it, confirm fields preserved."""
    chunks_root = tmp_path / "multi_era_chunks"
    era_dir = chunks_root / "test_run" / "late_bull"
    era_dir.mkdir(parents=True)
    chunk = {
        "era": "late_bull",
        "trades": [
            {
                "return": 0.10,
                "net_return": 0.095,
                "entry_date": "2017-01-06T00:00:00",
                "exit_date": "2017-02-06T00:00:00",
                "stop_pct": 0.05,
            },
            {
                "return": -0.05,
                "net_return": -0.052,
                "entry_date": "2017-03-15T00:00:00",
                "exit_date": "2017-04-12T00:00:00",
                "stop_pct": 0.04,
                "ticker": "AAPL",
                "entry_price": 100.0,
                "exit_price": 95.0,
            },
        ],
    }
    (era_dir / "chunk_0001.json").write_text(json.dumps(chunk), encoding="utf-8")

    trades = load_trades("test_run", chunks_root=chunks_root)
    assert len(trades) == 2
    assert trades[0].ticker is None  # no augmentation
    assert trades[0].has_augmentation() is False
    assert trades[1].ticker == "AAPL"
    assert trades[1].has_augmentation() is True
    assert trades[1].entry_price == pytest.approx(100.0)


def test_load_trades_skips_ticker_files(tmp_path: Path) -> None:
    """Files ending in _tickers.json must be ignored, they hold metadata not trades."""
    chunks_root = tmp_path / "multi_era_chunks"
    era_dir = chunks_root / "test_run" / "late_bull"
    era_dir.mkdir(parents=True)
    (era_dir / "chunk_0001_tickers.json").write_text(json.dumps({"tickers": ["AAPL", "MSFT"]}), encoding="utf-8")
    (era_dir / "chunk_0001.json").write_text(json.dumps({"era": "late_bull", "trades": []}), encoding="utf-8")
    trades = load_trades("test_run", chunks_root=chunks_root)
    assert trades == []


def test_verdict_halts_when_too_few_eras_even_if_pf_clears_thresholds() -> None:
    """Reproduces the bug found on the first audit run: a single-era bare
    sweep had PF=1.317 and triggered "proceed" before the era-coverage
    guardrail was added. Must now halt on insufficient data."""
    s = _summary("stage2_only", {"recent_current": 1.317})
    assert len(s.eras) == 1
    assert len(s.eras) < MIN_ERAS_FOR_VERDICT
    assert s.pf_mean is not None and s.pf_mean >= PROCEED_PF_MEAN
    assert s.worst_era_pf is not None and s.worst_era_pf >= PROCEED_WORST
    assert _verdict(s) == "halt_insufficient_data"


def test_overlay_finding_uses_only_aligned_eras() -> None:
    """Bug: comparing a 1-era bare sweep against a 5-era control falsely
    flagged 'overlays_hurting' because the control mean was dragged down by
    eras the bare sweep never ran. Aligned comparison must only use the
    intersection of eras."""
    bare = _summary("stage2_only", {"recent_current": 1.31})
    control = _summary(
        "control_legacy",
        {
            "late_bull": 1.08,
            "volatility_chop": 0.90,
            "crash_recovery": 1.13,
            "bear_rates": 1.01,
            "recent_current": 1.31,
        },
    )
    s_pf, c_pf, common = _aligned_pf_means(bare, control)
    assert common == ["recent_current"]
    assert s_pf == pytest.approx(1.31)
    assert c_pf == pytest.approx(1.31)
    assert _overlay_finding(bare, control) == "overlays_neutral"


def test_aligned_pf_means_with_no_overlap_returns_none() -> None:
    bare = _summary("a", {"late_bull": 1.5})
    control = _summary("b", {"crash_recovery": 1.0})
    s_pf, c_pf, common = _aligned_pf_means(bare, control)
    assert common == []
    assert s_pf is None
    assert c_pf is None


def test_summarise_produces_pf_mean_and_worst() -> None:
    trades = [
        _make_trade("late_bull", 0.10, 1),
        _make_trade("late_bull", -0.05, 2),
        _make_trade("crash_recovery", 0.04, 3),
        _make_trade("crash_recovery", -0.06, 4),
    ]
    summary = _summarise("synthetic", trades)
    assert summary.n_total == 4
    # late_bull PF = 0.10 / 0.05 = 2.0
    # crash_recovery PF = 0.04 / 0.06 = 0.667
    assert summary.pf_mean == pytest.approx((2.0 + 0.04 / 0.06) / 2)
    assert summary.worst_era_pf == pytest.approx(0.04 / 0.06)
