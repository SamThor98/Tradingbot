"""Sound, bar-based strategy evaluators for Scan studio timeframes.

Live execution stays daily Stage 2 + VCP (`trend_breakout`). These sleeves
are shadow/research only. Weekly and monthly rules resample the same daily
OHLCV the scanner already fetched — not a separate vendor bar engine.
Intraday sleeves use session structure on the latest daily bar (gap, range,
close location). They are not a minute-bar book.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# Catalog ids that may dual-admit at Stage A when selected. Live breakout,
# PEAD, and the quote overlay keep their own admit paths.
DUAL_ADMIT_STRATEGY_IDS: frozenset[str] = frozenset(
    {
        "pullback",
        "donchian_20",
        "nr7_breakout",
        "gap_and_go",
        "range_expansion",
        "weekly_swing",
        "weekly_vcp",
        "weekly_breakout",
        "monthly_position",
        "monthly_52w_high",
        "monthly_pullback",
    }
)

# Extra Stage B slots for horizon-only admits so they do not steal Stage 2
# shortlist capacity.
HORIZON_STAGE_B_CAP = 40

_OHLCV = ("open", "high", "low", "close", "volume")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:  # NaN
        return default
    return out


def _clip_score(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def _plugin(*, name: str, triggered: bool, raw_score: float, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "mode": "shadow",
        "raw_score": _clip_score(raw_score),
        "triggered": bool(triggered),
        "meta": meta,
    }


def ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a sorted DatetimeIndex, dropping unparseable rows."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    return out


def _require_ohlcv(df: pd.DataFrame, *, min_rows: int) -> pd.DataFrame | None:
    if df is None or not isinstance(df, pd.DataFrame) or len(df) < min_rows:
        return None
    for col in _OHLCV:
        if col not in df.columns:
            return None
    return ensure_datetime_index(df)


def _month_rule() -> str:
    try:
        pd.tseries.frequencies.to_offset("ME")
        return "ME"
    except (ValueError, KeyError):
        return "M"


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample daily OHLCV to weekly (`W-FRI`) or month-end bars."""
    frame = _require_ohlcv(df, min_rows=2)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(_OHLCV))
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = frame[list(_OHLCV)].resample(rule).agg(agg)
    return out.dropna(how="any")


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    return resample_ohlcv(df, "W-FRI")


def resample_monthly(df: pd.DataFrame) -> pd.DataFrame:
    return resample_ohlcv(df, _month_rule())


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.astype(float).rolling(window, min_periods=window).mean()


def _sma_rising(series: pd.Series, *, lookback: int) -> bool:
    clean = series.dropna()
    if len(clean) < lookback + 1:
        return False
    last = clean.iloc[-(lookback + 1) :]
    return float(last.iloc[-1]) > float(last.iloc[0])


def _close_in_range_pct(open_: float, high: float, low: float, close: float) -> float | None:
    span = high - low
    if span <= 0:
        return None
    return (close - low) / span


