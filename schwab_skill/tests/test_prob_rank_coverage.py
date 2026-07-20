"""Trade-entry coverage helpers for prob-rank dual-run."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.counterfactual import run_prob_rank_counterfactual  # noqa: E402
from research.coverage import coverage_report, missing_trade_keys  # noqa: E402


def test_missing_trade_keys_detects_gaps() -> None:
    trades = pd.DataFrame(
        {
            "ticker": ["A", "B", "C"],
            "entry_date": ["2020-01-02", "2020-01-03", "2020-01-04"],
            "net_return": [0.01, -0.02, 0.03],
            "era": ["crash_recovery"] * 3,
        }
    )
    scored = pd.DataFrame(
        {
            "ticker": ["A", "B"],
            "asof_date": ["2020-01-02", "2020-01-03"],
            "expected_return_40d": [0.02, 0.01],
        }
    )
    miss = missing_trade_keys(trades, scored)
    assert list(miss["ticker"]) == ["C"]


def test_coverage_report_by_era() -> None:
    trades = pd.DataFrame(
        {
            "ticker": ["A", "B"],
            "entry_date": ["2020-01-02", "2021-01-04"],
            "net_return": [0.01, 0.02],
            "era": ["crash_recovery", "crash_recovery"],
        }
    )
    scored = pd.DataFrame(
        {
            "ticker": ["A"],
            "asof_date": ["2020-01-02"],
            "expected_return_40d": [0.02],
        }
    )
    rep = coverage_report(trades, scored)
    assert rep["n_trades"] == 2
    assert rep["n_scored"] == 1
    assert rep["coverage"] == 0.5


def test_counterfactual_includes_by_era() -> None:
    trades = pd.DataFrame(
        {
            "ticker": ["A", "B", "A", "B"],
            "entry_date": ["2020-01-02", "2020-01-02", "2020-01-03", "2020-01-03"],
            "net_return": [0.05, -0.02, 0.03, -0.01],
            "era": ["crash_recovery", "crash_recovery", "bear_rates", "bear_rates"],
            "rank_score_v2": [90, 40, 80, 30],
        }
    )
    scored = pd.DataFrame(
        {
            "ticker": ["A", "B", "A", "B"],
            "asof_date": ["2020-01-02", "2020-01-02", "2020-01-03", "2020-01-03"],
            "expected_return_40d": [0.04, 0.01, 0.03, 0.00],
        }
    )
    result = run_prob_rank_counterfactual(trades, scored, min_percentile=50, control_percentile=50)
    assert "by_era" in result["prob_rank"]
    assert "crash_recovery" in result["prob_rank"]["by_era"]
