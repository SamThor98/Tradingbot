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
        "opening_range_breakout",
        "st_reversal_5d",
        "overnight_gap_fade",
        "weekly_reversal",
        "momentum_12_1",
        "tsmom_12m",
    }
)

# Extra Stage B slots for horizon-only admits so they do not steal Stage 2
# shortlist capacity.
HORIZON_STAGE_B_CAP = 40

# Matches config.get_pretrade_min_dollar_volume default. Share-count floors
# are not liquidity.
DEFAULT_MIN_ADV_USD = 2_000_000.0

# Literature sleeves that are closer to a cross-section than an absolute rule.
# After Stage A, keep the top N of that sleeve's hits (not a WML book).
CROSS_SECTION_RANK: dict[str, str] = {
    "momentum_12_1": "formation_return_pct",
    "st_reversal_5d": "return_5d_pct",
}
CROSS_SECTION_KEEP = 10
CROSS_SECTION_ASCENDING = frozenset({"st_reversal_5d"})

# Regular-session close (America/New_York). Research sleeves do not use a
# same-day bar until this clock.
_SESSION_CLOSE_HOUR = 16
_ET = "America/New_York"

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


def _eastern_now(now: Any | None = None) -> pd.Timestamp:
    clock = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz=_ET)
    if clock.tzinfo is None:
        return clock.tz_localize(_ET)
    return clock.tz_convert(_ET)


def _as_et_day(value: Any) -> pd.Timestamp | None:
    ts = pd.Timestamp(value)
    if ts is pd.NaT or ts != ts:
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(_ET).tz_localize(None)
    return ts.normalize()


def session_is_closed(*, now: Any | None = None) -> bool:
    clock = _eastern_now(now)
    return int(clock.hour) > _SESSION_CLOSE_HOUR or (
        int(clock.hour) == _SESSION_CLOSE_HOUR and int(clock.minute) >= 0
    )


def completed_daily_bars(df: pd.DataFrame, *, now: Any | None = None) -> pd.DataFrame:
    """Drop the forming regular-session bar.

    If the last bar's Eastern date is today and the session has not closed,
    research sleeves evaluate as of the prior close. After the close, today's
    bar is a completed session and is kept.
    """
    frame = ensure_datetime_index(df)
    if frame.empty:
        return frame
    last_day = _as_et_day(frame.index[-1])
    today = _as_et_day(_eastern_now(now))
    if last_day is None or today is None:
        return frame
    if last_day == today and not session_is_closed(now=now):
        return frame.iloc[:-1]
    return frame


def _completed_ohlcv(
    df: pd.DataFrame | None,
    *,
    min_rows: int,
    now: Any | None = None,
) -> pd.DataFrame | None:
    frame = _require_ohlcv(df, min_rows=2)
    if frame is None:
        return None
    done = completed_daily_bars(frame, now=now)
    if len(done) < min_rows:
        return None
    return done


