"""Shared builder for the Kronos forecast endpoint payload.

Used by both the local dashboard (`routes/research.py`) and the SaaS tenant
dashboard so the interval routing, history assembly, and distribution payload
stay identical. Returns a plain dict (``{ok, error, data}``) so the library
layer never imports the webapp response models.

Interval routing:
- ``daily`` -> Schwab daily history (yfinance fallback inside market_data).
- ``5m`` / ``15m`` -> Schwab intraday history (no yfinance, by design).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

INTRADAY = {"5m", "15m"}


def _history_candles(df: Any) -> list[dict[str, Any]]:
    from datetime import datetime as _dt

    candles: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        ts = row.get("datetime") or row.get("date") or row.name
        try:
            if hasattr(ts, "timestamp"):
                epoch = int(ts.timestamp())
            else:
                epoch = int(_dt.fromisoformat(str(ts)).timestamp())
        except Exception:
            continue
        try:
            candles.append(
                {
                    "time": epoch,
                    "open": round(float(row.get("open", 0)), 4),
                    "high": round(float(row.get("high", 0)), 4),
                    "low": round(float(row.get("low", 0)), 4),
                    "close": round(float(row.get("close", 0)), 4),
                    "volume": int(row.get("volume", 0) or 0),
                }
            )
        except Exception:
            continue
    candles.sort(key=lambda c: c["time"])
    return candles


def build_forecast_payload(
    ticker: str,
    *,
    interval: str = "daily",
    days: int = 220,
    pred_len: int = 0,
    skill_dir: Path,
    auth: Any,
) -> dict[str, Any]:
    """Build the forecast response dict for ``ticker`` at ``interval``.

    Returns ``{"ok": bool, "error": str | None, "data": dict}``. Never raises for
    expected failure modes (no data, service offline) — those degrade cleanly.
    """
    from config import (
        get_kronos_intraday_days,
        get_kronos_lookback_bars,
        get_kronos_mode,
        get_kronos_pred_len,
    )
    from kronos_client import forecast as kronos_forecast

    symbol = ticker.upper().strip()
    interval = interval if interval in INTRADAY or interval == "daily" else "daily"
    scanner_mode = get_kronos_mode(skill_dir)
    horizon = int(pred_len) if int(pred_len) > 0 else get_kronos_pred_len(skill_dir)

    if interval in INTRADAY:
        from market_data import get_intraday_history_with_meta

        lookback = 480  # fill most of the 512 context with dense intraday bars
        df, meta = get_intraday_history_with_meta(
            symbol, interval=interval, days=get_kronos_intraday_days(skill_dir), auth=auth, skill_dir=skill_dir
        )
    else:
        from market_data import get_daily_history_with_meta

        lookback = get_kronos_lookback_bars(skill_dir)
        fetch_days = min(365, max(int(days), lookback + 20))
        df, meta = get_daily_history_with_meta(symbol, days=fetch_days, auth=auth, skill_dir=skill_dir)

    if df is None or df.empty:
        reason = meta.get("fallback_reason")
        msg = f"No {interval} price data for {symbol}"
        if interval in INTRADAY:
            msg += " (Schwab intraday only; ~10 trading days max)."
        return {
            "ok": False,
            "error": msg + (f" [{reason}]" if reason else ""),
            "data": {"ticker": symbol, "interval": interval, "provider": meta.get("provider")},
        }

    history_candles = _history_candles(df)
    fc = kronos_forecast(symbol, df, skill_dir=skill_dir, pred_len=horizon, lookback=lookback, interval=interval)

    if fc is None:
        return {
            "ok": False,
            "error": "Kronos forecast unavailable (inference service offline or degraded).",
            "data": {
                "ticker": symbol,
                "interval": interval,
                "degraded": True,
                "scanner_mode": scanner_mode,
                "history_candles": history_candles,
                "provider": meta.get("provider"),
            },
        }

    payload = fc.to_dict()
    payload.update(
        {
            "ticker": symbol,
            "interval": interval,
            "scanner_mode": scanner_mode,
            "history_candles": history_candles,
            "provider": meta.get("provider"),
            "used_fallback": meta.get("used_fallback"),
        }
    )
    return {"ok": True, "error": None, "data": payload}
