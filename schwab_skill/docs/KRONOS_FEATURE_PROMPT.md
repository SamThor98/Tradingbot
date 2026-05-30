# Kronos Forecast Feature — High-Level Implementation Prompt

> A reusable, high-level brief for implementing (or re-implementing / extending)
> the Kronos forecasting capability in the TradingBot webapp. Hand this to an
> engineer or coding agent as the source-of-truth spec.

## Goal

Incorporate the open-source [Kronos](https://github.com/shiyu-coder/Kronos)
foundation model for financial candlesticks (K-lines) into the TradingBot
product as an **advisory forecasting capability** — surfaced both as a dedicated
"Kronos" tab in the dashboard and as a shadow signal inside the scanner —
without bloating the lean web/worker images and without ever placing or
modifying orders.

## Principles (non-negotiable)

- **Advisory only.** Forecasts never trigger or alter trades. The scanner uses
  Kronos behind the `OFF → SHADOW → LIVE` rollout pattern; LIVE applies only a
  small, clamped score nudge gated by regime + confidence.
- **Isolation.** All `torch` / `transformers` / Hugging Face weights live in a
  separate inference microservice. The main app stays torch-free and talks to
  it over HTTP.
- **Degrade safely.** Any failure (service offline, timeout, bad payload) yields
  a clear status/`None`, never an exception into the pipeline or a broken UI.
- **Reuse existing seams.** OHLCV input comes from
  `market_data.get_daily_history_with_meta()`. Config follows `config.py`
  getter conventions. API responses use the `ApiResponse(ok, data, error)`
  envelope. UI follows the screen/tab + `*-surface` visibility pattern.

## Architecture

```
Main webapp (lean, no torch)                 Kronos service (isolated, heavy)
  Kronos tab  ─┐
  /api/forecast/{ticker} ─┼─ kronos_client.py ──HTTP──> POST /predict ─> KronosPredictor
  Stage B scanner scoring ┘                                   ▲
  config.py KRONOS_* getters                                  │
  market_data.get_daily_history_with_meta()  ── OHLCV input ──┘
```

## Components

1. **Inference microservice** (`kronos_service/`)
   - FastAPI `GET /health` + `POST /predict`. Loads `KronosPredictor` once.
   - Input: `{ symbol, ohlcv:[{time,open,high,low,close,volume}], pred_len, lookback }`.
   - Output: forecast candles + `direction`, `expected_return_pct`, `confidence`.
   - Vendors the Kronos `model/` package at a pinned ref; pre-downloads weights.
   - Default checkpoints: `NeoQuasar/Kronos-small` + `NeoQuasar/Kronos-Tokenizer-base`.

2. **Backend client + config** (`schwab_skill/`)
   - `config.py`: `get_kronos_mode` (off/shadow/live) + `inference_url`,
     `model_id`, `lookback_bars`, `pred_len`, `max_symbols`, `timeout_s`,
     confidence thresholds, score-delta clamp.
   - `kronos_client.py`: torch-free HTTP client returning a `KronosForecast`
     dataclass (or `None` on failure). Scanner wrapper `forecast_signal_kronos`.

3. **On-demand API** (`webapp/routes/research.py`)
   - `GET /api/forecast/{ticker}` → `{ history_candles, forecast_candles,
     direction, expected_return_pct, confidence, confidence_bucket,
     scanner_mode, provider }`. Works at any scanner mode; returns a degraded
     payload when the service is offline.

4. **Scanner integration** (`signal_scanner.py`)
   - Budget-capped Kronos block in Stage B (after the advisory hook). SHADOW
     attaches `signal_row["kronos_forecast"]`; LIVE applies a clamped score
     nudge gated by regime + high confidence. Diagnostics counters + a `kronos`
     summary block.

5. **Dedicated UI tab** (`webapp/static/`)
   - A 3rd top-level tab "Kronos" (after Research, before Diagnostics) using the
     existing `SCREEN_MODES` / `SCREEN_SECTIONS` / `body.ui-screen-*` system.
   - Sections: workspace intro, forecast workspace (ticker + horizon form, run
     button, service-status line, history+forecast chart, summary metrics), and
     an "How Kronos works" explainer.
   - Logic in `panels/kronosWorkspace.js`; summary markup reused from
     `panels/forecast.js`. Router alias `kronos` / `forecast`.

## UX & Accessibility requirements

- Tab uses `role="tab"` / `aria-selected` / `aria-controls`; arrow-key + Home/End
  roving tabindex; `Ctrl/Cmd+3` jumps to Kronos.
- Form: real `<label for>` on every control, `required` on ticker, `aria-describedby`
  hint, submit-on-Enter, visible busy state on the run button.
- Live regions: forecast summary + service status use `aria-live="polite"`;
  the chart container has a descriptive `aria-label`.
- Clear empty/degraded states with human-readable copy (no raw stack traces).
- Works across Simple/Standard/Pro display modes (core forecast card is not
  `section-advanced`; the explainer may be).

## Rollout

- Ship scanner integration at `KRONOS_MODE=shadow`. Promote to `live` only after
  shadow data shows edge, mirroring the advisory / prediction-market discipline.
- Deploy the service as a private Render service; wire `KRONOS_INFERENCE_URL`
  into web + worker via internal hostname.

## Acceptance criteria

- `ruff check` clean; new Python type-clean under mypy; degraded-mode unit test
  passes (service down → `None`, no raise).
- Kronos tab appears, is keyboard-navigable, and is hidden on the other four
  tabs (and vice versa) with no empty leftover wrappers.
- `/api/forecast/AAPL` returns history + forecast candles when the service is
  up, and a clear degraded payload when it is down; the workspace renders both.
- Scanner in shadow mode attaches `kronos_forecast` to signals and increments
  `diagnostics.kronos.*` without changing ranking.
