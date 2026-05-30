# Kronos Inference Service

Standalone microservice that serves forecasts from the open-source
[Kronos](https://github.com/shiyu-coder/Kronos) foundation model for financial
candlesticks (MIT licensed). It is deliberately isolated from the main
TradingBot webapp so the heavy `torch` / `transformers` / Hugging Face weight
footprint stays out of the lean web and worker images.

The main app calls this service over HTTP through
[`schwab_skill/kronos_client.py`](../schwab_skill/kronos_client.py).

## Provenance

- Upstream repo: `shiyu-coder/Kronos` (MIT). The `model/` package is vendored at
  build time via `git clone` (pin a commit with the `KRONOS_REF` build arg for
  reproducibility).
- Default checkpoints (Hugging Face Hub):
  - Model: `NeoQuasar/Kronos-small` (24.7M params, 512-bar context)
  - Tokenizer: `NeoQuasar/Kronos-Tokenizer-base`
  - Upgrade to `NeoQuasar/Kronos-base` via `KRONOS_MODEL_ID` for more accuracy.

## API

- `GET /health` -> `{ ok, loaded, model_id, tokenizer_id, device, error }`
- `POST /predict`
  - Request: `{ symbol, ohlcv: [{time, open, high, low, close, volume}], pred_len, lookback, temperature, top_p, sample_count }`
  - Response: `{ ok, data: { direction, expected_return_pct, confidence, forecast_candles, ... } }`

## Local development (no Docker)

```bash
cd kronos_service
python fetch_model_code.py          # clones the Kronos model/ package here
pip install torch                   # CPU wheel
pip install -r requirements.txt
uvicorn app:app --port 8100
```

Then point the main app at it:

```
KRONOS_INFERENCE_URL=http://localhost:8100
KRONOS_MODE=shadow
```

## Docker

```bash
docker build -t kronos-service .
docker run -p 8100:8100 kronos-service
```

## Environment variables

| Var | Default | Meaning |
|-----|---------|---------|
| `KRONOS_MODEL_ID` | `NeoQuasar/Kronos-small` | HF model id |
| `KRONOS_TOKENIZER_ID` | `NeoQuasar/Kronos-Tokenizer-base` | HF tokenizer id |
| `KRONOS_DEVICE` | `cpu` | `cpu`, `cuda:0`, or `mps` |
| `KRONOS_MAX_CONTEXT` | `512` | Max input bars fed to the model |
