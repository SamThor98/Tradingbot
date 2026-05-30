"""Kronos inference microservice.

Standalone FastAPI service that loads the open-source Kronos foundation model
(https://github.com/shiyu-coder/Kronos) and exposes a single ``/predict``
endpoint that turns recent OHLCV candles into a forecast of future candles.

This service is intentionally isolated from the main TradingBot webapp so that
the heavy ``torch`` / ``transformers`` / Hugging Face weight footprint never
touches the lean web/worker images. The main app talks to it over HTTP via
``schwab_skill/kronos_client.py``.

The Kronos model package (``model/``) is vendored at build time by the
Dockerfile (``git clone`` at a pinned ref). For local development without
Docker, run ``python fetch_model_code.py`` first to clone the same package.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("kronos_service")

TOKENIZER_ID = os.environ.get("KRONOS_TOKENIZER_ID", "NeoQuasar/Kronos-Tokenizer-base")
MODEL_ID = os.environ.get("KRONOS_MODEL_ID", "NeoQuasar/Kronos-small")
DEVICE = os.environ.get("KRONOS_DEVICE", "cpu")
MAX_CONTEXT = int(os.environ.get("KRONOS_MAX_CONTEXT", "512"))

# Module-global predictor loaded once at startup. ``_LOAD_ERROR`` records the
# last load failure so /health and /predict can report it instead of raising.
_PREDICTOR: Any = None
_LOAD_ERROR: str | None = None


def _load_predictor() -> None:
    """Load the Kronos tokenizer + model once. Safe to call repeatedly."""
    global _PREDICTOR, _LOAD_ERROR
    if _PREDICTOR is not None:
        return
    try:
        from model import Kronos, KronosPredictor, KronosTokenizer

        tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_ID)
        model = Kronos.from_pretrained(MODEL_ID)
        _PREDICTOR = KronosPredictor(
            model, tokenizer, device=DEVICE, max_context=MAX_CONTEXT
        )
        _LOAD_ERROR = None
        LOG.info(
            "Kronos loaded: model=%s tokenizer=%s device=%s max_context=%s",
            MODEL_ID,
            TOKENIZER_ID,
            DEVICE,
            MAX_CONTEXT,
        )
    except Exception as exc:  # noqa: BLE001 - report, never crash the service
        _LOAD_ERROR = str(exc)
        LOG.exception("Failed to load Kronos predictor")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _load_predictor()
    yield


app = FastAPI(title="Kronos Inference Service", version="1.0.0", lifespan=lifespan)


class Candle(BaseModel):
    time: int  # epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class PredictRequest(BaseModel):
    symbol: str = ""
    ohlcv: list[Candle] = Field(default_factory=list)
    pred_len: int = 24
    lookback: int = 256
    temperature: float = 1.0
    top_p: float = 0.9
    sample_count: int = 1


def _confidence_from_path(
    last_close: float, closes: np.ndarray, exp_ret_pct: float
) -> float:
    """Heuristic confidence proxy in [0, 1].

    Combines (a) directional consistency of the predicted path with the net
    move and (b) the magnitude of the expected move. This is a pragmatic proxy
    until calibrated uncertainty is wired in; the main app treats it as advisory.
    """
    if closes.size == 0 or last_close <= 0:
        return 0.0
    steps = np.diff(np.concatenate([[last_close], closes]))
    if abs(exp_ret_pct) < 1e-9:
        consistency = 0.0
    else:
        consistency = float(np.mean(np.sign(steps) == np.sign(exp_ret_pct)))
    magnitude = float(min(abs(exp_ret_pct) / 5.0, 1.0))
    return round(0.5 * consistency + 0.5 * magnitude, 3)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": _PREDICTOR is not None,
        "loaded": _PREDICTOR is not None,
        "model_id": MODEL_ID,
        "tokenizer_id": TOKENIZER_ID,
        "device": DEVICE,
        "max_context": MAX_CONTEXT,
        "error": _LOAD_ERROR,
    }


@app.post("/predict")
def predict(req: PredictRequest) -> dict[str, Any]:
    if _PREDICTOR is None:
        _load_predictor()
    if _PREDICTOR is None:
        return {"ok": False, "error": f"model_not_loaded: {_LOAD_ERROR}"}

    if len(req.ohlcv) < 32:
        return {"ok": False, "error": "insufficient_history (need >= 32 candles)"}

    try:
        lookback = max(32, min(int(req.lookback or 256), MAX_CONTEXT))
        rows = req.ohlcv[-lookback:]
        df = pd.DataFrame(
            [
                {
                    "open": float(c.open),
                    "high": float(c.high),
                    "low": float(c.low),
                    "close": float(c.close),
                    "volume": float(c.volume or 0.0),
                }
                for c in rows
            ]
        )
        x_ts = pd.Series(pd.to_datetime([c.time for c in rows], unit="s"))
        last_ts = x_ts.iloc[-1]
        pred_len = max(1, min(int(req.pred_len or 24), 120))
        # Daily forecast horizon on business days following the last bar.
        future = pd.bdate_range(start=last_ts + pd.Timedelta(days=1), periods=pred_len)
        y_ts = pd.Series(future)

        pred_df = _PREDICTOR.predict(
            df=df,
            x_timestamp=x_ts,
            y_timestamp=y_ts,
            pred_len=pred_len,
            T=float(req.temperature or 1.0),
            top_p=float(req.top_p or 0.9),
            sample_count=max(1, int(req.sample_count or 1)),
            verbose=False,
        )

        last_close = float(df["close"].iloc[-1])
        forecast_candles: list[dict[str, Any]] = []
        for ts, row in pred_df.iterrows():
            forecast_candles.append(
                {
                    "time": int(pd.Timestamp(ts).timestamp()),
                    "open": round(float(row["open"]), 4),
                    "high": round(float(row["high"]), 4),
                    "low": round(float(row["low"]), 4),
                    "close": round(float(row["close"]), 4),
                    "volume": float(max(0.0, round(float(row.get("volume", 0.0)), 2))),
                }
            )

        final_close = float(pred_df["close"].iloc[-1])
        exp_ret_pct = (
            ((final_close - last_close) / last_close * 100.0) if last_close else 0.0
        )
        if exp_ret_pct > 0.25:
            direction = "up"
        elif exp_ret_pct < -0.25:
            direction = "down"
        else:
            direction = "flat"
        confidence = _confidence_from_path(
            last_close, pred_df["close"].to_numpy(dtype=float), exp_ret_pct
        )

        return {
            "ok": True,
            "data": {
                "symbol": req.symbol,
                "model_id": MODEL_ID,
                "pred_len": pred_len,
                "lookback": len(rows),
                "last_close": round(last_close, 4),
                "final_close": round(final_close, 4),
                "expected_return_pct": round(exp_ret_pct, 4),
                "direction": direction,
                "confidence": confidence,
                "forecast_candles": forecast_candles,
            },
        }
    except Exception as exc:  # noqa: BLE001 - never crash the service on bad input
        LOG.warning("Prediction failed for %s: %s", req.symbol, exc)
        return {"ok": False, "error": f"prediction_failed: {exc}"}
