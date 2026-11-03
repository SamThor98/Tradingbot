"""Point-in-time slopes of fast averages, for research screens only.

The live scanner does not read these columns. They answer whether a short
average that is rising quickly would have improved realized profit factor on
a frozen trade book.

Definitions, all causal (bar t uses bars at or before t):

- ``ema8`` is a standard EMA with span 8 (alpha = 2/9). It is the fast average.
- ``ema21`` is the same formula with span 21, so a result that appears only on
  ema8 is about speed rather than "any rising average".
- ``wema10`` is Wilder smoothing with period 10 (alpha = 1/10). That is the
  usual trading meaning of WEMA.
- ``vwma10`` is a 10-day volume-weighted average of typical price
  ``(high + low + close) / 3``. Daily bars have no session VWAP; this is the
  daily analogue that does not reset every morning.
- ``avwap`` is an anchored VWAP. The anchor is the earliest lowest low in the
  window ``[t-63, t-10]``. The last 10 sessions are excluded so the breakout
  day itself cannot become the anchor.
- Slopes are ordinary least squares per bar, then divided by that bar's close.
  A value of 0.01 means the average rose by about 1% of price per day.
  ``sma_20_slope`` uses a 20-bar slope of the 20-day SMA, matching the
  research registry. ``sma20_slope_10`` uses a 10-bar slope so it shares a
  window with the fast averages.

Session VWAP is not computed. On daily bars it is a single point per day.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np
import pandas as pd

EMA_FAST_SPAN = 8
EMA_SLOW_SPAN = 21
WEMA_PERIOD = 10
VWMA_WINDOW = 10
SLOPE_WINDOW = 10
SMA_SLOPE_WINDOW = 20
SMA_WINDOW = 20
AVWAP_LOOKBACK = 63
AVWAP_EXCLUDE_RECENT = 10
MIN_BARS = 80

FEATURE_COLUMNS: tuple[str, ...] = (
    "ema8_slope_10",
    "wema10_slope_10",
    "vwma10_slope_10",
    "ema21_slope_10",
    "sma20_slope_10",
    "sma_20_slope",
    "ema8_minus_sma20_slope_10",
    "avwap_dist_pct",
    "avwap_slope_10",
)

# New hypotheses. sma_20_slope and sma20_slope_10 are controls, not candidates.
CANDIDATE_COLUMNS: tuple[str, ...] = (
    "ema8_slope_10",
    "wema10_slope_10",
    "vwma10_slope_10",
    "ema21_slope_10",
    "ema8_minus_sma20_slope_10",
    "avwap_dist_pct",
    "avwap_slope_10",
)

PF_MEAN_FLOOR = 1.20
WORST_ERA_FLOOR = 1.00
MIN_PF_LIFT = 0.03


def _as_float_array(values: pd.Series | np.ndarray) -> np.ndarray:
    return np.asarray(pd.to_numeric(values, errors="coerce"), dtype=float)


def rolling_ols_slope(values: pd.Series | np.ndarray, window: int) -> np.ndarray:
    """OLS slope per step on each trailing window. NaN until the window is full."""
    y = _as_float_array(values)
    out = np.full(y.shape[0], np.nan)
    n = int(window)
    if n < 2 or y.shape[0] < n:
        return out
    x = np.arange(n, dtype=float)
    x = x - x.mean()
    denom = float(np.dot(x, x))
    if denom <= 0:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(y, n)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        y_mean = np.nanmean(windows, axis=1)
    numer = (windows - y_mean[:, None]) @ x
    slopes = numer / denom
    bad = np.isnan(windows).any(axis=1)
    slopes = slopes.astype(float)
    slopes[bad] = np.nan
    out[n - 1 :] = slopes
    return out


def ema(close: pd.Series, span: int) -> pd.Series:
    """Standard EMA. Alpha is 2 / (span + 1), seeded with the first close."""
    return close.astype(float).ewm(span=int(span), adjust=False).mean()


def wilder_ema(close: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing. Alpha is 1 / period, seeded with the first close."""
    return close.astype(float).ewm(alpha=1.0 / float(period), adjust=False).mean()


def volume_weighted_average(df: pd.DataFrame, window: int) -> pd.Series:
    """Rolling volume-weighted average of typical price. NaN while volume is 0."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    typical = (high + low + close) / 3.0
    num = (typical * volume).rolling(int(window), min_periods=int(window)).sum()
    den = volume.rolling(int(window), min_periods=int(window)).sum()
    out = num / den.replace(0.0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def base_anchor_index(
    low: np.ndarray,
    index: int,
    *,
    lookback: int = AVWAP_LOOKBACK,
    exclude_recent: int = AVWAP_EXCLUDE_RECENT,
) -> int | None:
    """Earliest lowest low in ``[index-lookback, index-exclude_recent]``."""
    end_anchor = int(index) - int(exclude_recent)
    start = int(index) - int(lookback)
    if end_anchor < 0 or end_anchor < start:
        return None
    start = max(0, start)
    segment = low[start : end_anchor + 1]
    if segment.size == 0 or np.isnan(segment).all():
        return None
    # nanargmin is not earliest-on-ties once nans exist; mask nans to +inf.
    usable = np.where(np.isnan(segment), np.inf, segment)
    if not np.isfinite(usable).any():
        return None
    return start + int(np.argmin(usable))


def anchored_vwap(df: pd.DataFrame) -> pd.Series:
    """Anchored VWAP from the base low through each bar. Leading bars are NaN."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    volume = df["volume"].to_numpy(dtype=float)
    typical = (high + low + close) / 3.0
    typical_volume = typical * volume
    cum_tv = np.cumsum(np.where(np.isnan(typical_volume), 0.0, typical_volume))
    cum_vol = np.cumsum(np.where(np.isnan(volume), 0.0, volume))
    out = np.full(len(df), np.nan)
    first = AVWAP_LOOKBACK
    for i in range(first, len(df)):
        anchor = base_anchor_index(low, i)
        if anchor is None:
            continue
        vol_before = cum_vol[anchor - 1] if anchor else 0.0
        tv_before = cum_tv[anchor - 1] if anchor else 0.0
        vol_sum = cum_vol[i] - vol_before
        if vol_sum <= 0:
            continue
        out[i] = (cum_tv[i] - tv_before) / vol_sum
    return pd.Series(out, index=df.index, name="avwap")


