"""Triple-barrier labels aligned to adaptive-stop + exit-grace (15/40) knobs.

Barriers: stop, profit, timeout. Output: label, barrier_hit, holding_days, ret_net.
Does not import backtest.py — copies the live stop/grace semantics from config.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from research.feature_engine import normalize_ohlcv

BARRIER_STOP = "stop"
BARRIER_PROFIT = "profit"
BARRIER_TIMEOUT = "timeout"

BARRIER_LABEL_COLUMNS = [
    "barrier_label",
    "barrier_hit",
    "barrier_holding_days",
    "barrier_ret_net",
]


def _atr_14(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> float:
    if len(close) < 15:
        return 0.0
    prev_close = close[:-1]
    h = high[1:]
    lo = low[1:]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_close), np.abs(lo - prev_close)))
    window = tr[-14:]
    if len(window) < 14:
        return 0.0
    return float(np.mean(window))


def resolve_barrier_knobs(
    bars: pd.DataFrame,
    entry_idx: int,
    *,
    skill_dir: Any = None,
    stop_pct: float | None = None,
) -> dict[str, float | int]:
    """Stop/target/hold knobs matching config.get_adaptive_stop_* and exit grace."""
    from config import (
        get_adaptive_stop_atr_mult,
        get_adaptive_stop_base_pct,
        get_adaptive_stop_enabled,
        get_adaptive_stop_max_pct,
        get_adaptive_stop_min_pct,
        get_adaptive_stop_trend_lookback,
        get_barrier_profit_r_multiple,
        get_exit_max_hold_days,
        get_exit_min_hold_days_before_trail,
    )

    hold = int(get_exit_max_hold_days(skill_dir))
    grace = int(get_exit_min_hold_days_before_trail(skill_dir))
    profit_r = float(get_barrier_profit_r_multiple(skill_dir))
    if stop_pct is not None and stop_pct > 0:
        sp = float(stop_pct)
        return {
            "stop_pct": sp,
            "profit_pct": sp * profit_r,
            "max_hold_days": hold,
            "min_hold_before_trail": grace,
            "profit_r": profit_r,
        }

    base_pct = float(get_adaptive_stop_base_pct(skill_dir))
    min_pct = float(get_adaptive_stop_min_pct(skill_dir))
    max_pct = float(get_adaptive_stop_max_pct(skill_dir))
    atr_mult = float(get_adaptive_stop_atr_mult(skill_dir))
    lookback = max(10, int(get_adaptive_stop_trend_lookback(skill_dir)))
    sp = base_pct
    if get_adaptive_stop_enabled(skill_dir) and entry_idx >= 0:
        close = bars["close"].astype(float).to_numpy()
        high = bars["high"].astype(float).to_numpy() if "high" in bars.columns else close
        low = bars["low"].astype(float).to_numpy() if "low" in bars.columns else close
        price = float(close[entry_idx])
        atr = _atr_14(high[: entry_idx + 1], low[: entry_idx + 1], close[: entry_idx + 1])
        if price > 0 and atr > 0:
            atr_pct = atr / price
            prev_i = max(0, entry_idx - lookback)
            prev_price = float(close[prev_i])
            trend = ((price / prev_price) - 1.0) if prev_price > 0 else 0.0
            sp = atr_pct * atr_mult
            if trend < -0.03:
                sp *= 1.2
            elif trend > 0.06:
                sp *= 0.9
            sp = max(min_pct, min(max_pct, sp))
        else:
            sp = max(min_pct, min(max_pct, base_pct))
    return {
        "stop_pct": float(sp),
        "profit_pct": float(sp) * profit_r,
        "max_hold_days": hold,
        "min_hold_before_trail": grace,
        "profit_r": profit_r,
    }


def triple_barrier_at_index(
    df: pd.DataFrame,
    idx: int,
    *,
    skill_dir: Any = None,
    stop_pct: float | None = None,
) -> dict[str, Any] | None:
    """
    Label the trade that would enter at bar ``idx`` (close).

    Stop during grace uses the initial stop (not trailing), matching
    ``backtest._stop_triggered``. Profit may hit on any bar. Timeout at max hold.
    """
    norm = normalize_ohlcv(df) if not isinstance(df.index, pd.DatetimeIndex) else df
    if idx < 0 or idx >= len(norm) - 1:
        return None
    knobs = resolve_barrier_knobs(norm, idx, skill_dir=skill_dir, stop_pct=stop_pct)
    entry = float(norm["close"].iloc[idx])
    if entry <= 0:
        return None
    stop_pct_v = float(knobs["stop_pct"])
    profit_pct = float(knobs["profit_pct"])
    max_hold = int(knobs["max_hold_days"])
    grace = int(knobs["min_hold_before_trail"])
    last_idx = min(idx + max_hold, len(norm) - 1)
    highest = entry
    hit = BARRIER_TIMEOUT
    exit_idx = last_idx
    for j in range(idx + 1, last_idx + 1):
        px = float(norm["close"].iloc[j])
        lo = float(norm["low"].iloc[j]) if "low" in norm.columns else px
        hi = float(norm["high"].iloc[j]) if "high" in norm.columns else px
        highest = max(highest, hi, px)
        bars_held = j - idx
        # Profit: close or high pierces target
        if max(px, hi) >= entry * (1.0 + profit_pct):
            hit = BARRIER_PROFIT
            exit_idx = j
            break
        if bars_held < grace:
            stop_level = entry * (1.0 - stop_pct_v)
        else:
            stop_level = highest * (1.0 - stop_pct_v)
        if min(px, lo) <= stop_level:
            hit = BARRIER_STOP
            exit_idx = j
            break
    exit_px = float(norm["close"].iloc[exit_idx])
    holding = int(exit_idx - idx)
    ret_net = (exit_px - entry) / entry if entry else 0.0
    if hit == BARRIER_PROFIT:
        label = 1
    elif hit == BARRIER_STOP:
        label = -1
    else:
        label = 0
    return {
        "barrier_label": int(label),
        "barrier_hit": hit,
        "barrier_holding_days": holding,
        "barrier_ret_net": float(ret_net),
        "label": int(label),
        "barrier_hit_alias": hit,
        "holding_days": holding,
        "ret_net": float(ret_net),
        "stop_pct": stop_pct_v,
        "profit_pct": profit_pct,
        "max_hold_days": max_hold,
        "t1_date": str(pd.Timestamp(norm.index[exit_idx]).date()),
        "entry_date": str(pd.Timestamp(norm.index[idx]).date()),
    }


def attach_triple_barrier_labels(
    feature_row: dict[str, Any],
    bars: pd.DataFrame,
    *,
    skill_dir: Any = None,
    stop_pct: float | None = None,
) -> dict[str, Any] | None:
    """Join triple-barrier labels onto one feature row using full bar history."""
    norm = normalize_ohlcv(bars)
    asof = pd.Timestamp(feature_row["asof_date"]).normalize()
    eligible = norm.loc[norm.index <= asof]
    if eligible.empty:
        return None
    asof_ts = eligible.index[-1]
    loc = norm.index.get_loc(asof_ts)
    if isinstance(loc, slice):
        idx = loc.stop - 1
    elif isinstance(loc, (list, tuple)):
        idx = int(loc[-1])
    else:
        idx = int(loc)
    labels = triple_barrier_at_index(norm, idx, skill_dir=skill_dir, stop_pct=stop_pct)
    if labels is None:
        return None
    out = dict(feature_row)
    out.update(labels)
    return out


def label_ohlc_path(
    entry_price: float,
    ohlc: list[dict[str, Any]],
    *,
    stop_pct: float,
    profit_pct: float,
    max_hold_days: int,
    min_hold_before_trail: int,
) -> dict[str, Any]:
    """Label a frozen trade OHLC path (hand-checkable fixture helper)."""
    if entry_price <= 0 or not ohlc:
        return {
            "barrier_label": 0,
            "barrier_hit": BARRIER_TIMEOUT,
            "barrier_holding_days": 0,
            "barrier_ret_net": 0.0,
            "label": 0,
            "holding_days": 0,
            "ret_net": 0.0,
        }
    highest = float(entry_price)
    hit = BARRIER_TIMEOUT
    exit_px = float(ohlc[min(len(ohlc), max_hold_days) - 1].get("close") or entry_price)
    holding = min(len(ohlc), max_hold_days)
    n = min(len(ohlc), max_hold_days)
    for i in range(n):
        row = ohlc[i]
        px = float(row.get("close") or entry_price)
        lo = float(row.get("low") if row.get("low") is not None else px)
        hi = float(row.get("high") if row.get("high") is not None else px)
        highest = max(highest, hi, px)
        bars_held = i + 1
        if max(px, hi) >= entry_price * (1.0 + profit_pct):
            hit = BARRIER_PROFIT
            exit_px = px
            holding = bars_held
            break
        if bars_held < min_hold_before_trail:
            stop_level = entry_price * (1.0 - stop_pct)
        else:
            stop_level = highest * (1.0 - stop_pct)
        if min(px, lo) <= stop_level:
            hit = BARRIER_STOP
            exit_px = px
            holding = bars_held
            break
        exit_px = px
        holding = bars_held
    ret_net = (exit_px - entry_price) / entry_price
    if hit == BARRIER_PROFIT:
        label = 1
    elif hit == BARRIER_STOP:
        label = -1
    else:
        label = 0
    return {
        "barrier_label": int(label),
        "barrier_hit": hit,
        "barrier_holding_days": int(holding),
        "barrier_ret_net": float(ret_net),
        "label": int(label),
        "holding_days": int(holding),
        "ret_net": float(ret_net),
    }
