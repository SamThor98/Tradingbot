"""Phase F ops pipeline: smoke orchestration + dual-run gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

pytest.importorskip("lightgbm")

from research.ops_pipeline import (  # noqa: E402
    assess_dual_run,
    make_synthetic_universe,
    run_ops_pipeline,
    trades_from_labeled_dataset,
)


def test_assess_dual_run_ok_and_fail() -> None:
    ok = assess_dual_run(
        {
            "equal_weight_top_n": {"n": 100, "pf_mean_eras": 1.25, "worst_era_pf": 1.05},
            "rank_v2_control": {"n": 80, "pf_mean": 1.22, "worst_era_pf": 1.02},
        },
        min_trades=50,
    )
    assert ok["dual_run_ok"] is True

    fail = assess_dual_run(
        {
            "equal_weight_top_n": {"n": 10, "pf_mean_eras": 1.25, "worst_era_pf": 1.05},
            "rank_v2_control": {"n": 80, "pf_mean": 1.22, "worst_era_pf": 1.02},
        },
        min_trades=50,
    )
    assert fail["dual_run_ok"] is False


def test_synthetic_universe_and_trades() -> None:
    bars = make_synthetic_universe(n_tickers=3, start="2018-01-02", end="2020-12-31")
    assert set(bars) == {"SYN0", "SYN1", "SYN2"}
    assert len(bars["SYN0"]) > 200


def test_smoke_ops_pipeline(tmp_path: Path) -> None:
    result = run_ops_pipeline(
        skill_dir=tmp_path,
        mode="smoke",
        date_start="2017-01-01",
        date_end="2023-06-01",
        top_n=3,
        num_boost_round=40,
        artifact_dir=tmp_path / "artifacts",
        apply_registry=False,
    )
    assert result.ok, result.errors
    assert "dataset" in result.steps
    assert "train" in result.steps
    assert "portfolio" in result.steps
    assert "promotion" in result.steps
    assert result.model_dir is not None
    assert Path(result.model_dir).is_dir()
    assert result.promotion_path is not None
    assert Path(result.promotion_path).is_file()
    assert result.dual_run.get("dual_run_ok") is not None


def test_trades_from_dataset_sample() -> None:
    import pandas as pd

    ds = pd.DataFrame(
        {
            "ticker": ["A", "B", "C"] * 40,
            "asof_date": pd.bdate_range("2020-01-02", periods=120).strftime("%Y-%m-%d"),
            "ret_40d_fwd": [0.01, -0.02, 0.03] * 40,
            "era": ["crash_recovery"] * 120,
        }
    )
    trades = trades_from_labeled_dataset(ds, sample_frac=0.5, seed=1)
    assert len(trades) >= 20
    assert "rank_score_v2" in trades.columns
    assert "net_return" in trades.columns