def _slope_over_price(series: pd.Series, close: pd.Series, window: int) -> pd.Series:
    slope = rolling_ols_slope(series, window)
    price = close.astype(float).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        norm = np.where(price > 0, slope / price, np.nan)
    return pd.Series(norm, index=series.index)


def prepare_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Lower-case OHLCV, sorted unique dates, no timezone."""
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"OHLCV missing columns: {sorted(missing)}")
    if "date" in out.columns and not isinstance(out.index, pd.DatetimeIndex):
        out["date"] = pd.to_datetime(out["date"])
        out = out.set_index("date")
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def compute_fast_slope_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Feature columns aligned to ``df``. Values at t do not use bars after t."""
    work = prepare_ohlcv(df)
    close = work["close"].astype(float)
    empty = pd.DataFrame(index=work.index, columns=list(FEATURE_COLUMNS), dtype=float)
    if len(work) < MIN_BARS:
        return empty

    ema8 = ema(close, EMA_FAST_SPAN)
    ema21 = ema(close, EMA_SLOW_SPAN)
    wema = wilder_ema(close, WEMA_PERIOD)
    vwma = volume_weighted_average(work, VWMA_WINDOW)
    sma20 = close.rolling(SMA_WINDOW, min_periods=SMA_WINDOW).mean()
    avwap = anchored_vwap(work)

    ema8_slope = _slope_over_price(ema8, close, SLOPE_WINDOW)
    sma20_slope_10 = _slope_over_price(sma20, close, SLOPE_WINDOW)
    frame = pd.DataFrame(
        {
            "ema8_slope_10": ema8_slope,
            "wema10_slope_10": _slope_over_price(wema, close, SLOPE_WINDOW),
            "vwma10_slope_10": _slope_over_price(vwma, close, SLOPE_WINDOW),
            "ema21_slope_10": _slope_over_price(ema21, close, SLOPE_WINDOW),
            "sma20_slope_10": sma20_slope_10,
            "sma_20_slope": _slope_over_price(sma20, close, SMA_SLOPE_WINDOW),
            "ema8_minus_sma20_slope_10": ema8_slope - sma20_slope_10,
            "avwap_dist_pct": (close - avwap) / avwap.where(avwap > 0),
            "avwap_slope_10": _slope_over_price(avwap, close, SLOPE_WINDOW),
        },
        index=work.index,
    )
    return frame.replace([np.inf, -np.inf], np.nan)


def promotion_verdict(
    pf_mean: float | None,
    worst_era_pf: float | None,
    baseline_pf_mean: float | None,
    *,
    pf_mean_floor: float = PF_MEAN_FLOOR,
    worst_floor: float = WORST_ERA_FLOOR,
    min_lift: float = MIN_PF_LIFT,
) -> str:
    """``candidate`` only when the arm clears the signal gate and beats its own baseline."""
    if pf_mean is None or worst_era_pf is None or baseline_pf_mean is None:
        return "inconclusive"
    if not all(math.isfinite(v) for v in (pf_mean, worst_era_pf, baseline_pf_mean)):
        return "inconclusive"
    lift = float(pf_mean) - float(baseline_pf_mean)
    if float(pf_mean) >= pf_mean_floor and float(worst_era_pf) >= worst_floor and lift >= min_lift:
        return "candidate"
    return "reject"


def verdict_row(screen: dict[str, Any], arm: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """One feature's decision, using the steep arm against the covered-row baseline."""
    verdict = promotion_verdict(arm.get("pf_mean"), arm.get("worst_era_pf"), baseline.get("pf_mean"))
    return {
        "feature": screen.get("feature"),
        "verdict": verdict,
        "ic_net_return": screen.get("ic_net_return"),
        "sign_consistent_eras": screen.get("sign_consistent_eras"),
        "n_eras_with_ic": screen.get("n_eras_with_ic"),
        "steep_pf_mean": arm.get("pf_mean"),
        "steep_worst_era_pf": arm.get("worst_era_pf"),
        "steep_n": arm.get("n"),
        "baseline_pf_mean": baseline.get("pf_mean"),
        "baseline_worst_era_pf": baseline.get("worst_era_pf"),
        "lift": (
            None
            if arm.get("pf_mean") is None or baseline.get("pf_mean") is None
            else round(float(arm["pf_mean"]) - float(baseline["pf_mean"]), 4)
        ),
    }