def drop_incomplete_last_period(resampled: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Drop the last weekly/monthly bar when the daily history has not reached the period label."""
    if resampled is None or resampled.empty or daily is None or daily.empty:
        return resampled if isinstance(resampled, pd.DataFrame) else pd.DataFrame(columns=list(_OHLCV))
    last_daily = _as_et_day(daily.index[-1])
    last_period = _as_et_day(resampled.index[-1])
    if last_daily is None or last_period is None:
        return resampled
    if last_daily < last_period:
        return resampled.iloc[:-1]
    return resampled


def avg_dollar_volume(df: pd.DataFrame, *, window: int = 50) -> float:
    if df is None or df.empty or "close" not in df.columns or "volume" not in df.columns:
        return 0.0
    dollar = df["close"].astype(float) * df["volume"].astype(float)
    slice_ = dollar.iloc[-window:] if len(dollar) else dollar
    if slice_.empty:
        return 0.0
    return _safe_float(slice_.mean())


def liquid_enough(df: pd.DataFrame, *, min_adv_usd: float = DEFAULT_MIN_ADV_USD) -> bool:
    return avg_dollar_volume(df) >= float(min_adv_usd)


def above_sma(close: float, sma: float) -> bool:
    """Fail-closed trend filter: missing SMA does not pass."""
    return sma > 0.0 and close > sma


def research_score_from_plugins(plugins: list[dict[str, Any]] | None) -> float:
    best = 0.0
    for plugin in plugins or []:
        if not isinstance(plugin, dict) or not plugin.get("triggered"):
            continue
        name = str(plugin.get("name") or "").strip().lower()
        if name in {"", "trend_breakout"}:
            continue
        best = max(best, _safe_float(plugin.get("raw_score"), 0.0))
    return _clip_score(best)


def is_horizon_research_signal(signal: dict[str, Any] | None) -> bool:
    if not isinstance(signal, dict):
        return False
    return str(signal.get("entry_family") or "").strip().lower() == "horizon"


def partition_live_and_research(
    signals: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split Stage 2 / both names from horizon-only research admits."""
    live: list[dict[str, Any]] = []
    research: list[dict[str, Any]] = []
    for row in signals or []:
        if not isinstance(row, dict):
            continue
        if is_horizon_research_signal(row):
            if row.get("research_score") is None:
                row["research_score"] = research_score_from_plugins(
                    row.get("strategy_plugins") if isinstance(row.get("strategy_plugins"), list) else []
                )
            row["rank_basis"] = "research_score"
            research.append(row)
        else:
            live.append(row)
    return live, research


def _candidate_hit_names(cand: dict[str, Any]) -> set[str]:
    names = {str(x) for x in (cand.get("horizon_hits") or []) if str(x)}
    hits = cand.get("horizon_plugin_hits") if isinstance(cand.get("horizon_plugin_hits"), list) else []
    for hit in hits:
        if isinstance(hit, dict):
            name = str(hit.get("name") or "")
            if name:
                names.add(name)
    return names


def _candidate_hit_meta(cand: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    hits = cand.get("horizon_plugin_hits") if isinstance(cand.get("horizon_plugin_hits"), list) else []
    for hit in hits:
        if isinstance(hit, dict) and str(hit.get("name") or "") == strategy_id:
            meta = hit.get("meta")
            return meta if isinstance(meta, dict) else {}
    return {}


def cap_cross_section_horizon(
    candidates: list[dict[str, Any]],
    *,
    keep: int = CROSS_SECTION_KEEP,
) -> list[dict[str, Any]]:
    """Keep the top-N hits per literature cross-section sleeve.

    Absolute thresholds still fire at Stage A (cheap filter). This ranks those
    hits inside the current scan — not CRSP winner-minus-loser.
    """
    if not candidates or keep <= 0:
        return list(candidates or [])
    drop: set[str] = set()
    for sid, meta_key in CROSS_SECTION_RANK.items():
        scored: list[tuple[float, str, set[str]]] = []
        for cand in candidates:
            names = _candidate_hit_names(cand)
            if sid not in names:
                continue
            ticker = str(cand.get("ticker") or "")
            meta = _candidate_hit_meta(cand, sid)
            scored.append((_safe_float(meta.get(meta_key), 0.0), ticker, names))
        if len(scored) <= keep:
            continue
        reverse = sid not in CROSS_SECTION_ASCENDING
        scored.sort(key=lambda row: (row[0], row[1]), reverse=reverse)
        for _score, ticker, names in scored[keep:]:
            if ticker and names <= {sid}:
                drop.add(ticker)
    if not drop:
        return list(candidates)
    return [c for c in candidates if str(c.get("ticker") or "") not in drop]


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


def resample_weekly(df: pd.DataFrame, *, now: Any | None = None) -> pd.DataFrame:
    daily = _completed_ohlcv(df, min_rows=2, now=now)
    if daily is None or daily.empty:
        return pd.DataFrame(columns=list(_OHLCV))
    weekly = resample_ohlcv(daily, "W-FRI")
    return drop_incomplete_last_period(weekly, daily)


def resample_monthly(df: pd.DataFrame, *, now: Any | None = None) -> pd.DataFrame:
    daily = _completed_ohlcv(df, min_rows=2, now=now)
    if daily is None or daily.empty:
        return pd.DataFrame(columns=list(_OHLCV))
    monthly = resample_ohlcv(daily, _month_rule())
    return drop_incomplete_last_period(monthly, daily)


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


def selected_dual_admit_ids(strategy_ids: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in strategy_ids or []:
        key = str(raw or "").strip().lower()
        if key in DUAL_ADMIT_STRATEGY_IDS and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def evaluate_gap_and_go(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Daily-bar gap-and-go: gap up, hold above the open, volume expansion.

    Classic gap-and-go wants an opening gap that does not fill, with the
    session closing strong. Without a minute book we use the last *completed*
    daily bar vs the prior close. Daily low still cannot prove an intraday fill.
    """
    frame = _completed_ohlcv(df, min_rows=3, now=now)
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
    liquid = liquid_enough(frame)
    trend_ok = above_sma(close, sma200)
    held = close >= open_ and low >= prior_close * 0.995
    location_ok = loc is not None and loc >= 0.5
    gap_ok = gap_pct >= 0.01
    triggered = bool(gap_ok and held and location_ok and vol_ok and liquid and trend_ok)

    score = 0.0
    if gap_ok:
        score += min(25.0, gap_pct * 1000.0)
    if held:
        score += 20.0
    if location_ok and loc is not None:
        score += loc * 25.0
    if vol_ok and liquid:
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
            "adv_usd": round(avg_dollar_volume(frame), 0),
            "liquid": liquid,
            "above_sma200": trend_ok,
            "decision_bar": "completed_daily",
        },
    )


def evaluate_range_expansion(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Opening-drive / range-expansion proxy on the last completed daily bar.

    Close through the prior high, today's true range large vs ATR, close in
    the top quartile of the session. Trend filter: close above the 50-day SMA.
    """
    frame = _completed_ohlcv(df, min_rows=50, now=now)
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
    sma50 = _safe_float(_sma(frame["close"], 50).iloc[-1])
    loc = _close_in_range_pct(open_, high, low, close)
    today_tr = _safe_float(tr.iloc[-1])
    expansion = atr > 0 and today_tr >= 1.25 * atr
    broke_high = close > prior_high
    location_ok = loc is not None and loc >= 0.75
    trend_ok = above_sma(close, sma50)
    liquid = liquid_enough(frame)
    triggered = bool(expansion and broke_high and location_ok and trend_ok and liquid)

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
            "liquid": liquid,
            "decision_bar": "completed_daily",
        },
    )


def evaluate_donchian_20(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """20-day Donchian/Turtle breakout with a 200-day trend filter."""
    frame = _completed_ohlcv(df, min_rows=220, now=now)
    if frame is None:
        return _plugin(name="donchian_20", triggered=False, raw_score=0.0, meta={"reason": "insufficient_bars"})
    close = _safe_float(frame["close"].iloc[-1])
    channel = frame["high"].astype(float).shift(1).rolling(20, min_periods=20).max()
    donchian_high = _safe_float(channel.iloc[-1])
    sma200 = _safe_float(_sma(frame["close"], 200).iloc[-1])
    broke = donchian_high > 0 and close > donchian_high
    trend_ok = above_sma(close, sma200)
    liquid = liquid_enough(frame)
    triggered = bool(broke and trend_ok and liquid)
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
            "liquid": liquid,
            "decision_bar": "completed_daily",
        },
    )


def evaluate_nr7_breakout(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Mark Fisher NR7: yesterday was the narrowest of 7 days, today breaks it."""
    frame = _completed_ohlcv(df, min_rows=50, now=now)
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
    sma50 = _safe_float(_sma(frame["close"], 50).iloc[-1])
    broke = close > yesterday_high
    trend_ok = above_sma(close, sma50)
    liquid = liquid_enough(frame)
    triggered = bool(nr7 and broke and trend_ok and liquid)
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
            "liquid": liquid,
            "decision_bar": "completed_daily",
        },
    )


def evaluate_weekly_swing(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Weinstein-style weekly Stage 2: close > 10w SMA > 30w SMA, 30w rising, near highs."""
    weekly = resample_weekly(df, now=now)
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
    lookback = min(52, len(weekly))
    high_52w = _safe_float(weekly["high"].astype(float).iloc[-lookback:].max())
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
            "decision_bar": "completed_week",
        },
    )


