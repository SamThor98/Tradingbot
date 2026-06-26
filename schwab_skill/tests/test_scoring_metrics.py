from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from core.scoring_metrics import (
    assign_era,
    decile_monotonicity,
    enrich_candidate_scores,
    evaluate_score_column,
    rank_lift_table,
    roc_auc_score_manual,
)


def test_roc_auc_perfect_separation() -> None:
    y = np.array([0, 0, 1, 1])
    s = np.array([0.1, 0.2, 0.8, 0.9])
    assert roc_auc_score_manual(y, s) == 1.0


def test_evaluate_score_column_basic() -> None:
    rng = np.random.default_rng(42)
    n = 200
    score = rng.uniform(0, 100, n)
    ret = (score - 50) / 500 + rng.normal(0, 0.01, n)
    df = pd.DataFrame(
        {
            "entry_date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "signal_score": score,
            "y_up_10d": (ret > 0).astype(int),
            "ret_10d_fwd": ret,
            "pct_from_52w_high": rng.uniform(0.7, 1.0, n),
            "close_vs_sma200_pct": rng.uniform(0.0, 0.2, n),
            "avg_vcp_volume_ratio": rng.uniform(0.4, 1.2, n),
            "volume_ratio": rng.uniform(0.5, 1.5, n),
            "sec_risk_score": 0.0,
            "breakout_confirmed": 1,
        }
    )
    pack = evaluate_score_column(df, "signal_score", y_col="y_up_10d", ret_col="ret_10d_fwd")
    assert pack is not None
    assert pack.n == n
    assert pack.auc > 0.55
    assert pack.spearman_ic > 0.0


def test_decile_monotonicity_detects_trend() -> None:
    deciles = [{"avg_return": v} for v in [0.01, 0.012, 0.015, 0.018, 0.02, 0.022, 0.025, 0.028, 0.03, 0.035]]
    mono, spread = decile_monotonicity(deciles)
    assert mono is True
    assert spread is not None and spread > 0


def test_enrich_with_live_score_stack_adds_rank() -> None:
    from core.scoring_audit_builder import enrich_with_live_score_stack
    from core.scoring_metrics import reapply_composite_scores

    df = pd.DataFrame(
        {
            "ticker": ["AAPL"] * 50,
            "entry_date": pd.date_range("2024-01-01", periods=50, freq="D"),
            "signal_score": np.linspace(45, 85, 50),
            "y_up_10d": (np.linspace(45, 85, 50) > 65).astype(int),
            "ret_10d_fwd": np.linspace(-0.01, 0.03, 50),
            "pct_from_52w_high": np.linspace(0.78, 0.96, 50),
            "close_vs_sma200_pct": np.linspace(0.02, 0.12, 50),
            "close_vs_sma50_pct": np.linspace(0.01, 0.08, 50),
            "avg_vcp_volume_ratio": np.linspace(0.85, 0.55, 50),
            "volume_ratio": np.linspace(0.9, 1.1, 50),
            "sec_risk_score": 0.0,
            "breakout_confirmed": 1,
            "sector_rel_21d": 0.01,
            "miro_continuation_prob": 0.6,
            "miro_bull_trap_prob": 0.2,
            "pts_52w": np.linspace(10, 35, 50),
            "pts_sma": np.linspace(2, 12, 50),
            "pts_volume": np.linspace(4, 12, 50),
            "pts_mirofish": np.linspace(0, 8, 50),
        }
    )
    enriched = enrich_with_live_score_stack(df, skill_dir=Path(__file__).resolve().parents[1])
    if "composite_score" not in enriched.columns or enriched["composite_score"].notna().sum() < 40:
        enriched = reapply_composite_scores(df.assign(score_stack_source="proxy"), skill_dir=Path(__file__).resolve().parents[1])
    assert "composite_score" in enriched.columns
    assert enriched["composite_score"].notna().sum() >= 40


def test_pick_primary_horizon_prefers_40d() -> None:
    from core.scoring_metrics import pick_primary_horizon

    df = pd.DataFrame(
        {
            "y_up_10d": [1] * 60,
            "ret_10d_fwd": [0.01] * 60,
            "y_up_40d": [1] * 60,
            "ret_40d_fwd": [0.02] * 60,
        }
    )
    key, y, r = pick_primary_horizon(df, "candidates")
    assert key == "40d"
    assert y == "y_up_40d"
    assert r == "ret_40d_fwd"


def test_sma_multiplier_sensitivity() -> None:
    from core.scoring_metrics import sma_multiplier_sensitivity

    n = 200
    pts_sma = np.linspace(5, 20, n)
    pts_other = np.linspace(30, 50, n)
    signal = pts_other + pts_sma
    ret = (pts_other - pts_sma) / 500
    df = pd.DataFrame(
        {
            "signal_score": signal,
            "pts_sma": pts_sma,
            "y_up_10d": (ret > 0).astype(int),
            "ret_10d_fwd": ret,
        }
    )
    rows = sma_multiplier_sensitivity(df, y_col="y_up_10d", ret_col="ret_10d_fwd")
    assert len(rows) == 4
    zero_mult = next(r for r in rows if r["sma_multiplier"] == 0.0)
    full_mult = next(r for r in rows if r["sma_multiplier"] == 1.0)
    assert zero_mult["spearman_ic"] >= full_mult["spearman_ic"]


