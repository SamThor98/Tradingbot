/**
 * Scan-detail chart overlays — SMA levels, breakout reference, setup badges.
 * Uses fields on the scan signal row plus OHLCV candles (no extra API calls).
 */

import { safeText, safeNum, escapeHtml } from "./format.js";
import { isScanSignalStageable } from "./signalProvenance.js";
import { formatFilterReasons } from "./filterReasons.js";

/** @typedef {{ price: number, color: string, title: string, lineStyle?: number }} OverlayLine */

/**
 * @param {Array<{ high?: number }>} candles
 * @returns {number|null}
 */
export function computePriorHigh(candles) {
  if (!Array.isArray(candles) || candles.length < 2) return null;
  const prior = Number(candles[candles.length - 2]?.high);
  return Number.isFinite(prior) && prior > 0 ? prior : null;
}

/**
 * @param {object} signal
 * @param {Array<{ high?: number }>} candles
 * @returns {{ sma50: number|null, sma200: number|null, breakout: number|null, stop: number|null }}
 */
export function extractSignalOverlayLevels(signal = {}, candles = []) {
  const sma50 = safeNum(signal.sma_50, NaN);
  const sma200 = safeNum(signal.sma_200, NaN);
  const price = safeNum(signal.price ?? signal.current_price, NaN);
  const breakout = computePriorHigh(candles);
  let stop = null;
  if (Number.isFinite(sma200) && sma200 > 0) {
    stop = sma200 * 0.97;
  } else if (Number.isFinite(price) && price > 0) {
    stop = price * 0.93;
  }
  return {
    sma50: Number.isFinite(sma50) && sma50 > 0 ? sma50 : null,
    sma200: Number.isFinite(sma200) && sma200 > 0 ? sma200 : null,
    breakout,
    stop: stop && stop > 0 ? stop : null,
  };
}

/**
 * @param {import('lightweight-charts').ISeriesApi<'Candlestick'>} candleSeries
 * @param {OverlayLine[]} lines
 * @returns {() => void}
 */
export function applyPriceLines(candleSeries, lines) {
  const handles = [];
  lines.forEach((line) => {
    if (!Number.isFinite(line.price) || line.price <= 0) return;
    try {
      handles.push(
        candleSeries.createPriceLine({
          price: line.price,
          color: line.color,
          lineWidth: 1,
          lineStyle: line.lineStyle ?? 2,
          axisLabelVisible: true,
          title: line.title,
        }),
      );
    } catch {
      // best-effort overlay
    }
  });
  return () => {
    handles.forEach((handle) => {
      try {
        candleSeries.removePriceLine(handle);
      } catch {
        // ignore cleanup failures
      }
    });
  };
}

/**
 * @param {object} chart LightweightCharts.IChartApi
 * @param {object} candleSeries
 * @param {object|null} signal
 * @param {Array<object>} candles
 * @returns {() => void}
 */
export function applyScanDetailOverlays(chart, candleSeries, signal, candles) {
  if (!chart || !candleSeries || !signal) return () => {};
  const levels = extractSignalOverlayLevels(signal, candles);
  const lines = [];
  if (levels.sma50 != null) {
    lines.push({ price: levels.sma50, color: "rgba(45, 90, 74, 0.85)", title: "SMA 50", lineStyle: 2 });
  }
  if (levels.sma200 != null) {
    lines.push({ price: levels.sma200, color: "rgba(26, 58, 46, 0.75)", title: "SMA 200", lineStyle: 2 });
  }
  if (levels.breakout != null) {
    lines.push({ price: levels.breakout, color: "rgba(201, 73, 73, 0.8)", title: "Breakout", lineStyle: 0 });
  }
  if (levels.stop != null) {
    lines.push({ price: levels.stop, color: "rgba(180, 120, 40, 0.75)", title: "Stop ref", lineStyle: 3 });
  }
  return applyPriceLines(candleSeries, lines);
}

/**
 * @param {object|null} signal
 * @param {{ scanBlocked?: boolean }} [opts]
 * @returns {string}
 */
export function renderChartSetupBadges(signal, opts = {}) {
  if (!signal) return "";
  const stageable = isScanSignalStageable(signal);
  const breakoutOk = signal.breakout_confirmed === true;
  const regimeBlocked = Boolean(opts.scanBlocked);
  const reasons = formatFilterReasons(signal._filter_reasons);
  const primaryReason = reasons[0] || safeText(signal._filter_status || "").replace(/_/g, " ");
  const badges = [
    `<span class="pill ${stageable ? "good" : "bad"}">${stageable ? "Stageable" : "Blocked"}</span>`,
    `<span class="pill ${breakoutOk ? "good" : "warn"}">Breakout ${breakoutOk ? "confirmed" : "unconfirmed"}</span>`,
    `<span class="pill ${regimeBlocked ? "bad" : "good"}">Regime ${regimeBlocked ? "blocked" : "open"}</span>`,
  ];
  if (!stageable && primaryReason) {
    badges.push(
      `<span class="pill bad scan-overlay-reason" title="${escapeHtml(reasons.join("; "))}">${escapeHtml(primaryReason)}</span>`,
    );
  }
  return `<div class="scan-chart-badge-row" role="list">${badges.join("")}</div>`;
}

/**
 * @param {HTMLElement} container
 * @param {object|null} signal
 * @param {{ scanBlocked?: boolean }} [opts]
 */
export function renderChartOverlayLegend(container, signal, opts = {}) {
  if (!container) return;
  const existing = container.querySelector(".scan-chart-legend");
  if (existing) existing.remove();
  if (!signal) return;
  const legend = document.createElement("div");
  legend.className = "scan-chart-legend";
  legend.innerHTML = renderChartSetupBadges(signal, opts);
  container.appendChild(legend);
}