def evaluate_weekly_vcp(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Weekly volume dryness: last 5 weekly bars below the 10-week avg volume.

    This is not Minervini VCP (contracting ranges / successive tightness).
    """
    weekly = resample_weekly(df, now=now)
    if len(weekly) < 30:
        return _plugin(name="weekly_vcp", triggered=False, raw_score=0.0, meta={"reason": "insufficient_weekly_bars"})
    vol = weekly["volume"].astype(float)
    avg10 = vol.rolling(10, min_periods=10).mean()
    last5 = weekly.iloc[-5:]
    avg = _safe_float(avg10.iloc[-1])
    if avg <= 0:
        return _plugin(name="weekly_vcp", triggered=False, raw_score=0.0, meta={"reason": "no_avg_volume"})
    dry = all(_safe_float(v) < avg for v in last5["volume"].tolist())
    close = _safe_float(weekly["close"].iloc[-1])
    sma30 = _safe_float(_sma(weekly["close"], 30).iloc[-1])
    trend_ok = above_sma(close, sma30)
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
            "decision_bar": "completed_week",
        },
    )


def evaluate_weekly_breakout(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Weekly close through the prior week's high while above the 30-week SMA."""
    weekly = resample_weekly(df, now=now)
    if len(weekly) < 32:
        return _plugin(
            name="weekly_breakout", triggered=False, raw_score=0.0, meta={"reason": "insufficient_weekly_bars"}
        )
    close = _safe_float(weekly["close"].iloc[-1])
    prior_high = _safe_float(weekly["high"].iloc[-2])
    sma30 = _safe_float(_sma(weekly["close"], 30).iloc[-1])
    broke = prior_high > 0 and close > prior_high
    trend_ok = above_sma(close, sma30)
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
            "decision_bar": "completed_week",
        },
    )


