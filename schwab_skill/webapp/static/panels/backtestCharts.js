/**
 * Backtest equity + drawdown charts (Phase 3).
 * Renders LightweightCharts area/line series from backtest result curves.
 */

import { getLightweightChartsProps } from "../modules/chartThemeAdapters.js";
import { safeText } from "../modules/format.js";

let _equityChart = null;
let _drawdownChart = null;
let _resizeObserver = null;

function disposeCharts() {
  try {
    _resizeObserver?.disconnect();
  } catch {
    /* ignore */
  }
  _resizeObserver = null;
  try {
    _equityChart?.remove();
  } catch {
    /* ignore */
  }
  try {
    _drawdownChart?.remove();
  } catch {
    /* ignore */
  }
  _equityChart = null;
  _drawdownChart = null;
}

function toChartTime(dateStr) {
  const raw = safeText(dateStr);
  if (!raw) return null;
  const day = raw.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : null;
}

function normalizeCurve(rows, valueKey) {
  const out = [];
  for (const row of rows || []) {
    if (!row || typeof row !== "object") continue;
    const time = toChartTime(row.date);
    const value = Number(row[valueKey]);
    if (!time || !Number.isFinite(value)) continue;
    out.push({ time, value });
  }
  return out.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
}

/**
 * @param {HTMLElement|null} wrap
 * @param {object|null} result
 */
export function renderBacktestEquityCharts(wrap, result) {
  const equityHost = document.getElementById("btEquityChart");
  const ddHost = document.getElementById("btDrawdownChart");
  if (!wrap || !equityHost || !ddHost) return;

  disposeCharts();
  equityHost.innerHTML = "";
  ddHost.innerHTML = "";

  const equityRows = normalizeCurve(result?.equity_curve, "equity");
  const ddRows = normalizeCurve(result?.drawdown_curve, "drawdown_pct");
  if (!equityRows.length) {
    wrap.classList.add("hidden");
    wrap.setAttribute("aria-hidden", "true");
    return;
  }

  wrap.classList.remove("hidden");
  wrap.setAttribute("aria-hidden", "false");

  if (typeof LightweightCharts === "undefined") {
    equityHost.innerHTML = `<p class="muted">Chart library unavailable.</p>`;
    return;
  }

  const theme = getLightweightChartsProps();
  const width = () => Math.max(280, wrap.clientWidth || 320);

  _equityChart = LightweightCharts.createChart(equityHost, {
    width: width(),
    height: 180,
    layout: theme.layout,
    grid: theme.grid,
    rightPriceScale: theme.rightPriceScale,
    timeScale: { ...theme.timeScale, timeVisible: false },
  });
  const equitySeries = _equityChart.addAreaSeries({
    lineColor: theme.candlestick.upColor,
    topColor: theme.volumeColors.up,
    bottomColor: "transparent",
    lineWidth: 2,
  });
  equitySeries.setData(equityRows);
  _equityChart.timeScale().fitContent();

  if (ddRows.length) {
    _drawdownChart = LightweightCharts.createChart(ddHost, {
      width: width(),
      height: 90,
      layout: theme.layout,
      grid: theme.grid,
      rightPriceScale: { ...theme.rightPriceScale, invertScale: false },
      timeScale: { ...theme.timeScale, timeVisible: true },
    });
    const ddSeries = _drawdownChart.addLineSeries({
      color: theme.candlestick.downColor,
      lineWidth: 2,
    });
    ddSeries.setData(ddRows);
    _drawdownChart.timeScale().fitContent();
  }

  _resizeObserver = new ResizeObserver(() => {
    const w = width();
    _equityChart?.applyOptions({ width: w });
    _drawdownChart?.applyOptions({ width: w });
  });
  _resizeObserver.observe(wrap);
}

export function clearBacktestEquityCharts() {
  const wrap = document.getElementById("btEquityChartWrap");
  if (wrap) {
    wrap.classList.add("hidden");
    wrap.setAttribute("aria-hidden", "true");
  }
  disposeCharts();
  document.getElementById("btEquityChart")?.replaceChildren();
  document.getElementById("btDrawdownChart")?.replaceChildren();
}
