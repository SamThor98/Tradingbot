/**
 * Kronos forecast workspace (dedicated "Kronos" tab).
 *
 * Owns the standalone forecast experience: a ticker/horizon form, an on-demand
 * call to `/api/forecast/{ticker}`, a history + forecast candlestick chart, and
 * an accessible summary. Reuses the shared summary builders from forecast.js.
 *
 * Self-contained: imports its own `api` client and reads the global
 * `LightweightCharts` (loaded via CDN in index.html). Degrades gracefully when
 * the inference service is offline.
 */

import { api } from "../modules/api.js";
import { safeText } from "../modules/format.js";
import { buildForecastSummary, buildForecastUnavailable } from "./forecast.js";

let _kronosChart = null;
let _kronosResizeObserver = null;
let _kronosInited = false;
let _kronosServiceProbed = false;

function chartWidth(container) {
  if (!container) return 320;
  const measured = Math.round(container.getBoundingClientRect().width || container.clientWidth || 0);
  const viewportCap = Math.max(240, Math.round((window.innerWidth || 0) - 96));
  const fallback = Math.min(720, viewportCap);
  const safe = measured > 0 ? measured : fallback;
  return Math.max(240, Math.min(safe, viewportCap));
}

function forecastCandles(data) {
  if (Array.isArray(data?.median_candles) && data.median_candles.length) return data.median_candles;
  if (Array.isArray(data?.forecast_candles) && data.forecast_candles.length) return data.forecast_candles;
  return [];
}

function toCandlestickPoint(candle) {
  const open = Number(candle?.open);
  const high = Number(candle?.high);
  const low = Number(candle?.low);
  const close = Number(candle?.close);
  const time = candle?.time;
  if (time == null || !Number.isFinite(open) || !Number.isFinite(high) || !Number.isFinite(low) || !Number.isFinite(close)) {
    return null;
  }
  return {
    time,
    open,
    high: Math.max(high, open, close, low),
    low: Math.min(low, open, close, high),
    close,
  };
}

