"""HTTP client for the Kronos inference microservice.

This module is the only seam between the lean main app and the heavy Kronos
service. It is intentionally torch-free: it serializes recent OHLCV candles,
POSTs them to the service, and parses the forecast back into a small dataclass.

Per the degraded-mode policy, every failure path (service down, timeout, bad
payload, model not loaded) returns ``None`` and logs a warning — it never
raises into the scanner pipeline or an API request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent


@dataclass
class KronosForecast:
    """Parsed forecast from the Kronos service (advisory only)."""

    direction: str
    expected_return_pct: float
    confidence: float
    confidence_bucket: str
    model_version: str
    pred_len: int
    last_close: float = 0.0
    final_close: float = 0.0
    forecast_candles: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    source: str = "kronos"

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "expected_return_pct": round(float(self.expected_return_pct), 4),
            "confidence": round(float(self.confidence), 3),
            "confidence_bucket": self.confidence_bucket,
            "model_version": self.model_version,
            "pred_len": int(self.pred_len),
            "last_close": round(float(self.last_close), 4),
            "final_close": round(float(self.final_close), 4),
            "forecast_candles": self.forecast_candles,
            "degraded": bool(self.degraded),
            "source": self.source,
        }


def _bucket(confidence: float, skill_dir: Path | None) -> str:
    try:
        from config import get_kronos_confidence_high, get_kronos_confidence_low

        high = get_kronos_confidence_high(skill_dir)
        low = get_kronos_confidence_low(skill_dir)
    except Exception:
        high, low = 0.66, 0.4
    if confidence >= high:
        return "high"
    if confidence >= low:
        return "medium"
    return "low"


def _df_to_candles(df: Any, lookback: int) -> list[dict[str, Any]]:
    """Convert an OHLCV DataFrame (DatetimeIndex) into epoch candle dicts."""
    from datetime import datetime as _dt

    candles: list[dict[str, Any]] = []
    tail = df.tail(lookback) if hasattr(df, "tail") else df
    for _, row in tail.iterrows():
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
                    "open": float(row.get("open", 0.0)),
                    "high": float(row.get("high", 0.0)),
                    "low": float(row.get("low", 0.0)),
                    "close": float(row.get("close", 0.0)),
                    "volume": float(row.get("volume", 0.0) or 0.0),
                }
            )
        except Exception:
            continue
    candles.sort(key=lambda c: c["time"])
    return candles


def forecast(
    ticker: str,
    df: Any,
    *,
    skill_dir: Path | None = None,
    pred_len: int | None = None,
    lookback: int | None = None,
    timeout: float | None = None,
) -> KronosForecast | None:
    """Request a forecast for ``ticker`` from the Kronos service.

    Returns ``None`` on any failure (graceful degradation). ``df`` is an OHLCV
    DataFrame with a DatetimeIndex (as produced by ``market_data``).
    """
    skill_dir = skill_dir or SKILL_DIR
    try:
        from config import (
            get_kronos_inference_url,
            get_kronos_lookback_bars,
            get_kronos_model_id,
            get_kronos_pred_len,
            get_kronos_timeout_s,
        )

        url = get_kronos_inference_url(skill_dir)
        lookback = int(lookback or get_kronos_lookback_bars(skill_dir))
        pred_len = int(pred_len or get_kronos_pred_len(skill_dir))
        timeout = float(timeout or get_kronos_timeout_s(skill_dir))
        model_id = get_kronos_model_id(skill_dir)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Kronos config unavailable for %s: %s", ticker, exc)
        return None

    if df is None:
        return None
    candles = _df_to_candles(df, lookback)
    if len(candles) < 32:
        LOG.debug("Kronos skipped %s: only %d candles", ticker, len(candles))
        return None

    payload = {
        "symbol": str(ticker).upper(),
        "ohlcv": candles,
        "pred_len": pred_len,
        "lookback": lookback,
    }

    try:
        import requests

        resp = requests.post(f"{url}/predict", json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - degrade, never raise
        LOG.warning("Kronos request failed for %s: %s", ticker, exc)
        return None

    if not isinstance(body, dict) or not body.get("ok"):
        LOG.warning(
            "Kronos returned error for %s: %s",
            ticker,
            (body or {}).get("error") if isinstance(body, dict) else "bad_response",
        )
        return None

    data = body.get("data") or {}
    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
        return KronosForecast(
            direction=str(data.get("direction", "flat")),
            expected_return_pct=float(data.get("expected_return_pct", 0.0) or 0.0),
            confidence=confidence,
            confidence_bucket=_bucket(confidence, skill_dir),
            model_version=str(data.get("model_id") or model_id),
            pred_len=int(data.get("pred_len", pred_len) or pred_len),
            last_close=float(data.get("last_close", 0.0) or 0.0),
            final_close=float(data.get("final_close", 0.0) or 0.0),
            forecast_candles=list(data.get("forecast_candles") or []),
            degraded=False,
            source="kronos",
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Kronos response parse failed for %s: %s", ticker, exc)
        return None


def forecast_signal_kronos(
    ticker: str,
    df: Any,
    *,
    skill_dir: Path | None = None,
    regime_is_bullish: bool | None = None,
) -> KronosForecast | None:
    """Scanner-facing wrapper around :func:`forecast`.

    ``regime_is_bullish`` is accepted for signature symmetry with other Stage B
    enrichers; the regime gate itself is enforced by the caller before any LIVE
    score adjustment is applied.
    """
    return forecast(ticker, df, skill_dir=skill_dir)
