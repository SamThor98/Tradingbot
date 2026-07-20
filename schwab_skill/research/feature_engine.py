"""Point-in-time OHLCV feature computation for the research feature store."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from stage_analysis import (
    AVG_VOL_50,
    SMA_50,
    SMA_150,
    SMA_200,
    TRADING_DAYS_52W,
    _slope_per_step,
    add_indicators,
    is_stage_2,
)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _percentile_rank(series: pd.Series, value: float) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float((clean <= value).mean())


def _prior_return(close: pd.Series, bars: int) -> float | None:
    if len(close) <= bars:
        return None
    base = float(close.iloc[-(bars + 1)])
    last = float(close.iloc[-1])
    if base == 0:
        return None
    return (last / base) - 1.0


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Lower-case OHLCV columns and ensure a DatetimeIndex."""
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"OHLCV missing columns: {sorted(missing)}")
    if not isinstance(out.index, pd.DatetimeIndex):
        if "date" in out.columns:
            out["date"] = pd.to_datetime(out["date"])
            out = out.set_index("date")
        else:
            out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def window_asof(df: pd.DataFrame, asof_date: str | pd.Timestamp) -> pd.DataFrame:
    """Return bars with index date <= asof_date (point-in-time safe)."""
    norm = normalize_ohlcv(df)
    asof = pd.Timestamp(asof_date).normalize()
    # Allow timezone-naive compare
    idx = norm.index
    if getattr(idx, "tz", None) is not None:
        asof = asof.tz_localize(idx.tz) if asof.tzinfo is None else asof
    return norm.loc[idx <= asof]


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add SMAs/ATR/avg volume plus sma_20 used by the research schema."""
    out = add_indicators(normalize_ohlcv(df))
    close = out["close"].astype(float)
    out["sma_20"] = close.rolling(20, min_periods=1).mean()
    return out


def compute_ohlcv_features(df: pd.DataFrame, skill_dir: Any = None) -> dict[str, float | None]:
    """
    Compute schema-v1 OHLCV features on a PIT window ending at the last bar.

    Does not include cross-sectional or enrichment features (those are merged
    by the materializer from extras).
    """
    if df is None or len(df) < 30:
        return {}

    from config import get_stage2_sma_upward_days, get_vcp_days

    work = enrich_indicators(df)
    latest = work.iloc[-1]
    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)
    volume = work["volume"].astype(float)
    price = float(close.iloc[-1])
    if price <= 0:
        return {}

    feats: dict[str, float | None] = {}

    # --- Trend / SMAs ---
    for col, key in (
        ("sma_20", "sma_20"),
        (SMA_50, "sma_50"),
        (SMA_150, "sma_150"),
        (SMA_200, "sma_200"),
    ):
        feats[key] = _safe_float(latest.get(col))

    sma20 = work["sma_20"].dropna()
    sma50 = work[SMA_50].dropna()
    sma200 = work[SMA_200].dropna()
    feats["sma_20_slope"] = (
        _safe_float(_slope_per_step(sma20.iloc[-20:]) / price) if len(sma20) >= 5 else None
    )
    feats["sma_50_slope"] = (
        _safe_float(_slope_per_step(sma50.iloc[-20:]) / price) if len(sma50) >= 5 else None
    )
    upward_days = int(get_stage2_sma_upward_days(skill_dir))
    n_slope = upward_days + 1
    feats["sma_200_slope"] = (
        _safe_float(_slope_per_step(sma200.iloc[-n_slope:]) / price) if len(sma200) >= n_slope else None
    )

    sma50_v = _safe_float(latest.get(SMA_50))
    sma200_v = _safe_float(latest.get(SMA_200))
    feats["dist_sma50_pct"] = ((price - sma50_v) / sma50_v) if sma50_v and sma50_v > 0 else None
    feats["dist_sma200_pct"] = ((price - sma200_v) / sma200_v) if sma200_v and sma200_v > 0 else None

    lookback = min(TRADING_DAYS_52W, len(work))
    high_52w = float(high.iloc[-lookback:].max()) if lookback else None
    feats["pct_from_52w_high"] = (price / high_52w) if high_52w and high_52w > 0 else None

    # Continuous stage / weinstein / trend strength
    stack_ok = (
        sma50_v is not None
        and sma200_v is not None
        and price > sma50_v > float(latest.get(SMA_150) or 0) > sma200_v
    )
    slope_pos = (feats["sma_200_slope"] or 0.0) > 0
    near_high = (feats["pct_from_52w_high"] or 0.0) >= 0.75
    stage_parts = [1.0 if stack_ok else 0.0, 1.0 if slope_pos else 0.0, 1.0 if near_high else 0.0]
    if feats["pct_from_52w_high"] is not None:
        stage_parts.append(max(0.0, min(1.0, (feats["pct_from_52w_high"] - 0.7) / 0.3)))
    feats["stage_score"] = float(sum(stage_parts) / len(stage_parts))
    dist200 = feats["dist_sma200_pct"] or 0.0
    feats["weinstein_score"] = float(
        max(
            0.0,
            min(
                1.0,
                0.4 * feats["stage_score"]
                + 0.3 * max(0.0, min(1.0, dist200 * 10))
                + 0.3 * max(0.0, min(1.0, (feats["sma_200_slope"] or 0.0) * 500)),
            ),
        )
    )
    feats["trend_strength_score"] = float(
        max(
            0.0,
            min(
                1.0,
                0.5 * feats["weinstein_score"]
                + 0.3 * max(0.0, min(1.0, (feats["dist_sma50_pct"] or 0.0) * 8))
                + 0.2 * max(0.0, min(1.0, (feats["sma_50_slope"] or 0.0) * 400)),
            ),
        )
    )

    # --- Volatility ---
    atr = _safe_float(latest.get("atr_14"))
    feats["atr_14"] = atr
    feats["atr_pct"] = (atr / price) if atr is not None and price > 0 else None
    atr_pct_series = (work["atr_14"].astype(float) / close.replace(0, np.nan)).replace(
        [np.inf, -np.inf], np.nan
    )
    if feats["atr_pct"] is not None and len(atr_pct_series.dropna()) >= 5:
        feats["atr_percentile_126d"] = _percentile_rank(atr_pct_series.iloc[-126:], feats["atr_pct"])
        atr_sma20 = float(atr_pct_series.iloc[-20:].mean())
        feats["atr_expansion_rate"] = (feats["atr_pct"] / atr_sma20 - 1.0) if atr_sma20 > 0 else None
        med63 = float(atr_pct_series.iloc[-63:].median()) if len(atr_pct_series) >= 5 else None
        if med63 is not None:
            streak = 0
            for val in reversed(atr_pct_series.tolist()):
                if pd.isna(val) or float(val) >= med63:
                    break
                streak += 1
            feats["atr_contraction_duration"] = float(streak)
        else:
            feats["atr_contraction_duration"] = None
    else:
        feats["atr_percentile_126d"] = None
        feats["atr_expansion_rate"] = None
        feats["atr_contraction_duration"] = None

    log_ret = np.log(close / close.shift(1)).replace([np.inf, -np.inf], np.nan)
    if len(log_ret.dropna()) >= 5:
        rv20 = float(log_ret.iloc[-20:].std(ddof=0) * math.sqrt(252))
        feats["realized_vol_20d"] = rv20
        rv_series = log_ret.rolling(20, min_periods=5).std(ddof=0) * math.sqrt(252)
        feats["hist_vol_percentile_252d"] = _percentile_rank(rv_series.iloc[-252:], rv20)
    else:
        feats["realized_vol_20d"] = None
        feats["hist_vol_percentile_252d"] = None

    mid = close.rolling(20, min_periods=5).mean()
    std20 = close.rolling(20, min_periods=5).std(ddof=0)
    upper = mid + 2 * std20
    lower = mid - 2 * std20
    bw = ((upper - lower) / mid.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    feats["bb_bandwidth_20"] = _safe_float(bw.iloc[-1]) if len(bw) else None
    bw_pct = _percentile_rank(bw.iloc[-126:], feats["bb_bandwidth_20"]) if feats["bb_bandwidth_20"] is not None else None
    atr_comp = 1.0 - (feats["atr_percentile_126d"] or 0.5)
    bw_comp = 1.0 - (bw_pct or 0.5)
    feats["compression_score"] = float(max(0.0, min(1.0, 0.5 * atr_comp + 0.5 * bw_comp)))

    # --- Volume ---
    avg_vol = _safe_float(latest.get(AVG_VOL_50))
    last_vol = _safe_float(latest.get("volume"))
    feats["volume_ratio"] = (last_vol / avg_vol) if last_vol is not None and avg_vol and avg_vol > 0 else None
    vcp_days = int(get_vcp_days(skill_dir))
    last_n = work.iloc[-vcp_days:] if len(work) >= vcp_days else work
    ratios = []
    dry_days = 0
    for _, row in last_n.iterrows():
        avg_v = _safe_float(row.get(AVG_VOL_50))
        vol = _safe_float(row.get("volume"))
        if avg_v and avg_v > 0 and vol is not None:
            ratio = vol / avg_v
            ratios.append(ratio)
            if vol < avg_v:
                dry_days += 1
    feats["avg_vcp_volume_ratio"] = float(sum(ratios) / len(ratios)) if ratios else None
    feats["vcp_contraction_days"] = float(dry_days)
    vol_ratio_series = (volume / work[AVG_VOL_50].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
    if feats["volume_ratio"] is not None:
        feats["rvol_percentile_60d"] = _percentile_rank(vol_ratio_series.iloc[-60:], feats["volume_ratio"])
    else:
        feats["rvol_percentile_60d"] = None
    log_vol = np.log(volume.replace(0, np.nan))
    if len(log_vol.dropna()) >= 10:
        mu = float(log_vol.iloc[-60:].mean())
        sigma = float(log_vol.iloc[-60:].std(ddof=0))
        cur = float(log_vol.iloc[-1]) if not pd.isna(log_vol.iloc[-1]) else None
        feats["volume_anomaly_score"] = ((cur - mu) / sigma) if cur is not None and sigma > 1e-9 else None
    else:
        feats["volume_anomaly_score"] = None
    feats["dollar_volume"] = price * last_vol if last_vol is not None else None
    dvol = close * volume
    if feats["dollar_volume"] is not None:
        feats["dollar_volume_percentile_60d"] = _percentile_rank(dvol.iloc[-60:], feats["dollar_volume"])
    else:
        feats["dollar_volume_percentile_60d"] = None

    window20 = work.iloc[-20:] if len(work) >= 2 else work
    up_vol = 0.0
    down_vol = 0.0
    total_vol = 0.0
    prev_c = None
    for _, row in window20.iterrows():
        c = float(row["close"])
        v = float(row["volume"])
        total_vol += v
        if prev_c is not None:
            if c > prev_c:
                up_vol += v
            elif c < prev_c:
                down_vol += v
        prev_c = c
    feats["accumulation_score"] = (up_vol / total_vol) if total_vol > 0 else None
    feats["distribution_score"] = (down_vol / total_vol) if total_vol > 0 else None

    direction = np.sign(close.diff().fillna(0.0))
    obv = (direction * volume).cumsum()
    if len(obv) >= 5:
        feats["obv_slope_20d"] = _safe_float(_slope_per_step(obv.iloc[-20:]) / max(price, 1.0))
    else:
        feats["obv_slope_20d"] = None
    if len(volume) >= 5:
        feats["volume_trend_20d"] = _safe_float(
            _slope_per_step(volume.iloc[-20:]) / max(float(volume.iloc[-20:].mean()), 1.0)
        )
    else:
        feats["volume_trend_20d"] = None

    dry = 1.0 - min(1.0, max(0.0, feats["avg_vcp_volume_ratio"] or 1.0))
    feats["vol_contraction_score"] = float(
        max(0.0, min(1.0, 0.6 * dry + 0.4 * (feats["vcp_contraction_days"] or 0.0) / max(vcp_days, 1)))
    )
    feats["vcp_score"] = feats["vol_contraction_score"]
    feats["volume_score"] = float(
        max(
            0.0,
            min(
                1.0,
                0.4 * (feats["rvol_percentile_60d"] or 0.5)
                + 0.3 * feats["vol_contraction_score"]
                + 0.3 * max(0.0, min(1.0, (feats["accumulation_score"] or 0.5))),
            ),
        )
    )

    # --- Breakout ---
    if len(high) >= 21:
        pivot = float(high.iloc[-21:-1].max())
        feats["breakout_distance_pct"] = (price / pivot - 1.0) if pivot > 0 else None
        feats["dist_above_pivot_pct"] = feats["breakout_distance_pct"]
    else:
        feats["breakout_distance_pct"] = None
        feats["dist_above_pivot_pct"] = None
    feats["breakout_velocity"] = _prior_return(close, 5)

    persistence = 0
    if len(high) >= 22:
        for i in range(len(work) - 1, max(len(work) - 40, 20), -1):
            pivot_i = float(high.iloc[max(0, i - 20) : i].max())
            if pivot_i > 0 and float(close.iloc[i]) >= pivot_i:
                persistence += 1
            else:
                break
    feats["breakout_persistence"] = float(persistence)

    failed = 0
    touches = 0
    if len(work) >= 25:
        for i in range(max(21, len(work) - 63), len(work)):
            pivot_i = float(high.iloc[i - 20 : i].max())
            if pivot_i <= 0:
                continue
            hi = float(high.iloc[i])
            cl = float(close.iloc[i])
            if hi >= pivot_i * 0.99:
                touches += 1
            if hi >= pivot_i and cl < pivot_i:
                failed += 1
    feats["failed_breakout_attempts_63d"] = float(failed)
    feats["prior_resistance_touches_63d"] = float(touches)
    bd = feats["breakout_distance_pct"] or 0.0
    feats["breakout_quality_score"] = float(
        max(
            0.0,
            min(
                1.0,
                0.4 * max(0.0, min(1.0, bd * 20 + 0.5))
                + 0.3 * max(0.0, min(1.0, (feats["breakout_velocity"] or 0.0) * 10))
                + 0.3 * max(0.0, min(1.0, (feats["breakout_persistence"] or 0.0) / 10.0)),
            ),
        )
    )

    # --- Momentum ---
    for bars, key in (
        (5, "ret_5d_prev"),
        (10, "ret_10d_prev"),
        (20, "ret_20d_prev"),
        (60, "ret_60d_prev"),
        (120, "ret_120d_prev"),
        (252, "ret_252d_prev"),
    ):
        feats[key] = _prior_return(close, bars)
    r20 = feats["ret_20d_prev"]
    r60 = feats["ret_60d_prev"]
    feats["momentum_acceleration"] = (r20 - (r60 / 3.0)) if r20 is not None and r60 is not None else None

    # --- Liquidity / structure ---
    feats["adv_20d"] = _safe_float(volume.iloc[-20:].mean()) if len(volume) >= 5 else None
    feats["dollar_adv_20d"] = _safe_float(dvol.iloc[-20:].mean()) if len(dvol) >= 5 else None

    if feats["bb_bandwidth_20"] is not None and len(bw.dropna()) >= 5:
        med_bw = float(bw.iloc[-126:].median()) if len(bw) >= 20 else float(bw.median())
        streak = 0
        for val in reversed(bw.tolist()):
            if pd.isna(val) or float(val) > med_bw:
                break
            streak += 1
        feats["base_duration_days"] = float(streak)
    else:
        feats["base_duration_days"] = None
    if len(work) >= 10:
        hi63 = float(high.iloc[-63:].max())
        lo63 = float(low.iloc[-63:].min())
        feats["base_depth_pct"] = ((hi63 - lo63) / hi63) if hi63 > 0 else None
    else:
        feats["base_depth_pct"] = None
    if len(work) >= 3:
        gaps = (work["open"].astype(float) / close.shift(1) - 1.0).abs().iloc[-20:]
        feats["gap_stats_20d"] = _safe_float(gaps.mean())
    else:
        feats["gap_stats_20d"] = None

    return feats


def compute_feature_row(
    *,
    ticker: str,
    df: pd.DataFrame,
    asof_date: str | pd.Timestamp,
    extras: dict[str, Any] | None = None,
    candidate_set_version: str = "stage2_pass_v1",
    feature_schema_version: int = 1,
    bar_provider: str | None = None,
    skill_dir: Any = None,
    require_stage2: bool = True,
) -> dict[str, Any] | None:
    """
    Build one research feature row for (ticker, asof_date).

    Returns None when require_stage2 and the PIT window fails Stage 2.
    """
    from research.registry import enabled_feature_names, feature_coverage

    pit = window_asof(df, asof_date)
    if len(pit) < 50:
        return None
    if require_stage2 and not is_stage_2(pit, skill_dir=skill_dir):
        return None

    ohlcv_feats = compute_ohlcv_features(pit, skill_dir=skill_dir)
    if not ohlcv_feats:
        return None

    row: dict[str, Any] = {
        "asof_date": str(pd.Timestamp(asof_date).date()),
        "ticker": str(ticker).upper(),
        "candidate_set_version": candidate_set_version,
        "feature_schema_version": int(feature_schema_version),
        "bar_provider": bar_provider,
    }
    row.update(ohlcv_feats)
    if extras:
        for key, value in extras.items():
            if value is not None:
                row[key] = value

    ohlcv_names = enabled_feature_names(ohlcv_only=True)
    row["feature_coverage"] = feature_coverage(row, ohlcv_names)
    return row


def iter_stage2_asof_dates(
    df: pd.DataFrame,
    *,
    start: str | None = None,
    end: str | None = None,
    skill_dir: Any = None,
    min_bars: int = 200,
) -> list[str]:
    """Return asof dates (YYYY-MM-DD) where Stage 2 passes on the PIT window."""
    norm = normalize_ohlcv(df)
    if start:
        norm = norm.loc[norm.index >= pd.Timestamp(start)]
    if end:
        norm = norm.loc[norm.index <= pd.Timestamp(end)]
    dates: list[str] = []
    # Walk forward; for unit tests this is fine on short series.
    for i in range(min_bars, len(norm)):
        window = norm.iloc[: i + 1]
        if is_stage_2(window, skill_dir=skill_dir):
            dates.append(str(window.index[-1].date()))
    return dates