function renderKronosChart(container, data) {
  if (!container) return;
  if (_kronosResizeObserver) {
    _kronosResizeObserver.disconnect();
    _kronosResizeObserver = null;
  }
  if (_kronosChart) {
    try {
      _kronosChart.remove();
    } catch {
      // ignore cleanup failures
    }
    _kronosChart = null;
  }
  if (typeof LightweightCharts === "undefined") {
    container.innerHTML = '<p class="muted">Chart library unavailable.</p>';
    return;
  }
  const history = Array.isArray(data?.history_candles) ? data.history_candles : [];
  if (!history.length) {
    container.classList.remove("kronos-chart-active");
    container.innerHTML = '<p class="muted">No price history available for this symbol.</p>';
    return;
  }
  const forecast = forecastCandles(data)
    .map(toCandlestickPoint)
    .filter(Boolean);
  const lastHistTime = history[history.length - 1]?.time;
  const lastHistEpoch = Number(lastHistTime);
  const forward = Number.isFinite(lastHistEpoch)
    ? forecast.filter((c) => Number(c.time) > lastHistEpoch)
    : forecast;
  const band = Array.isArray(data?.band) ? data.band : [];
  const intraday = safeText(data?.interval) !== "daily" && safeText(data?.interval) !== "";

  container.classList.add("kronos-chart-active");
  container.innerHTML = "";
  const chart = LightweightCharts.createChart(container, {
    width: chartWidth(container),
    height: 340,
    layout: { background: { type: "solid", color: "transparent" }, textColor: "#94a3b8" },
    grid: {
      vertLines: { color: "rgba(148,163,184,0.10)" },
      horzLines: { color: "rgba(148,163,184,0.10)" },
    },
    rightPriceScale: { borderColor: "rgba(148,163,184,0.22)" },
    timeScale: { borderColor: "rgba(148,163,184,0.22)", timeVisible: intraday, secondsVisible: false },
  });

  const histSeries = chart.addCandlestickSeries({
    upColor: "#2d8a5f",
    downColor: "#c94949",
    borderUpColor: "#2d8a5f",
    borderDownColor: "#c94949",
    wickUpColor: "#2d8a5f",
    wickDownColor: "#c94949",
  });
  histSeries.setData(history);

  // p10/p90 cone as two thin lines (Lightweight Charts has no native band fill).
  if (band.length) {
    try {
      const upperPoints = band
        .map((b) => ({ time: b.time, value: Number(b.upper) }))
        .filter((p) => p.time != null && Number.isFinite(p.value));
      const lowerPoints = band
        .map((b) => ({ time: b.time, value: Number(b.lower) }))
        .filter((p) => p.time != null && Number.isFinite(p.value));
      if (upperPoints.length && lowerPoints.length) {
        const upper = chart.addLineSeries({
          color: "rgba(46,110,170,0.55)",
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        const lower = chart.addLineSeries({
          color: "rgba(46,110,170,0.55)",
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        upper.setData(upperPoints);
        lower.setData(lowerPoints);
      }
    } catch {
      // band overlay is best-effort; projected candles still render
    }
  }
  // Median forecast path as translucent candlesticks (matches scan-detail overlay).
  if (forward.length) {
    try {
      const fcSeries = chart.addCandlestickSeries({
        upColor: "rgba(46,110,170,0.55)",
        downColor: "rgba(150,90,170,0.55)",
        borderUpColor: "#2e6eaa",
        borderDownColor: "#965aaa",
        wickUpColor: "#2e6eaa",
        wickDownColor: "#965aaa",
      });
      fcSeries.setData(forward);
    } catch {
      // fallback to close line if candle payload is rejected by the chart library
      try {
        const med = chart.addLineSeries({ color: "#2e6eaa", lineWidth: 2, priceLineVisible: false });
        med.setData(forward.map((c) => ({ time: c.time, value: c.close })));
      } catch {
        // summary still renders when overlay fails
      }
    }
  }

  chart.timeScale().fitContent();
  _kronosChart = chart;
  _kronosResizeObserver = new ResizeObserver(() => {
    if (_kronosChart) _kronosChart.applyOptions({ width: chartWidth(container) });
  });
  _kronosResizeObserver.observe(container);
}

async function runKronosForecast() {
  const input = document.getElementById("kronosTickerInput");
  const horizonSel = document.getElementById("kronosHorizonSelect");
  const intervalSel = document.getElementById("kronosIntervalSelect");
  const btn = document.getElementById("kronosRunBtn");
  const summary = document.getElementById("kronosForecastSummary");
  const container = document.getElementById("kronosChartContainer");
  const ticker = (input?.value || "").trim().toUpperCase();
  if (!ticker) {
    input?.focus();
    if (summary) summary.innerHTML = buildForecastUnavailable("Enter a ticker symbol first.");
    return;
  }
  const horizon = parseInt(horizonSel?.value || "24", 10) || 24;
  const interval = intervalSel?.value || "daily";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Forecasting…";
  }
  if (summary) {
    summary.innerHTML = `<p class="muted">Requesting Kronos forecast for ${ticker} (${interval})… base model on CPU can take up to ~90s.</p>`;
  }
  if (container) {
    container.classList.remove("kronos-chart-active");
    container.innerHTML = '<p class="muted">Loading chart…</p>';
  }
  try {
    const qs = `pred_len=${horizon}&interval=${encodeURIComponent(interval)}`;
    // base + steady sampling on CPU can take ~90s; allow generous headroom.
    const out = await api.get(`/api/forecast/${encodeURIComponent(ticker)}?${qs}`, { timeoutMs: 150000 });
    const data = out?.data || {};
    if (!out.ok || !data.direction) {
      if (summary) summary.innerHTML = buildForecastUnavailable(out.error || "Kronos forecast unavailable.");
      renderKronosChart(container, data);
      return;
    }
    if (summary) summary.innerHTML = buildForecastSummary(data);
    renderKronosChart(container, data);
  } catch (err) {
    if (summary) summary.innerHTML = buildForecastUnavailable(`Forecast error: ${err}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Run forecast";
    }
  }
}

/** Probe the Kronos service once and render a small status line. */
export async function primeKronosWorkspace() {
  if (_kronosServiceProbed) return;
  _kronosServiceProbed = true;
  const statusEl = document.getElementById("kronosServiceStatus");
  if (!statusEl) return;
  try {
    const out = await api.get("/api/health/deep");
    const k = out?.data?.kronos;
    if (!out.ok || !k) {
      statusEl.textContent = "";
      return;
    }
    const mode = String(k.mode || "off");
    let cls = "kronos-status-neutral";
    let text = `Scanner mode: ${mode}.`;
    if (k.service_ok === true) {
      cls = "kronos-status-ok";
      text = `Inference service online · model ${k.model_id || "kronos"} · scanner mode ${mode}.`;
    } else if (k.service_ok === false) {
      cls = "kronos-status-warn";
      text = "Inference service offline — on-demand forecasts may be unavailable.";
    } else {
      text = `On-demand forecasts ready · scanner mode ${mode}.`;
    }
    statusEl.className = `kronos-service-status ${cls}`;
    statusEl.textContent = text;
  } catch {
    statusEl.textContent = "";
  }
}

/** Wire the forecast form once. Safe to call multiple times. */
export function initKronosWorkspace() {
  if (_kronosInited) return;
  const form = document.getElementById("kronosForecastForm");
  if (!form) return;
  _kronosInited = true;
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    void runKronosForecast();
  });
}
