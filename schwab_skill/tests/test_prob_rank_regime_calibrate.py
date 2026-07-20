"""Regime context features + risk-off score calibration."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.calibrate import (  # noqa: E402
    apply_chop_aware_scores,
    apply_regime_aware_scores,
    fit_risk_off_blend,
)
from research.regime_context import (  # noqa: E402
    assign_era,
    attach_regime_features,
    chop_mask,
    compute_spy_regime_table,
    risk_off_mask,
)


def _spy_like(n: int = 400, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-02", periods=n)
    # Trend then selloff
    close = 300 + np.linspace(0, 80, n) + rng.normal(0, 1, n).cumsum() * 0.2
    close[250:] = close[250] + np.linspace(0, -60, n - 250)
    close = np.maximum(close, 50.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1e7, 2e7, n).astype(float),
        },
        index=idx,
    )


def test_compute_spy_regime_table_has_risk_off() -> None:
    tbl = compute_spy_regime_table(_spy_like())
    assert not tbl.empty
    assert "regime_risk_off" in tbl.columns
    assert "spy_dist_sma200_pct" in tbl.columns
    assert "regime_chop_score" in tbl.columns
    assert "spy_trend_efficiency_20d" in tbl.columns
    assert tbl["regime_risk_off"].between(0, 1).all()
    assert tbl["regime_chop_score"].between(0, 1).all()


def test_attach_regime_features_joins_and_sets_era() -> None:
    spy = _spy_like()
    asof = str(spy.index[-10].date())
    feats = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "asof_date": [asof],
            "ret_20d_prev": [0.05],
            "dist_sma50_pct": [0.02],
        }
    )
    out = attach_regime_features(feats, spy)
    assert out.loc[0, "era"] == assign_era(asof)
    assert pd.notna(out.loc[0, "spy_ret_20d"])
    assert pd.notna(out.loc[0, "rel_spy_20d"])


def test_regime_blend_moves_toward_control_in_risk_off() -> None:
    df = pd.DataFrame(
        {
            "expected_return_40d": [0.05, 0.04, 0.01, 0.00],
            "rank_score_v2": [10.0, 20.0, 90.0, 80.0],
            "net_return": [-0.02, -0.01, 0.03, 0.02],
            "era": ["bear_rates"] * 4,
            "regime_risk_off": [0.8, 0.8, 0.8, 0.8],
        }
    )
    cal = apply_regime_aware_scores(df, risk_off_blend=1.0)
    # Full blend → ranking follows rank_v2 (higher control → higher calibrated)
    order = cal.sort_values("expected_return_40d_calibrated")["rank_score_v2"].tolist()
    assert order == sorted(order)


def test_chop_aware_prefers_compression() -> None:
    df = pd.DataFrame(
        {
            "expected_return_40d": [0.05, 0.04, 0.03, 0.02],
            "compression_score": [0.2, 0.3, 0.8, 0.9],
            "breakout_velocity": [0.08, 0.06, 0.01, 0.00],
            "net_return": [-0.02, -0.01, 0.03, 0.04],
            "era": ["volatility_chop"] * 4,
            "regime_chop_score": [0.8, 0.8, 0.8, 0.8],
        }
    )
    assert chop_mask(df).all()
    cal = apply_chop_aware_scores(df, chop_blend=1.0, breakout_penalty=0.0)
    # Full blend to compression → ranking follows compression
    order = cal.sort_values("expected_return_40d_chop_cal")["compression_score"].tolist()
    assert order == sorted(order)


def test_fit_risk_off_blend_prefers_control_when_model_inverted() -> None:
    rng = np.random.default_rng(0)
    n = 80
    ctrl = rng.normal(size=n)
    # Model anti-correlated with label; control correlated
    label = ctrl + rng.normal(0, 0.1, n)
    model = -label + rng.normal(0, 0.05, n)
    df = pd.DataFrame(
        {
            "expected_return_40d": model,
            "rank_score_v2": ctrl,
            "net_return": label,
            "regime_risk_off": np.ones(n),
            "era": ["bear_rates"] * n,
        }
    )
    assert risk_off_mask(df).all()
    fit = fit_risk_off_blend(df)
    assert fit["best_blend"] >= 0.5
