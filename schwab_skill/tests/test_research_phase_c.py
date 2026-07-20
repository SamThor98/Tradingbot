"""Phase C: dataset builder, leakage, LightGBM walk-forward, counterfactual."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.counterfactual import run_prob_rank_counterfactual  # noqa: E402
from research.dataset import build_rank_dataset, resolve_feature_columns  # noqa: E402
from research.feature_engine import compute_ohlcv_features  # noqa: E402
from research.infer import attach_scores_to_trades, predict_frame  # noqa: E402
from research.labels import attach_forward_labels, forward_labels_at_index  # noqa: E402
from research.leakage import (  # noqa: E402
    assert_no_label_columns_in_features,
    purge_gap_mask,
    validate_dataset_leakage,
    walk_forward_splits,
)
from research.report import write_experiment_report  # noqa: E402
from research.train import train_prob_rank_model  # noqa: E402

pytest.importorskip("lightgbm")


def _synthetic_uptrend(n: int = 400, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-02", periods=n)
    close = 40 + np.linspace(0, 100, n) + rng.normal(0, 0.3, n).cumsum() * 0.03
    close = np.maximum(close, 5.0)
    high = close * 1.01
    low = close * 0.99
    open_ = close.copy()
    volume = rng.integers(700_000, 1_400_000, n).astype(float)
    volume[-20:] *= 0.5
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_forward_labels_use_future_bars_only_for_labels() -> None:
    df = _synthetic_uptrend(120)
    labels = forward_labels_at_index(df, 50)
    assert labels is not None
    assert "ret_40d_fwd" in labels
    # Spot-check math
    c0 = float(df["close"].iloc[50])
    c40 = float(df["close"].iloc[90])
    assert labels["ret_40d_fwd"] == pytest.approx((c40 - c0) / c0)


def test_attach_forward_labels_requires_40_bars_ahead() -> None:
    df = _synthetic_uptrend(80)
    asof = str(df.index[-5].date())
    row = {"asof_date": asof, "ticker": "X"}
    assert attach_forward_labels(row, df) is None


def test_leakage_rejects_forward_columns_as_features() -> None:
    errs = assert_no_label_columns_in_features(["ret_40d_fwd", "dist_sma50_pct", "ret_20d_prev"])
    assert any("ret_40d_fwd" in e for e in errs)
    assert not any("ret_20d_prev" in e for e in errs)


def test_validate_dataset_leakage_ok_for_clean_frame() -> None:
    df = pd.DataFrame(
        {
            "asof_date": ["2020-01-02", "2020-01-03"],
            "ticker": ["A", "A"],
            "dist_sma50_pct": [0.1, 0.2],
            "ret_40d_fwd": [0.01, -0.02],
            "feature_coverage": [0.9, 0.9],
        }
    )
    report = validate_dataset_leakage(df, ["dist_sma50_pct"])
    assert report.ok


def test_purge_gap_drops_near_test_boundary() -> None:
    train = pd.Series(pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01"]))
    test = pd.Series(pd.to_datetime(["2020-03-15", "2020-04-01"]))
    keep, _ = purge_gap_mask(train, test, purge_days=40)
    # 2020-03-01 is within 40d of 2020-03-15 → dropped
    assert bool(keep.iloc[0]) is True
    assert bool(keep.iloc[2]) is False


def test_build_rank_dataset_from_bars(tmp_path: Path) -> None:
    bars = _synthetic_uptrend(350)
    ds, path, manifest = build_rank_dataset(
        ticker_bars={"SYN": bars},
        date_start="2018-06-01",
        date_end="2019-06-01",
        label_set="fwd40",
        skill_dir=tmp_path,
        write=True,
    )
    assert len(ds) >= 1
    assert path is not None and path.is_file()
    assert "ret_40d_fwd" in ds.columns
    assert manifest["leakage"]["ok"] is True
    feats = resolve_feature_columns(ds)
    assert "volume_score" in feats or "dist_sma50_pct" in feats
    assert "ret_40d_fwd" not in feats


def _multi_era_training_frame(n_per_era: int = 80) -> pd.DataFrame:
    """Construct a labeled feature frame spanning catalog eras for WF tests."""
    rng = np.random.default_rng(0)
    eras = [
        ("late_bull", "2016-06-01"),
        ("volatility_chop", "2018-06-01"),
        ("crash_recovery", "2020-06-01"),
        ("bear_rates", "2022-06-01"),
        ("recent_current", "2024-06-01"),
    ]
    rows: list[dict] = []
    for era, start in eras:
        dates = pd.bdate_range(start, periods=n_per_era)
        for i, dt in enumerate(dates):
            # Signal features that weakly predict forward return
            f1 = float(rng.normal())
            f2 = float(rng.normal())
            noise = float(rng.normal(0, 0.02))
            ret = 0.01 * f1 - 0.005 * f2 + noise
            rows.append(
                {
                    "asof_date": str(dt.date()),
                    "ticker": f"T{i % 5}",
                    "era": era,
                    "dist_sma50_pct": f1 * 0.01,
                    "volume_score": max(0.0, min(1.0, 0.5 + 0.1 * f2)),
                    "atr_pct": 0.02 + 0.001 * abs(f1),
                    "ret_20d_prev": f2 * 0.01,
                    "stage_score": max(0.0, min(1.0, 0.5 + 0.05 * f1)),
                    "breakout_quality_score": max(0.0, min(1.0, 0.5 + 0.05 * f2)),
                    "feature_coverage": 1.0,
                    "ret_40d_fwd": ret,
                    "y_up_40d": int(ret > 0),
                    "drawdown_40d": min(0.0, ret - 0.01),
                    "net_return": ret * 0.9,
                    "rank_score_v2": 50 + 10 * f1,
                }
            )
    return pd.DataFrame(rows)


def test_walk_forward_splits_expanding() -> None:
    from research.dataset import ERA_BOUNDS

    df = _multi_era_training_frame(40)
    folds = walk_forward_splits(df, ERA_BOUNDS, min_train_rows=20)
    assert len(folds) >= 2
    assert folds[0]["test_era"] == "volatility_chop"
    assert "late_bull" in folds[0]["train_eras"]


def test_train_prob_rank_and_report(tmp_path: Path) -> None:
    df = _multi_era_training_frame(60)
    df["dataset_id"] = "test_ds"
    feature_cols = [
        "dist_sma50_pct",
        "volume_score",
        "atr_pct",
        "ret_20d_prev",
        "stage_score",
        "breakout_quality_score",
    ]
    artifact = train_prob_rank_model(
        df,
        feature_cols,
        skill_dir=tmp_path,
        num_boost_round=40,
        early_stopping_rounds=10,
        write=True,
    )
    assert artifact.get("model_id")
    assert (tmp_path / "research_store" / "models" / artifact["model_id"] / "model.txt").is_file()
    scored = predict_frame(artifact, df)
    assert "expected_return_40d" in scored.columns
    out = write_experiment_report(
        run_id="unit_test_run",
        artifact=artifact,
        scored_df=scored,
        skill_dir=tmp_path,
    )
    assert (out / "metrics.json").is_file()
    assert (out / "shap_summary.json").is_file()
    assert (out / "manifest.json").is_file()


def test_counterfactual_top_n_vs_control() -> None:
    trades = pd.DataFrame(
        {
            "ticker": ["A", "A", "B", "B", "C", "C"],
            "entry_date": ["2020-01-02"] * 3 + ["2020-01-03"] * 3,
            "net_return": [0.05, -0.02, 0.03, 0.01, -0.04, 0.02],
            "era": ["crash_recovery"] * 6,
            "rank_score_v2": [90, 40, 80, 30, 20, 70],
        }
    )
    scored = pd.DataFrame(
        {
            "ticker": ["A", "B", "C", "A", "B", "C"],
            "asof_date": ["2020-01-02", "2020-01-02", "2020-01-02", "2020-01-03", "2020-01-03", "2020-01-03"],
            "expected_return_40d": [0.04, 0.02, -0.01, 0.03, 0.01, 0.00],
        }
    )
    result = run_prob_rank_counterfactual(trades, scored, top_n=1, control_percentile=50)
    assert result["baseline"]["n"] == 6
    assert result["prob_rank"]["n"] == 2  # top-1 per day × 2 days
    assert result["rank_v2_control"] is not None
    assert result["coverage"] == 1.0


def test_attach_scores_to_trades_join() -> None:
    trades = pd.DataFrame(
        {"ticker": ["aaa"], "entry_date": ["2021-05-10"], "net_return": [0.01]}
    )
    scored = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "asof_date": ["2021-05-10"],
            "expected_return_40d": [0.02],
            "confidence": [0.7],
        }
    )
    merged = attach_scores_to_trades(trades, scored)
    assert float(merged.iloc[0]["expected_return_40d"]) == pytest.approx(0.02)


def test_ohlcv_features_still_smoke() -> None:
    feats = compute_ohlcv_features(_synthetic_uptrend())
    assert feats.get("ret_40d_fwd") is None  # must not leak forward labels into features