def evaluate_monthly_position(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Meb Faber 10-month SMA timing: monthly close above a rising 10-month SMA."""
    monthly = resample_monthly(df, now=now)
    if len(monthly) < 12:
        return _plugin(
            name="monthly_position", triggered=False, raw_score=0.0, meta={"reason": "insufficient_monthly_bars"}
        )
    close = monthly["close"].astype(float)
    sma10 = _sma(close, 10)
    last_close = _safe_float(close.iloc[-1])
    last_sma = _safe_float(sma10.iloc[-1])
    above = above_sma(last_close, last_sma)
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
            "decision_bar": "completed_month",
        },
    )


def evaluate_monthly_52w_high(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Position-style strength: monthly close near the 52-week high and above the 10-month SMA."""
    monthly = resample_monthly(df, now=now)
    if len(monthly) < 12:
        return _plugin(
            name="monthly_52w_high", triggered=False, raw_score=0.0, meta={"reason": "insufficient_monthly_bars"}
        )
    last_close = _safe_float(monthly["close"].iloc[-1])
    sma10 = _safe_float(_sma(monthly["close"], 10).iloc[-1])
    lookback = min(12, len(monthly))
    high_52w = _safe_float(monthly["high"].astype(float).iloc[-lookback:].max())
    near = high_52w > 0 and last_close >= 0.95 * high_52w
    above = above_sma(last_close, sma10)
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
            "decision_bar": "completed_month",
        },
    )


