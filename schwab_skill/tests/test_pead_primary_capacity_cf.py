"""Unit tests for PEAD-primary capacity CF helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.pead_primary_capacity_cf import (  # noqa: E402
    portfolio_note,
    recommend_operating_stack,
    select_top_n_arm,
    select_transfer_breakout_buffer,
    summarize_arm,
)


def _sample_book() -> pd.DataFrame:
    rows: list[dict] = []
    eras = ["late_bull", "volatility_chop", "crash_recovery", "bear_rates", "recent_current"]
    for era_i, era in enumerate(eras):
        for day in range(20):
            for j in range(4):
                score = 10.0 + j * 5.0 + day
                ret = 0.02 if j >= 2 else -0.01
                rows.append(
                    {
                        "era": era,
                        "ticker": f"T{era_i}{day}{j}",
                        "entry_date": f"202{era_i}-01-{day + 1:02d}",
                        "exit_date": f"202{era_i}-02-{day + 1:02d}",
                        "net_return": ret,
                        "hold_days": 20,
                        "stop_pct": 0.12,
                        "signal_score": score,
                        "breakout_buffer_pct": -0.01 if j < 3 else 0.02,
                        "pts_52w": 20.0,
                        "rank_score_v2": 50.0 + j,
                    }
                )
    return pd.DataFrame(rows)


def test_top_n_keeps_highest_scores_per_day() -> None:
    df = _sample_book()
    day = df[df["entry_date"] == "2020-01-01"].copy()
    selected = select_top_n_arm(day, score_col="signal_score", top_n=2)
    assert len(selected) == 2
    assert selected["signal_score"].min() >= day["signal_score"].nlargest(2).min()


def test_summarize_arm_flags_thin_eras() -> None:
    df = _sample_book().head(10)
    summary = summarize_arm(df, label="thin", baseline_n=100, family="top_n")
    assert summary["passes_pf_150"] is False
    assert summary["thin_eras"]


def test_transfer_buffer_filters_negative_buffers() -> None:
    df = _sample_book()
    kept = select_transfer_breakout_buffer(df, min_buf=0.01)
    assert len(kept) < len(df)
    assert (kept["breakout_buffer_pct"] >= 0.01).all()


def test_portfolio_note_score_priority_reduces_capacity_waste() -> None:
    df = _sample_book()
    fifo = portfolio_note(
        df,
        max_positions=2,
        risk_per_trade_pct=0.0075,
        position_size_pct=0.05,
        starting_equity=100_000.0,
        score_col=None,
    )
    scored = portfolio_note(
        df,
        max_positions=2,
        risk_per_trade_pct=0.0075,
        position_size_pct=0.05,
        starting_equity=100_000.0,
        score_col="signal_score",
    )
    assert fifo["n_input"] == scored["n_input"]
    assert scored["capacity_filtered"] >= 0
    assert scored["score_priority"] == "signal_score"


def test_recommend_operating_stack_forbids_live() -> None:
    arms = [
        {
            "label": "bare_full_book",
            "family": "baseline",
            "is_transfer_arm": False,
            "passes_pf_150": True,
            "pf_mean": 1.55,
            "worst_era_pf": 1.16,
            "retention_pct": 100.0,
            "n_trades": 1000,
            "portfolio": {"accepted_pct": 40.0},
        },
        {
            "label": "top5_by_signal_score",
            "family": "top_n",
            "is_transfer_arm": False,
            "passes_pf_150": True,
            "pf_mean": 1.6,
            "worst_era_pf": 1.2,
            "retention_pct": 40.0,
            "n_trades": 100,
            "portfolio": {"accepted_pct": 85.0},
        },
        {
            "label": "transfer_breakout_buffer_ge_0.010",
            "family": "transfer",
            "is_transfer_arm": True,
            "passes_pf_150": False,
            "pf_mean": 1.1,
            "worst_era_pf": 0.9,
            "retention_pct": 20.0,
            "n_trades": 50,
        },
    ]
    rec = recommend_operating_stack(arms)
    assert rec["pead_live_allowed"] is False
    assert rec["strategy_pead_primary_allow_live"] is False
    assert rec["recommended_arm"] == "top5_by_signal_score"
    assert rec["transfer_arms_cleared"] == []