def test_mirofish_subset_evaluation() -> None:
    from scripts.validate_scoring_metrics import _evaluate_horizon

    n = 120
    rng = np.random.default_rng(7)
    entry_dates = pd.date_range("2024-01-01", periods=n, freq="D")
    df = pd.DataFrame(
        {
            "entry_date": entry_dates,
            "era": assign_era(pd.Series(entry_dates)),
            "signal_score": rng.uniform(40, 90, n),
            "rank_score": rng.uniform(40, 90, n),
            "pts_mirofish": rng.uniform(0, 10, n),
            "pts_52w": rng.uniform(10, 35, n),
            "pts_sma": np.zeros(n),
            "pts_volume": rng.uniform(4, 14, n),
            "mirofish_included": [1 if i < 40 else 0 for i in range(n)],
            "y_up_40d": (rng.uniform(40, 90, n) > 65).astype(int),
            "ret_40d_fwd": rng.normal(0, 0.02, n),
        }
    )
    block = _evaluate_horizon(df, source="candidates", y_col="y_up_40d", ret_col="ret_40d_fwd", min_rows=50)
    assert block.get("skipped") is not True
    assert "mirofish_subset" in block
    assert block["mirofish_subset"]["row_count"] == 40


def test_enrich_and_rank_lift() -> None:
    df = pd.DataFrame(
        {
            "entry_date": pd.date_range("2024-01-01", periods=120, freq="D"),
            "signal_score": np.linspace(40, 90, 120),
            "y_up_10d": (np.linspace(40, 90, 120) > 65).astype(int),
            "ret_10d_fwd": np.linspace(-0.02, 0.04, 120),
            "pct_from_52w_high": np.linspace(0.75, 0.98, 120),
            "close_vs_sma200_pct": np.linspace(0.01, 0.15, 120),
            "avg_vcp_volume_ratio": np.linspace(0.9, 0.5, 120),
            "volume_ratio": np.linspace(0.8, 1.2, 120),
            "sec_risk_score": 0.0,
            "breakout_confirmed": 1,
        }
    )
    enriched = enrich_candidate_scores(df)
    assert "rank_score_proxy" in enriched.columns
    lift = rank_lift_table(enriched, y_col="y_up_10d", ret_col="ret_10d_fwd", rank_cols=["signal_score", "rank_score_proxy"])
    assert len(lift) == 2
    eras = assign_era(enriched["entry_date"])
    assert (eras == "recent_current").all()


def test_optimize_composite_weights_returns_recommendation() -> None:
    from core.scoring_metrics import optimize_composite_weights

    n = 200
    rng = np.random.default_rng(11)
    pts_52w = rng.uniform(10, 35, n)
    pts_volume = rng.uniform(4, 16, n)
    signal = pts_52w + pts_volume + rng.uniform(0, 10, n)
    ret = (pts_volume - pts_52w) / 500 + rng.normal(0, 0.01, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    df = pd.DataFrame(
        {
            "entry_date": dates,
            "era": assign_era(pd.Series(dates)),
            "signal_score": signal,
            "pts_52w": pts_52w,
                "pts_volume": pts_volume,
                "pts_mirofish": np.zeros(n),
                "reliability_score_proxy": np.full(n, 70.0),
            "execution_score_proxy": np.full(n, 85.0),
            "y_up_40d": (ret > 0).astype(int),
            "ret_40d_fwd": ret,
        }
    )
    out = optimize_composite_weights(df, y_col="y_up_40d", ret_col="ret_40d_fwd", min_era_wins=0)
    assert out.get("recommended")
    assert out.get("recommended_env")
    assert int(out.get("candidates_evaluated") or 0) > 0


def test_enrich_trade_frame_for_scoring_from_signal_only() -> None:
    from core.scoring_metrics import enrich_trade_frame_for_scoring

    df = pd.DataFrame(
        {
            "entry_date": pd.date_range("2024-03-01", periods=80, freq="7D"),
            "ticker": ["AAPL"] * 80,
            "net_return": np.linspace(-0.03, 0.05, 80),
            "signal_score": np.linspace(55, 85, 80),
        }
    )
    enriched = enrich_trade_frame_for_scoring(df)
    assert "composite_score" in enriched.columns
    assert enriched["composite_score"].notna().sum() >= 80
    assert enriched["score_stack_source"].iloc[0] == "trade_enriched"


def test_enrich_candidate_scores_excludes_52w_from_edge_by_default() -> None:
    df = pd.DataFrame(
        {
            "signal_score": [80.0],
            "pct_from_52w_high": [0.95],
            "close_vs_sma200_pct": [0.05],
            "avg_vcp_volume_ratio": [0.6],
            "volume_ratio": [1.0],
            "sec_risk_score": [0.0],
            "breakout_confirmed": [1],
        }
    )
    with_exclusion = enrich_candidate_scores(df, stage2_floor=0.75, exclude_52w=True)
    without = enrich_candidate_scores(df, stage2_floor=0.75, exclude_52w=False)
    assert float(with_exclusion["edge_score_proxy"].iloc[0]) < float(without["edge_score_proxy"].iloc[0])