def evaluate_monthly_pullback(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Uptrend pullback toward the 10-month SMA: still above, this month tagged it."""
    monthly = resample_monthly(df, now=now)
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
    still_above = above_sma(close, sma10)
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
            "decision_bar": "completed_month",
        },
    )


def evaluate_opening_range_breakout(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Daily RVOL + strong-close screen. Not a 5-minute opening-range engine."""
    frame = _completed_ohlcv(df, min_rows=25, now=now)
    if frame is None:
        return _plugin(
            name="opening_range_breakout", triggered=False, raw_score=0.0, meta={"reason": "insufficient_bars"}
        )
    last = frame.iloc[-1]
    prior_high = _safe_float(frame["high"].iloc[-2])
    open_ = _safe_float(last["open"])
    high = _safe_float(last["high"])
    low = _safe_float(last["low"])
    close = _safe_float(last["close"])
    vol = _safe_float(last["volume"])
    hist_vol = frame["volume"].astype(float).iloc[:-1]
    avg_vol = _safe_float(hist_vol.iloc[-50:].mean() if len(hist_vol) else 0.0)
    rvol = (vol / avg_vol) if avg_vol > 0 else 0.0
    loc = _close_in_range_pct(open_, high, low, close)
    sip = rvol >= 1.5
    through_open = close > open_ > 0
    through_prior = prior_high > 0 and close > prior_high
    location_ok = loc is not None and loc >= 0.5
    liquid = liquid_enough(frame)
    triggered = bool(sip and through_open and through_prior and location_ok and liquid)
    score = 0.0
    if sip:
        score += min(35.0, rvol * 15.0)
    if through_open:
        score += 20.0
    if through_prior:
        score += 25.0
    if location_ok and loc is not None:
        score += loc * 20.0
    return _plugin(
        name="opening_range_breakout",
        triggered=triggered,
        raw_score=score,
        meta={
            "rvol": round(rvol, 3),
            "close_above_open": through_open,
            "close_above_prior_high": through_prior,
            "close_in_range": round(loc, 3) if loc is not None else None,
            "bar_engine": "daily_rvol_proxy",
            "liquid": liquid,
            "decision_bar": "completed_daily",
        },
    )


def evaluate_st_reversal_5d(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Long-only 5-session loser bounce on completed bars.

    Formation uses the five closes ending yesterday; today's completed close is
    only the turn confirmation — not part of the loser window.
    """
    frame = _completed_ohlcv(df, min_rows=8, now=now)
    if frame is None:
        return _plugin(name="st_reversal_5d", triggered=False, raw_score=0.0, meta={"reason": "insufficient_bars"})
    close = frame["close"].astype(float)
    last = _safe_float(close.iloc[-1])
    start = _safe_float(close.iloc[-7])
    formation_end = _safe_float(close.iloc[-2])
    if start <= 0 or last <= 0 or formation_end <= 0:
        return _plugin(name="st_reversal_5d", triggered=False, raw_score=0.0, meta={"reason": "invalid_prices"})
    ret5 = (formation_end / start) - 1.0
    turned = last > formation_end
    liquid = liquid_enough(frame)
    triggered = bool(ret5 <= -0.0799 and turned and liquid)
    score = 0.0
    if ret5 <= -0.0799:
        score += min(45.0, abs(ret5) * 250.0)
    if turned:
        score += 30.0
    if liquid:
        score += 25.0
    return _plugin(
        name="st_reversal_5d",
        triggered=triggered,
        raw_score=score,
        meta={
            "return_5d_pct": round(ret5 * 100.0, 2),
            "turned_up": turned,
            "adv_usd": round(avg_dollar_volume(frame), 0),
            "volume_floor_ok": liquid,
            "decision_bar": "completed_daily",
        },
    )


def evaluate_overnight_gap_fade(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """EOD label: gap down that recovered by the completed close. Not an open fade."""
    frame = _completed_ohlcv(df, min_rows=3, now=now)
    if frame is None:
        return _plugin(name="overnight_gap_fade", triggered=False, raw_score=0.0, meta={"reason": "insufficient_bars"})
    last = frame.iloc[-1]
    prior_close = _safe_float(frame["close"].iloc[-2])
    open_ = _safe_float(last["open"])
    close = _safe_float(last["close"])
    if prior_close <= 0 or open_ <= 0 or close <= 0:
        return _plugin(name="overnight_gap_fade", triggered=False, raw_score=0.0, meta={"reason": "invalid_prices"})
    gap_pct = (open_ - prior_close) / prior_close
    gap_span = prior_close - open_
    fill = ((close - open_) / gap_span) if gap_span > 0 else 0.0
    liquid = liquid_enough(frame)
    triggered = bool(gap_pct <= -0.015 and close > open_ and fill >= 0.5 and liquid)
    score = 0.0
    if gap_pct <= -0.015:
        score += min(40.0, abs(gap_pct) * 800.0)
    if close > open_:
        score += 30.0
    if fill >= 0.5:
        score += min(30.0, fill * 30.0)
    return _plugin(
        name="overnight_gap_fade",
        triggered=triggered,
        raw_score=score,
        meta={
            "gap_pct": round(gap_pct * 100.0, 2),
            "gap_fill_frac": round(fill, 3),
            "label_kind": "eod_recovery",
            "liquid": liquid,
            "decision_bar": "completed_daily",
        },
    )


def evaluate_weekly_reversal(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Weekly loser bounce on completed Friday bars (not a Lehmann WML book)."""
    weekly = resample_weekly(df, now=now)
    if len(weekly) < 4:
        return _plugin(name="weekly_reversal", triggered=False, raw_score=0.0, meta={"reason": "insufficient_weekly_bars"})
    prior_close = _safe_float(weekly["close"].iloc[-2])
    prior_start = _safe_float(weekly["close"].iloc[-3])
    last = _safe_float(weekly["close"].iloc[-1])
    if prior_start <= 0 or prior_close <= 0 or last <= 0:
        return _plugin(name="weekly_reversal", triggered=False, raw_score=0.0, meta={"reason": "invalid_prices"})
    prior_ret = (prior_close / prior_start) - 1.0
    recovering = last > prior_close
    triggered = bool(prior_ret <= -0.06 and recovering)
    score = 0.0
    if prior_ret <= -0.06:
        score += min(50.0, abs(prior_ret) * 300.0)
    if recovering:
        score += 40.0
    return _plugin(
        name="weekly_reversal",
        triggered=triggered,
        raw_score=score,
        meta={
            "prior_week_return_pct": round(prior_ret * 100.0, 2),
            "recovering": recovering,
            "decision_bar": "completed_week",
        },
    )


def evaluate_momentum_12_1(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Single-name 12-1 strength screen (skip last month). Not a WML portfolio."""
    frame = _completed_ohlcv(df, min_rows=260, now=now)
    if frame is None:
        return _plugin(name="momentum_12_1", triggered=False, raw_score=0.0, meta={"reason": "insufficient_bars"})
    end_px = _safe_float(frame["close"].iloc[-22])
    start_px = _safe_float(frame["close"].iloc[-253])
    last_close = _safe_float(frame["close"].iloc[-1])
    if start_px <= 0 or end_px <= 0:
        return _plugin(name="momentum_12_1", triggered=False, raw_score=0.0, meta={"reason": "invalid_prices"})
    formation = (end_px / start_px) - 1.0
    sma200 = _safe_float(_sma(frame["close"], 200).iloc[-1])
    trend_ok = above_sma(last_close, sma200)
    liquid = liquid_enough(frame)
    triggered = bool(formation >= 0.20 and trend_ok and liquid)
    score = 0.0
    if formation >= 0.20:
        score += min(60.0, formation * 80.0)
    if trend_ok:
        score += 30.0
    return _plugin(
        name="momentum_12_1",
        triggered=triggered,
        raw_score=score,
        meta={
            "formation_return_pct": round(formation * 100.0, 2),
            "skip_days": 21,
            "above_sma200": trend_ok,
            "liquid": liquid,
            "decision_bar": "completed_daily",
        },
    )


def evaluate_tsmom_12m(df: pd.DataFrame, *, now: Any | None = None) -> dict[str, Any]:
    """Single-name analog of 12-month time-series momentum. Not a futures overlay."""
    monthly = resample_monthly(df, now=now)
    if len(monthly) < 13:
        return _plugin(name="tsmom_12m", triggered=False, raw_score=0.0, meta={"reason": "insufficient_monthly_bars"})
    last = _safe_float(monthly["close"].iloc[-1])
    ago = _safe_float(monthly["close"].iloc[-13])
    if ago <= 0 or last <= 0:
        return _plugin(name="tsmom_12m", triggered=False, raw_score=0.0, meta={"reason": "invalid_prices"})
    ret = (last / ago) - 1.0
    triggered = bool(last > ago)
    score = 55.0 if triggered else 10.0
    if triggered:
        score += min(35.0, max(0.0, ret) * 80.0)
    return _plugin(
        name="tsmom_12m",
        triggered=triggered,
        raw_score=score,
        meta={
            "return_12m_pct": round(ret * 100.0, 2),
            "monthly_close": round(last, 4),
            "close_12m_ago": round(ago, 4),
            "decision_bar": "completed_month",
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
    "opening_range_breakout": evaluate_opening_range_breakout,
    "st_reversal_5d": evaluate_st_reversal_5d,
    "overnight_gap_fade": evaluate_overnight_gap_fade,
    "weekly_reversal": evaluate_weekly_reversal,
    "momentum_12_1": evaluate_momentum_12_1,
    "tsmom_12m": evaluate_tsmom_12m,
}


def evaluate_horizon_plugins(df: pd.DataFrame | None, *, now: Any | None = None) -> list[dict[str, Any]]:
    """Evaluate every horizon sleeve against completed daily OHLCV. Always shadow mode."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return [
            _plugin(name=name, triggered=False, raw_score=0.0, meta={"reason": "missing_df"}) for name in _EVALUATORS
        ]
    return [fn(df, now=now) for fn in _EVALUATORS.values()]


def selected_horizon_plugin_hits(
    df: pd.DataFrame | None,
    strategy_ids: list[str] | None,
    *,
    pullback_triggered: bool = False,
    now: Any | None = None,
) -> list[dict[str, Any]]:
    """Triggered dual-admit plugins for this name, including pullback."""
    wanted = set(selected_dual_admit_ids(strategy_ids))
    if not wanted:
        return []
    hits: list[dict[str, Any]] = []
    if "pullback" in wanted and pullback_triggered:
        hits.append({"name": "pullback", "raw_score": 50.0, "triggered": True, "meta": {}})
    if df is None:
        return hits
    for plugin in evaluate_horizon_plugins(df, now=now):
        name = str(plugin.get("name") or "")
        if name in wanted and plugin.get("triggered"):
            hits.append(plugin)
    return hits


def triggered_dual_admit_ids(
    df: pd.DataFrame | None,
    strategy_ids: list[str] | None,
    *,
    pullback_triggered: bool = False,
    now: Any | None = None,
) -> list[str]:
    """Return selected dual-admit ids that fired on this name."""
    return [
        str(hit.get("name") or "")
        for hit in selected_horizon_plugin_hits(
            df, strategy_ids, pullback_triggered=pullback_triggered, now=now
        )
        if str(hit.get("name") or "")
    ]
