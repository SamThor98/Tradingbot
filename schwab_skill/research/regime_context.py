"""SPY / market-regime context features for prob-rank (PIT-safe)."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from research.dataset import ERA_BOUNDS
from research.feature_engine import normalize_ohlcv

LOG = logging.getLogger(__name__)

REGIME_FEATURE_NAMES = [
    "spy_ret_20d",
    "spy_ret_60d",
    "spy_dist_sma200_pct",
    "spy_realized_vol_20d",
    "spy_above_sma200",
    "regime_risk_off",
    "rel_spy_20d",
    "spy_trend_efficiency_20d",
    "spy_choppiness_14",
    "spy_vol_of_vol_20d",
    "regime_chop_score",
]


def assign_era(asof: str | pd.Timestamp) -> str:
    d = pd.Timestamp(asof)
    for name, (lo, hi) in ERA_BOUNDS.items():
        if d >= pd.Timestamp(lo) and (hi is None or d <= pd.Timestamp(hi)):
            return name
    return "unknown"


def compute_spy_regime_table(spy_bars: pd.DataFrame) -> pd.DataFrame:
    """
    Build a daily PIT table of SPY regime features indexed by asof date.

    Uses only bars through each date (causal rolling stats).
    """
    work = normalize_ohlcv(spy_bars)
    if work.empty or len(work) < 220:
        return pd.DataFrame()
    close = work["close"].astype(float)
    log_ret = np.log(close / close.shift(1))
    sma200 = close.rolling(200, min_periods=200).mean()
    out = pd.DataFrame(index=work.index)
    out["spy_ret_20d"] = close / close.shift(20) - 1.0
    out["spy_ret_60d"] = close / close.shift(60) - 1.0
    out["spy_dist_sma200_pct"] = (close - sma200) / sma200
    out["spy_realized_vol_20d"] = log_ret.rolling(20, min_periods=20).std() * np.sqrt(252.0)
    out["spy_above_sma200"] = (close > sma200).astype(float)
    # Continuous risk-off score in [0,1]: below SMA200 and/or elevated vol
    below = (-out["spy_dist_sma200_pct"]).clip(lower=0.0, upper=0.10) / 0.10
    vol = ((out["spy_realized_vol_20d"] - 0.12) / 0.20).clip(lower=0.0, upper=1.0)
    out["regime_risk_off"] = (0.6 * below.fillna(0.0) + 0.4 * vol.fillna(0.0)).clip(0.0, 1.0)

    # Chop / mean-reversion regime: low trend efficiency, high path noise
    abs_ret_20 = (close / close.shift(20) - 1.0).abs()
    path_20 = log_ret.abs().rolling(20, min_periods=20).sum()
    out["spy_trend_efficiency_20d"] = (abs_ret_20 / path_20.replace(0.0, np.nan)).clip(0.0, 1.0)
    # Classic choppiness index-ish on 14d (100-ish scale mapped to [0,1])
    tr = pd.concat(
        [
            (work["high"] - work["low"]).astype(float),
            (work["high"].astype(float) - close.shift(1)).abs(),
            (work["low"].astype(float) - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    sum_tr_14 = tr.rolling(14, min_periods=14).sum()
    high_14 = work["high"].astype(float).rolling(14, min_periods=14).max()
    low_14 = work["low"].astype(float).rolling(14, min_periods=14).min()
    rng_14 = (high_14 - low_14).replace(0.0, np.nan)
    chop_raw = 100.0 * np.log10(sum_tr_14 / rng_14) / np.log10(14.0)
    out["spy_choppiness_14"] = ((chop_raw - 38.0) / (62.0 - 38.0)).clip(0.0, 1.0)
    vol20 = out["spy_realized_vol_20d"]
    out["spy_vol_of_vol_20d"] = vol20.rolling(20, min_periods=20).std()
    # High chop score when efficiency low and choppiness/vol-of-vol elevated
    low_eff = (1.0 - out["spy_trend_efficiency_20d"].fillna(0.5)).clip(0.0, 1.0)
    vov = (out["spy_vol_of_vol_20d"] / 0.05).clip(0.0, 1.0).fillna(0.0)
    out["regime_chop_score"] = (
        0.5 * low_eff + 0.3 * out["spy_choppiness_14"].fillna(0.0) + 0.2 * vov
    ).clip(0.0, 1.0)

    out = out.dropna(subset=["spy_dist_sma200_pct", "spy_ret_20d"], how="any")
    out["asof_date"] = out.index.strftime("%Y-%m-%d")
    return out.reset_index(drop=True)


def attach_regime_features(
    features: pd.DataFrame,
    spy_bars: pd.DataFrame,
    *,
    assign_eras: bool = True,
) -> pd.DataFrame:
    """Left-join SPY regime features onto a feature/dataset frame by asof_date."""
    if features is None or features.empty:
        return features.copy() if features is not None else pd.DataFrame()
    spy_tbl = compute_spy_regime_table(spy_bars)
    if spy_tbl.empty:
        LOG.warning("SPY regime table empty — regime features unavailable")
        out = features.copy()
        for name in REGIME_FEATURE_NAMES:
            if name not in out.columns:
                out[name] = np.nan
        if assign_eras and "era" not in out.columns:
            out["era"] = [assign_era(d) for d in out["asof_date"]]
        return out

    out = features.copy()
    out["asof_date"] = pd.to_datetime(out["asof_date"]).dt.strftime("%Y-%m-%d")
    keep = ["asof_date"] + [c for c in REGIME_FEATURE_NAMES if c != "rel_spy_20d"]
    spy_keep = spy_tbl[keep].drop_duplicates(subset=["asof_date"], keep="last")
    # drop existing regime cols before join to avoid _x/_y
    drop_cols = [c for c in REGIME_FEATURE_NAMES if c in out.columns and c != "rel_spy_20d"]
    out = out.drop(columns=drop_cols, errors="ignore")
    out = out.merge(spy_keep, on="asof_date", how="left")
    if "ret_20d_prev" in out.columns and "spy_ret_20d" in out.columns:
        out["rel_spy_20d"] = pd.to_numeric(out["ret_20d_prev"], errors="coerce") - pd.to_numeric(
            out["spy_ret_20d"], errors="coerce"
        )
    else:
        out["rel_spy_20d"] = np.nan
    if assign_eras:
        out["era"] = [assign_era(d) for d in out["asof_date"]]
    return out


def risk_off_mask(df: pd.DataFrame, *, threshold: float = 0.35) -> pd.Series:
    """Boolean mask for risk-off rows using regime_risk_off or spy_above_sma200."""
    if "regime_risk_off" in df.columns and df["regime_risk_off"].notna().any():
        return pd.to_numeric(df["regime_risk_off"], errors="coerce").fillna(0.0) >= float(threshold)
    if "spy_above_sma200" in df.columns:
        return pd.to_numeric(df["spy_above_sma200"], errors="coerce").fillna(1.0) < 0.5
    if "era" in df.columns:
        return df["era"].astype(str).isin(["bear_rates", "crash_recovery"])
    return pd.Series(False, index=df.index)


def chop_mask(df: pd.DataFrame, *, threshold: float = 0.55) -> pd.Series:
    """
    Boolean mask for chop / low-efficiency regimes.

    Prefers ``regime_chop_score``; falls back to era label ``volatility_chop``.
    """
    if "regime_chop_score" in df.columns and df["regime_chop_score"].notna().any():
        return pd.to_numeric(df["regime_chop_score"], errors="coerce").fillna(0.0) >= float(threshold)
    if "spy_trend_efficiency_20d" in df.columns and df["spy_trend_efficiency_20d"].notna().any():
        return pd.to_numeric(df["spy_trend_efficiency_20d"], errors="coerce").fillna(1.0) <= 0.35
    if "era" in df.columns:
        return df["era"].astype(str) == "volatility_chop"
    return pd.Series(False, index=df.index)


def fetch_spy_bars(*, skill_dir: Any = None, days: int = 4000) -> pd.DataFrame:
    from pathlib import Path

    from market_data import get_daily_history

    root = Path(skill_dir) if skill_dir is not None else Path(__file__).resolve().parent.parent
    df = get_daily_history("SPY", days=days, skill_dir=root)
    if df is None:
        return pd.DataFrame()
    return df
