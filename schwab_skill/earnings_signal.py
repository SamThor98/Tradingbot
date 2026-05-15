"""
Earnings signal helpers for PEAD-style enrichment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config import get_schwab_only_data

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent


def _normalize_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def _normalize_earnings_df(df: Any) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    if isinstance(df, pd.DataFrame):
        out = df.copy()
    else:
        return pd.DataFrame()
    if out.empty:
        return out
    if not isinstance(out.index, pd.DatetimeIndex):
        try:
            out.index = pd.to_datetime(out.index, errors="coerce")
        except Exception:
            return pd.DataFrame()
    out = out[~out.index.isna()]
    if out.empty:
        return out
    out.index = out.index.tz_localize(None) if out.index.tz is not None else out.index
    out = out.sort_index(ascending=False)
    return out


def _extract_eps_cols(df: pd.DataFrame) -> tuple[str | None, str | None]:
    cols_lower = {str(c).lower(): c for c in df.columns}
    rep = None
    est = None
    for key, col in cols_lower.items():
        if "reported eps" in key:
            rep = col
        if "eps estimate" in key:
            est = col
    return rep, est


# Smallest |estimate_eps| we'll trust as a denominator. EPS estimates of a few
# cents are common for small/mid caps and turn a $0.03 beat into a "+150%
# surprise," firing the large-PEAD boost incorrectly. Floor the denominator
# at $0.10 so the surprise stays interpretable on near-zero estimates.
_SURPRISE_DENOMINATOR_FLOOR = 0.10
# Hard winsorization clamp on the final fractional surprise. ±300% covers
# every legitimate beat/miss while killing pathological ratios from data-
# entry errors or 1-cent estimates.
_SURPRISE_CLAMP = 3.0


def _calc_surprise(actual_eps: float | None, estimate_eps: float | None) -> float | None:
    if actual_eps is None or estimate_eps is None:
        return None
    try:
        actual_f = float(actual_eps)
        est_f = float(estimate_eps)
    except (TypeError, ValueError):
        return None
    if abs(est_f) < 1e-9:
        return None
    denom = max(abs(est_f), _SURPRISE_DENOMINATOR_FLOOR)
    raw = (actual_f - est_f) / denom
    if raw != raw:  # NaN guard
        return None
    return max(-_SURPRISE_CLAMP, min(_SURPRISE_CLAMP, raw))


def check_recent_earnings(ticker: str, lookback_days: int = 10) -> dict[str, Any] | None:
    """
    Check if ticker had earnings within lookback window from now.
    Returns EPS surprise details when available.
    """
    if get_schwab_only_data():
        return None
    try:
        import yfinance as yf

        from _io_utils import yfinance_call

        tkr = _normalize_ticker(ticker)
        with yfinance_call():
            df = _normalize_earnings_df(yf.Ticker(tkr).earnings_dates)
        if df.empty:
            return None

        now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
        window_start = now - pd.Timedelta(days=max(1, int(lookback_days)))
        recent = df[(df.index <= now) & (df.index >= window_start)]
        if recent.empty:
            return {
                "had_recent_earnings": False,
                "earnings_date": None,
                "actual_eps": None,
                "estimate_eps": None,
                "surprise_pct": None,
                "beat": None,
            }

        row = recent.iloc[0]
        rep_col, est_col = _extract_eps_cols(recent)
        actual_eps = float(row[rep_col]) if rep_col and pd.notna(row[rep_col]) else None
        estimate_eps = float(row[est_col]) if est_col and pd.notna(row[est_col]) else None
        surprise = _calc_surprise(actual_eps, estimate_eps)
        beat = None if surprise is None else bool(surprise > 0)
        return {
            "had_recent_earnings": True,
            "earnings_date": str(recent.index[0].date()),
            "actual_eps": actual_eps,
            "estimate_eps": estimate_eps,
            "surprise_pct": surprise,
            "beat": beat,
        }
    except Exception as exc:
        LOG.debug("Recent earnings check failed for %s: %s", ticker, exc)
        return None


def check_earnings_at_date(
    ticker: str,
    date: Any,
    df: pd.DataFrame | None = None,
    lookback_days: int = 10,
) -> dict[str, Any] | None:
    """
    Historical earnings check relative to a supplied entry date.
    """
    if get_schwab_only_data():
        return None
    try:
        import yfinance as yf

        from _io_utils import yfinance_call

        tkr = _normalize_ticker(ticker)
        with yfinance_call():
            earnings = _normalize_earnings_df(yf.Ticker(tkr).earnings_dates)
        if earnings.empty:
            return None

        anchor = pd.Timestamp(date).tz_localize(None)
        window_start = anchor - pd.Timedelta(days=max(1, int(lookback_days)))
        recent = earnings[(earnings.index <= anchor) & (earnings.index >= window_start)]
        if recent.empty:
            return {
                "had_recent_earnings": False,
                "earnings_date": None,
                "actual_eps": None,
                "estimate_eps": None,
                "surprise_pct": None,
                "beat": None,
            }

        row = recent.iloc[0]
        rep_col, est_col = _extract_eps_cols(recent)
        actual_eps = float(row[rep_col]) if rep_col and pd.notna(row[rep_col]) else None
        estimate_eps = float(row[est_col]) if est_col and pd.notna(row[est_col]) else None
        surprise = _calc_surprise(actual_eps, estimate_eps)
        beat = None if surprise is None else bool(surprise > 0)
        return {
            "had_recent_earnings": True,
            "earnings_date": str(recent.index[0].date()),
            "actual_eps": actual_eps,
            "estimate_eps": estimate_eps,
            "surprise_pct": surprise,
            "beat": beat,
        }
    except Exception as exc:
        LOG.debug("Historical earnings check failed for %s: %s", ticker, exc)
        return None
