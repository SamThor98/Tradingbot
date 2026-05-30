"""Kronos inference microservice (distribution-returning).

Standalone FastAPI service that loads the open-source Kronos foundation model
(https://github.com/shiyu-coder/Kronos) and turns recent OHLCV candles into a
*distribution* of future paths: a median candle path, a p10/p90 close cone,
an expected % move, and P(up) versus a flat (random-walk) baseline.

It supports daily and intraday (5m/15m) inputs; for intraday it reconstructs
the exchange session grid from the supplied history so future timestamps land
on real trading slots (no synthetic overnight bars).

Isolated from the main app so the torch/HF footprint stays out of the lean
web/worker images. The model package is vendored at build time by the
Dockerfile; for local dev run ``python fetch_model_code.py`` first.
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

_PREDICTOR: Any = None
_LOAD_ERROR: str | None = None

# Feature column order Kronos expects.
_PRICE_COLS = ["open", "high", "low", "close"]
_VOL_COL = "volume"
_AMT_COL = "amount"
_CLOSE_IDX = 3  # index of 'close' within _PRICE_COLS


def _load_predictor() -> None:
    global _PREDICTOR, _LOAD_ERROR
    if _PREDICTOR is not None:
        return
    try:
        from model import Kronos, KronosPredictor, KronosTokenizer

        tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_ID)
        model = Kronos.from_pretrained(MODEL_ID)
        _PREDICTOR = KronosPredictor(model, tokenizer, device=DEVICE, max_context=MAX_CONTEXT)
        _LOAD_ERROR = None
        LOG.info(
            "Kronos loaded: model=%s tokenizer=%s device=%s max_context=%s",
            MODEL_ID,
            TOKENIZER_ID,
            DEVICE,
            MAX_CONTEXT,
        )
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = str(exc)
        LOG.exception("Failed to load Kronos predictor")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _load_predictor()
    yield


app = FastAPI(title="Kronos Inference Service", version="2.0.0", lifespan=lifespan)


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
    interval: str = "daily"  # daily | 5m | 15m
    temperature: float = 0.7
    top_p: float = 0.9
    sample_count: int = 10


# ── Future-timestamp generation ──────────────────────────────────────────


def _next_weekday(ts: pd.Timestamp) -> pd.Timestamp:
    while ts.weekday() >= 5:  # Sat=5, Sun=6
        ts = ts + pd.Timedelta(days=1)
    return ts


def _future_timestamps(x_ts: pd.Series, pred_len: int, interval: str) -> pd.Series:
    """Generate ``pred_len`` future timestamps consistent with the input cadence.

    Daily -> business days. Intraday -> reconstruct the session slot grid from
    the supplied history so forecast bars land on real trading times and skip
    overnight/weekend gaps.
    """
    times = pd.DatetimeIndex(pd.to_datetime(x_ts))
    last = times[-1]
    if interval == "daily":
        return pd.Series(pd.bdate_range(start=last + pd.Timedelta(days=1), periods=pred_len))

    # Intraday: derive the ordered intraday slots (time-of-day) from history.
    slots = sorted({(int(t.hour), int(t.minute)) for t in times})
    if len(slots) < 2:
        step = 5 if interval == "5m" else 15
        return pd.Series([last + pd.Timedelta(minutes=step * (i + 1)) for i in range(pred_len)])

    last_slot = (int(last.hour), int(last.minute))
    if last_slot in slots:
        idx = slots.index(last_slot)
    else:
        idx = 0
        for j, hm in enumerate(slots):
            if hm <= last_slot:
                idx = j
    cur_date = last.normalize()
    out: list[pd.Timestamp] = []
    slot_i = idx
    for _ in range(pred_len):
        slot_i += 1
        if slot_i >= len(slots):
            slot_i = 0
            cur_date = _next_weekday(cur_date + pd.Timedelta(days=1))
        h, m = slots[slot_i]
        out.append(cur_date + pd.Timedelta(hours=h, minutes=m))
    return pd.Series(out)


# ── Distribution inference (per-sample, no averaging) ────────────────────


def _inference_samples(
    x_norm: np.ndarray,
    x_stamp: np.ndarray,
    y_stamp: np.ndarray,
    pred_len: int,
    temperature: float,
    top_p: float,
    sample_count: int,
) -> np.ndarray:
    """Adapted from Kronos' auto_regressive_inference, but returns *every*
    sampled path instead of the mean.

    Returns array shaped [sample_count, pred_len, n_features].
    """
    import torch
    from model.kronos import sample_from_logits

    tokenizer = _PREDICTOR.tokenizer
    model = _PREDICTOR.model
    device = _PREDICTOR.device
    max_context = _PREDICTOR.max_context
    clip = _PREDICTOR.clip
    top_k = 0

    with torch.no_grad():
        x = torch.from_numpy(x_norm.astype(np.float32))[None, :].to(device)
        xs = torch.from_numpy(x_stamp.astype(np.float32))[None, :].to(device)
        ys = torch.from_numpy(y_stamp.astype(np.float32))[None, :].to(device)
        x = torch.clip(x, -clip, clip)

        x = x.repeat(sample_count, 1, 1)
        xs = xs.repeat(sample_count, 1, 1)
        ys = ys.repeat(sample_count, 1, 1)

        x_token = tokenizer.encode(x, half=True)
        initial_seq_len = x.size(1)
        batch_size = x_token[0].size(0)
        total_seq_len = initial_seq_len + pred_len
        full_stamp = torch.cat([xs, ys], dim=1)

        generated_pre = x_token[0].new_empty(batch_size, pred_len)
        generated_post = x_token[1].new_empty(batch_size, pred_len)
        pre_buffer = x_token[0].new_zeros(batch_size, max_context)
        post_buffer = x_token[1].new_zeros(batch_size, max_context)
        buffer_len = min(initial_seq_len, max_context)
        if buffer_len > 0:
            start_idx = max(0, initial_seq_len - max_context)
            pre_buffer[:, :buffer_len] = x_token[0][:, start_idx : start_idx + buffer_len]
            post_buffer[:, :buffer_len] = x_token[1][:, start_idx : start_idx + buffer_len]

        for i in range(pred_len):
            current_seq_len = initial_seq_len + i
            window_len = min(current_seq_len, max_context)
            if current_seq_len <= max_context:
                input_tokens = [pre_buffer[:, :window_len], post_buffer[:, :window_len]]
            else:
                input_tokens = [pre_buffer, post_buffer]
            context_end = current_seq_len
            context_start = max(0, context_end - max_context)
            current_stamp = full_stamp[:, context_start:context_end, :].contiguous()

            s1_logits, context = model.decode_s1(input_tokens[0], input_tokens[1], current_stamp)
            s1_logits = s1_logits[:, -1, :]
            sample_pre = sample_from_logits(s1_logits, temperature=temperature, top_k=top_k, top_p=top_p, sample_logits=True)
            s2_logits = model.decode_s2(context, sample_pre)
            s2_logits = s2_logits[:, -1, :]
            sample_post = sample_from_logits(s2_logits, temperature=temperature, top_k=top_k, top_p=top_p, sample_logits=True)

            generated_pre[:, i] = sample_pre.squeeze(-1)
            generated_post[:, i] = sample_post.squeeze(-1)
            if current_seq_len < max_context:
                pre_buffer[:, current_seq_len] = sample_pre.squeeze(-1)
                post_buffer[:, current_seq_len] = sample_post.squeeze(-1)
            else:
                pre_buffer.copy_(torch.roll(pre_buffer, shifts=-1, dims=1))
                post_buffer.copy_(torch.roll(post_buffer, shifts=-1, dims=1))
                pre_buffer[:, -1] = sample_pre.squeeze(-1)
                post_buffer[:, -1] = sample_post.squeeze(-1)

        full_pre = torch.cat([x_token[0], generated_pre], dim=1)
        full_post = torch.cat([x_token[1], generated_post], dim=1)
        context_start = max(0, total_seq_len - max_context)
        input_tokens = [
            full_pre[:, context_start:total_seq_len].contiguous(),
            full_post[:, context_start:total_seq_len].contiguous(),
        ]
        z = tokenizer.decode(input_tokens, half=True)
        preds = z.cpu().numpy()  # [sample_count, total_seq_len, feat]
    return preds[:, -pred_len:, :]


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
        from model.kronos import calc_time_stamps

        lookback = max(32, min(int(req.lookback or 256), MAX_CONTEXT))
        rows = req.ohlcv[-lookback:]
        interval = req.interval if req.interval in ("daily", "5m", "15m") else "daily"
        pred_len = max(1, min(int(req.pred_len or 24), 120))
        sample_count = max(1, min(int(req.sample_count or 10), 64))

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
        df[_AMT_COL] = df[_VOL_COL] * df[_PRICE_COLS].mean(axis=1)
        x_ts = pd.Series(pd.to_datetime([c.time for c in rows], unit="s"))
        y_ts = _future_timestamps(x_ts, pred_len, interval)

        # Normalize exactly as KronosPredictor.predict does.
        feat_cols = _PRICE_COLS + [_VOL_COL, _AMT_COL]
        x = df[feat_cols].to_numpy(dtype=np.float32)
        x_mean, x_std = np.mean(x, axis=0), np.std(x, axis=0)
        x_norm = np.clip((x - x_mean) / (x_std + 1e-5), -_PREDICTOR.clip, _PREDICTOR.clip)
        x_stamp = calc_time_stamps(x_ts).to_numpy(dtype=np.float32)
        y_stamp = calc_time_stamps(y_ts).to_numpy(dtype=np.float32)

        samples = _inference_samples(
            x_norm, x_stamp, y_stamp, pred_len, float(req.temperature or 0.7), float(req.top_p or 0.9), sample_count
        )
        # Denormalize each sampled path.
        samples = samples * (x_std + 1e-5) + x_mean  # [sample_count, pred_len, feat]

        last_close = float(df["close"].iloc[-1])
        close_samples = samples[:, :, _CLOSE_IDX]  # [sample_count, pred_len]

        p10 = np.percentile(close_samples, 10, axis=0)
        p50 = np.percentile(close_samples, 50, axis=0)
        p90 = np.percentile(close_samples, 90, axis=0)
        med = np.percentile(samples, 50, axis=0)  # [pred_len, feat] median OHLC

        epochs = [int(pd.Timestamp(t).timestamp()) for t in y_ts]
        median_candles = [
            {
                "time": epochs[i],
                "open": round(float(med[i, 0]), 4),
                "high": round(float(med[i, 1]), 4),
                "low": round(float(med[i, 2]), 4),
                "close": round(float(med[i, 3]), 4),
            }
            for i in range(pred_len)
        ]
        band = [
            {"time": epochs[i], "lower": round(float(p10[i]), 4), "upper": round(float(p90[i]), 4)}
            for i in range(pred_len)
        ]

        final_samples = close_samples[:, -1]
        median_final = float(p50[-1])
        prob_up = float(np.mean(final_samples > last_close)) if last_close else 0.0
        exp_ret_pct = ((median_final - last_close) / last_close * 100.0) if last_close else 0.0
        if exp_ret_pct > 0.1:
            direction = "up"
        elif exp_ret_pct < -0.1:
            direction = "down"
        else:
            direction = "flat"
        # Confidence = directional consensus strength across samples.
        confidence = round(float(max(prob_up, 1.0 - prob_up)), 3)

        return {
            "ok": True,
            "data": {
                "symbol": req.symbol,
                "model_id": MODEL_ID,
                "interval": interval,
                "pred_len": pred_len,
                "lookback": len(rows),
                "sample_count": sample_count,
                "last_close": round(last_close, 4),
                "median_final_close": round(median_final, 4),
                "expected_return_pct": round(exp_ret_pct, 4),
                "direction": direction,
                "prob_up": round(prob_up, 4),
                "confidence": confidence,
                "median_candles": median_candles,
                "forecast_candles": median_candles,  # back-compat alias
                "band": band,
                "baseline": "random_walk_flat",
            },
        }
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Prediction failed for %s: %s", req.symbol, exc)
        return {"ok": False, "error": f"prediction_failed: {exc}"}