def _true_range(df: pd.DataFrame) -> pd.Series:
    hi = df["high"].astype(float)
    lo = df["low"].astype(float)
    cl = df["close"].astype(float)
    prev = cl.shift(1)
    return pd.concat([hi - lo, (hi - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)


def _high_52w(df: pd.DataFrame) -> float:
    lookback = min(252, len(df))
    return float(df["high"].astype(float).iloc[-lookback:].max())


def selected_dual_admit_ids(strategy_ids: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in strategy_ids or []:
        key = str(raw or "").strip().lower()
        if key in DUAL_ADMIT_STRATEGY_IDS and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def evaluate_gap_and_go(df: pd.DataFrame) -> dict[str, Any]:
    """Daily-bar gap-and-go: gap up, hold above the open, volume expansion.

    Classic gap-and-go wants an opening gap that does not fill, with the
    session closing strong. Without a minute book we use the latest daily bar
    vs the prior close.
    """
    frame = _require_ohlcv(df, min_rows=3)
    if frame is None:
        return _plugin(name="gap_and_go", triggered=False, raw_score=0.0, meta={"reason": "insufficient_bars"})
    last = frame.iloc[-1]
    prior = frame.iloc[-2]
    open_ = _safe_float(last["open"])
    high = _safe_float(last["high"])
    low = _safe_float(last["low"])
    close = _safe_float(last["close"])
    vol = _safe_float(last["volume"])
    prior_close = _safe_float(prior["close"])
    if prior_close <= 0 or open_ <= 0 or close <= 0:
        return _plugin(name="gap_and_go", triggered=False, raw_score=0.0, meta={"reason": "invalid_prices"})

    gap_pct = (open_ - prior_close) / prior_close
    loc = _close_in_range_pct(open_, high, low, close)
    avg_vol = _safe_float(frame["volume"].astype(float).iloc[-50:].mean() if len(frame) >= 20 else 0.0)
    sma200 = _safe_float(_sma(frame["close"], 200).iloc[-1]) if len(frame) >= 200 else 0.0
    vol_ok = avg_vol > 0 and vol >= avg_vol
    trend_ok = sma200 <= 0 or close > sma200
    held = close >= open_ and low >= prior_close * 0.995
    location_ok = loc is not None and loc >= 0.5
    gap_ok = gap_pct >= 0.01
    triggered = bool(gap_ok and held and location_ok and vol_ok and trend_ok)

    score = 0.0
    if gap_ok:
        score += min(25.0, gap_pct * 1000.0)
    if held:
        score += 20.0
    if location_ok and loc is not None:
        score += loc * 25.0
    if vol_ok:
        score += 15.0
    if trend_ok:
        score += 15.0
    return _plugin(
        name="gap_and_go",
        triggered=triggered,
        raw_score=score,
        meta={
            "gap_pct": round(gap_pct * 100.0, 2),
            "close_in_range": round(loc, 3) if loc is not None else None,
            "held_gap": held,
            "volume_expansion": vol_ok,
            "above_sma200": trend_ok,
        },
    )


def evaluate_range_expansion(df: pd.DataFrame) -> dict[str, Any]:
    """Opening-drive / range-expansion proxy on the latest daily bar.

    Close through the prior high, today's true range large vs ATR, close in
    the top quartile of the session. Trend filter: close above the 50-day SMA.
    """
    frame = _require_ohlcv(df, min_rows=20)
    if frame is None:
        return _plugin(name="range_expansion", triggered=False, raw_score=0.0, meta={"reason": "insufficient_bars"})
    last = frame.iloc[-1]
    prior_high = _safe_float(frame["high"].iloc[-2])
    open_ = _safe_float(last["open"])
    high = _safe_float(last["high"])
    low = _safe_float(last["low"])
    close = _safe_float(last["close"])
    tr = _true_range(frame)
    atr = _safe_float(tr.rolling(14, min_periods=8).mean().iloc[-1])
    sma50 = _safe_float(_sma(frame["close"], 50).iloc[-1]) if len(frame) >= 50 else 0.0
    loc = _close_in_range_pct(open_, high, low, close)
    today_tr = _safe_float(tr.iloc[-1])
    expansion = atr > 0 and today_tr >= 1.25 * atr
    broke_high = close > prior_high
    location_ok = loc is not None and loc >= 0.75
    trend_ok = sma50 <= 0 or close > sma50
    triggered = bool(expansion and broke_high and location_ok and trend_ok)

    score = 0.0
    if expansion and atr > 0:
        score += min(30.0, (today_tr / atr) * 15.0)
    if broke_high:
        score += 25.0
    if location_ok and loc is not None:
        score += loc * 25.0
    if trend_ok:
        score += 20.0
    return _plugin(
        name="range_expansion",
        triggered=triggered,
        raw_score=score,
        meta={
            "true_range": round(today_tr, 4),
            "atr_14": round(atr, 4),
            "close_above_prior_high": broke_high,
            "close_in_range": round(loc, 3) if loc is not None else None,
            "above_sma50": trend_ok,
        },
    )


def evaluate_donchian_20(df: pd.DataFrame) -> dict[str, Any]:
    """20-day Donchian/Turtle breakout with a 200-day trend filter."""
    frame = _require_ohlcv(df, min_rows=25)
    if frame is None:
        return _plugin(name="donchian_20", triggered=False, raw_score=0.0, meta={"reason": "insufficient_bars"})
    close = _safe_float(frame["close"].iloc[-1])
    channel = frame["high"].astype(float).shift(1).rolling(20, min_periods=20).max()
    donchian_high = _safe_float(channel.iloc[-1])
    sma200 = _safe_float(_sma(frame["close"], 200).iloc[-1]) if len(frame) >= 200 else 0.0
    broke = donchian_high > 0 and close > donchian_high
    trend_ok = sma200 <= 0 or close > sma200
    triggered = bool(broke and trend_ok)
    extension = ((close / donchian_high) - 1.0) * 100.0 if donchian_high > 0 else 0.0
    score = 0.0
    if broke:
        score += 55.0 + min(20.0, max(0.0, extension) * 10.0)
    if trend_ok:
        score += 25.0
    return _plugin(
        name="donchian_20",
        triggered=triggered,
        raw_score=score,
        meta={
            "donchian_high": round(donchian_high, 4),
            "close": round(close, 4),
            "above_sma200": trend_ok,
            "breakout_pct": round(extension, 3),
        },
    )


def evaluate_nr7_breakout(df: pd.DataFrame) -> dict[str, Any]:
    """Mark Fisher NR7: yesterday was the narrowest of 7 days, today breaks it."""
    frame = _require_ohlcv(df, min_rows=10)
    if frame is None:
        return _plugin(name="nr7_breakout", triggered=False, raw_score=0.0, meta={"reason": "insufficient_bars"})
    ranges = frame["high"].astype(float) - frame["low"].astype(float)
    # Yesterday vs the 7 bars ending yesterday (exclude today).
    window = ranges.iloc[-8:-1]
    if len(window) < 7:
        return _plugin(name="nr7_breakout", triggered=False, raw_score=0.0, meta={"reason": "insufficient_nr7_window"})
    yesterday_range = _safe_float(window.iloc[-1])
    nr7 = yesterday_range > 0 and yesterday_range <= float(window.min()) + 1e-12
    yesterday_high = _safe_float(frame["high"].iloc[-2])
    close = _safe_float(frame["close"].iloc[-1])
    sma50 = _safe_float(_sma(frame["close"], 50).iloc[-1]) if len(frame) >= 50 else 0.0
    broke = close > yesterday_high
    trend_ok = sma50 <= 0 or close > sma50
    triggered = bool(nr7 and broke and trend_ok)
    score = 0.0
    if nr7:
        score += 40.0
    if broke:
        score += 35.0
    if trend_ok:
        score += 25.0
    return _plugin(
        name="nr7_breakout",
        triggered=triggered,
        raw_score=score,
        meta={
            "yesterday_was_nr7": nr7,
            "yesterday_range": round(yesterday_range, 4),
            "close_above_nr7_high": broke,
            "above_sma50": trend_ok,
        },
    )


def evaluate_weekly_swing(df: pd.DataFrame) -> dict[str, Any]:
    """Weinstein-style weekly Stage 2: close > 10w SMA > 30w SMA, 30w rising, near highs."""
    weekly = resample_weekly(df)
    if len(weekly) < 36:
        return _plugin(name="weekly_swing", triggered=False, raw_score=0.0, meta={"reason": "insufficient_weekly_bars"})
    close = weekly["close"].astype(float)
    sma10 = _sma(close, 10)
    sma30 = _sma(close, 30)
    last_close = _safe_float(close.iloc[-1])
    last_10 = _safe_float(sma10.iloc[-1])
    last_30 = _safe_float(sma30.iloc[-1])
    stacked = last_close > last_10 > last_30 > 0
    rising = _sma_rising(sma30, lookback=8)
    high_52w = _high_52w(df if len(df) >= 2 else weekly)
    near_high = high_52w > 0 and last_close >= 0.85 * high_52w
    triggered = bool(stacked and rising and near_high)
    score = 0.0
    if stacked:
        score += 40.0
    if rising:
        score += 25.0
    if near_high:
        score += 25.0
    if last_30 > 0:
        score += min(10.0, ((last_close / last_30) - 1.0) * 50.0)
    return _plugin(
        name="weekly_swing",
        triggered=triggered,
        raw_score=score,
        meta={
            "weekly_close": round(last_close, 4),
            "sma_10w": round(last_10, 4),
            "sma_30w": round(last_30, 4),
            "sma_30w_rising": rising,
            "near_52w_high": near_high,
            "weekly_bars": int(len(weekly)),
        },
    )


def evaluate_weekly_vcp(df: pd.DataFrame) -> dict[str, Any]:
    """Multi-week volume contraction: last 5 weekly bars below the 10-week avg volume."""
    weekly = resample_weekly(df)
    if len(weekly) < 16:
        return _plugin(name="weekly_vcp", triggered=False, raw_score=0.0, meta={"reason": "insufficient_weekly_bars"})
    vol = weekly["volume"].astype(float)
    avg10 = vol.rolling(10, min_periods=10).mean()
    last5 = weekly.iloc[-5:]
    avg = _safe_float(avg10.iloc[-1])
    if avg <= 0:
        return _plugin(name="weekly_vcp", triggered=False, raw_score=0.0, meta={"reason": "no_avg_volume"})
    dry = all(_safe_float(v) < avg for v in last5["volume"].tolist())
    close = _safe_float(weekly["close"].iloc[-1])
    sma30 = _safe_float(_sma(weekly["close"], 30).iloc[-1]) if len(weekly) >= 30 else 0.0
    trend_ok = sma30 <= 0 or close > sma30
    triggered = bool(dry and trend_ok)
    ratio = float(last5["volume"].mean() / avg) if avg else 1.0
    score = 0.0
    if dry:
        score += 50.0 + max(0.0, (1.0 - ratio) * 30.0)
    if trend_ok:
        score += 20.0
    return _plugin(
        name="weekly_vcp",
        triggered=triggered,
        raw_score=score,
        meta={
            "dry_weeks": 5 if dry else int(sum(1 for v in last5["volume"].tolist() if _safe_float(v) < avg)),
            "vol_vs_avg10": round(ratio, 3),
            "above_sma30w": trend_ok,
        },
    )


def evaluate_weekly_breakout(df: pd.DataFrame) -> dict[str, Any]:
    """Weekly close through the prior week's high while above the 30-week SMA."""
    weekly = resample_weekly(df)
    if len(weekly) < 32:
        return _plugin(
            name="weekly_breakout", triggered=False, raw_score=0.0, meta={"reason": "insufficient_weekly_bars"}
        )
    close = _safe_float(weekly["close"].iloc[-1])
    prior_high = _safe_float(weekly["high"].iloc[-2])
    sma30 = _safe_float(_sma(weekly["close"], 30).iloc[-1])
    broke = prior_high > 0 and close > prior_high
    trend_ok = sma30 > 0 and close > sma30
    triggered = bool(broke and trend_ok)
    score = 0.0
    if broke:
        score += 55.0
    if trend_ok:
        score += 30.0
    if prior_high > 0:
        score += min(15.0, ((close / prior_high) - 1.0) * 400.0)
    return _plugin(
        name="weekly_breakout",
        triggered=triggered,
        raw_score=score,
        meta={
            "weekly_close": round(close, 4),
            "prior_week_high": round(prior_high, 4),
            "above_sma30w": trend_ok,
        },
    )


def evaluate_monthly_position(df: pd.DataFrame) -> dict[str, Any]:
    """Meb Faber 10-month SMA timing: monthly close above a rising 10-month SMA."""
    monthly = resample_monthly(df)
    if len(monthly) < 12:
        return _plugin(
            name="monthly_position", triggered=False, raw_score=0.0, meta={"reason": "insufficient_monthly_bars"}
        )
    close = monthly["close"].astype(float)
    sma10 = _sma(close, 10)
    last_close = _safe_float(close.iloc[-1])
    last_sma = _safe_float(sma10.iloc[-1])
    above = last_sma > 0 and last_close > last_sma
    rising = _sma_rising(sma10, lookback=3)
    triggered = bool(above and rising)
    score = 0.0
    if above:
        score += 55.0
        score += min(20.0, ((last_close / last_sma) - 1.0) * 200.0)
    if rising:
        score += 25.0
    return _plugin(
        name="monthly_position",
        triggered=triggered,
        raw_score=score,
        meta={
            "monthly_close": round(last_close, 4),
            "sma_10m": round(last_sma, 4),
            "sma_10m_rising": rising,
            "monthly_bars": int(len(monthly)),
        },
    )


def evaluate_monthly_52w_high(df: pd.DataFrame) -> dict[str, Any]:
    """Position-style strength: monthly close near the 52-week high and above the 10-month SMA."""
    monthly = resample_monthly(df)
    if len(monthly) < 12:
        return _plugin(
            name="monthly_52w_high", triggered=False, raw_score=0.0, meta={"reason": "insufficient_monthly_bars"}
        )
    last_close = _safe_float(monthly["close"].iloc[-1])
    sma10 = _safe_float(_sma(monthly["close"], 10).iloc[-1])
    high_52w = _high_52w(df)
    near = high_52w > 0 and last_close >= 0.95 * high_52w
    above = sma10 > 0 and last_close > sma10
    triggered = bool(near and above)
    pct_of_high = (last_close / high_52w) if high_52w > 0 else 0.0
    score = 0.0
    if near:
        score += 50.0 + min(20.0, max(0.0, pct_of_high - 0.95) * 400.0)
    if above:
        score += 30.0
    return _plugin(
        name="monthly_52w_high",
        triggered=triggered,
        raw_score=score,
        meta={
            "monthly_close": round(last_close, 4),
            "high_52w": round(high_52w, 4),
            "pct_of_52w_high": round(pct_of_high * 100.0, 2),
            "above_sma10m": above,
        },
    )


def evaluate_monthly_pullback(df: pd.DataFrame) -> dict[str, Any]:
    """Uptrend pullback toward the 10-month SMA: still above, this month tagged it."""
    monthly = resample_monthly(df)
    if len(monthly) < 12:
        return _plugin(
            name="monthly_pullback", triggered=False, raw_score=0.0, meta={"reason": "insufficient_monthly_bars"}
        )
    last = monthly.iloc[-1]
    close = _safe_float(last["close"])
    low = _safe_float(last["low"])
    sma10 = _safe_float(_sma(monthly["close"], 10).iloc[-1])
    rising = _sma_rising(_sma(monthly["close"], 10), lookback=3)
    if sma10 <= 0 or close <= 0:
        return _plugin(name="monthly_pullback", triggered=False, raw_score=0.0, meta={"reason": "invalid_sma"})
    dist = (close - sma10) / sma10
    tagged = low <= sma10 * 1.02
    still_above = close > sma10
    zone = 0.0 <= dist <= 0.08
    triggered = bool(rising and still_above and tagged and zone)
    score = 0.0
    if rising:
        score += 30.0
    if still_above:
        score += 25.0
    if tagged:
        score += 25.0
    if zone:
        score += max(0.0, 20.0 - abs(dist) * 200.0)
    return _plugin(
        name="monthly_pullback",
        triggered=triggered,
        raw_score=score,
        meta={
            "dist_to_sma10m_pct": round(dist * 100.0, 2),
            "tagged_sma10m": tagged,
            "sma_10m_rising": rising,
        },
    )


_EVALUATORS: dict[str, Any] = {
    "gap_and_go": evaluate_gap_and_go,
    "range_expansion": evaluate_range_expansion,
    "donchian_20": evaluate_donchian_20,
    "nr7_breakout": evaluate_nr7_breakout,
    "weekly_swing": evaluate_weekly_swing,
    "weekly_vcp": evaluate_weekly_vcp,
    "weekly_breakout": evaluate_weekly_breakout,
    "monthly_position": evaluate_monthly_position,
    "monthly_52w_high": evaluate_monthly_52w_high,
    "monthly_pullback": evaluate_monthly_pullback,
}


def evaluate_horizon_plugins(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Evaluate every horizon sleeve against daily OHLCV. Always shadow mode."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return [
            _plugin(name=name, triggered=False, raw_score=0.0, meta={"reason": "missing_df"}) for name in _EVALUATORS
        ]
    return [fn(df) for fn in _EVALUATORS.values()]


def triggered_dual_admit_ids(
    df: pd.DataFrame | None,
    strategy_ids: list[str] | None,
    *,
    pullback_triggered: bool = False,
) -> list[str]:
    """Return selected dual-admit ids that fired on this name."""
    wanted = set(selected_dual_admit_ids(strategy_ids))
    if not wanted:
        return []
    hits: list[str] = []
    if "pullback" in wanted and pullback_triggered:
        hits.append("pullback")
    if df is None:
        return hits
    for plugin in evaluate_horizon_plugins(df):
        name = str(plugin.get("name") or "")
        if name in wanted and plugin.get("triggered"):
            hits.append(name)
    return hits
