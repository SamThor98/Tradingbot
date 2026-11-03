"""Purged rank trains on net return and refuses forward-return labels."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.purged_cv import assert_train_labels_respect_embargo, combinatorial_purged_splits
from research.purged_net_rank import (
    TARGET,
    assert_net_return_target,
    fit_purged_oos_scores,
    legal_feature_columns,
    purged_rank_verdict,
)


def test_refuses_forward_return_target() -> None:
    with pytest.raises(ValueError, match="net_return"):
        assert_net_return_target("ret_40d_fwd")


def test_legal_features_drop_unpurged_model_and_labels() -> None:
    kept, rejected = legal_feature_columns(
        [
            "pts_52w",
            "close_vs_sma200_pct",
            "ret_40d_fwd",
            "net_return",
            "expected_return_40d",
            "hold_days",
            "ret_20d_prev",
        ]
    )
    assert kept == ["pts_52w", "close_vs_sma200_pct", "ret_20d_prev"]
    assert any("ret_40d_fwd" in row for row in rejected)
    assert any("expected_return_40d" in row for row in rejected)
    assert any("net_return" in row for row in rejected)


def test_oos_scores_respect_embargo_and_predict_net_return() -> None:
    dates = pd.bdate_range("2020-01-01", periods=48)
    rows = []
    rng = np.random.default_rng(0)
    for i, day in enumerate(dates):
        for _k in range(8):
            signal = float(rng.normal())
            rows.append(
                {
                    "asof_date": day,
                    "era": f"g{i // 12}",
                    "ticker": f"T{i}",
                    "hold_days": 5,
                    "pts_52w": signal,
                    "net_return": signal * 0.02,
                }
            )
    df = pd.DataFrame(rows)
    fitted = fit_purged_oos_scores(
        df,
        ["pts_52w", "ret_40d_fwd", "expected_return_40d"],
        embargo_days=5,
        n_groups=4,
        n_test_groups=1,
        alpha=0.1,
    )
    assert fitted["target"] == TARGET
    assert fitted["features"] == ["pts_52w"]
    assert fitted["n_scored"] > 0
    scored = fitted["scored"]
    corr = scored["purged_net_score"].corr(scored["net_return"])
    assert corr is not None and corr > 0.5
    folds = combinatorial_purged_splits(
        scored,
        n_groups=4,
        n_test_groups=1,
        embargo_days=5,
        holding_col="hold_days",
        default_holding_days=5,
        min_train_rows=20,
    )
    assert folds
    assert assert_train_labels_respect_embargo(scored, folds[0], holding_col="hold_days") == []


def test_verdict_needs_lift_over_inverted_pts52w() -> None:
    model = {"clears_floors": True, "pf_mean": 1.46}
    control = {"pf_mean": 1.42}
    assert purged_rank_verdict(model, control) == "shadow_ready_purged_rank_not_promoted"
    assert purged_rank_verdict({"clears_floors": True, "pf_mean": 1.44}, control) == "do_not_promote_purged_rank"
    assert purged_rank_verdict({"clears_floors": False, "pf_mean": 1.50}, control) == "do_not_promote_purged_rank"
