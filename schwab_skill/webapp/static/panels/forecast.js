/**
 * Kronos forecast panel helpers.
 *
 * Pure rendering utilities for the on-demand Kronos K-line forecast shown in
 * the scan-detail lane. The chart overlay itself is managed in app.js (which
 * owns the Lightweight Charts instance); this module builds the summary HTML
 * and small formatting helpers so the markup stays in one place.
 */

import { escapeHtml, safeText } from "../modules/format.js";

function directionLabel(dir) {
  const d = safeText(dir).toLowerCase();
  if (d === "up") return { text: "Bullish", cls: "forecast-up" };
  if (d === "down") return { text: "Bearish", cls: "forecast-down" };
  return { text: "Neutral", cls: "forecast-flat" };
}

function confidenceLabel(bucket) {
  const b = safeText(bucket).toLowerCase();
  if (b === "high") return "High";
  if (b === "medium") return "Medium";
  if (b === "low") return "Low";
  return "—";
}

/** Build the forecast summary markup from an /api/forecast payload. */
export function buildForecastSummary(data) {
  if (!data) return '<p class="muted">No forecast data.</p>';
  const dir = directionLabel(data.direction);
  const ret = Number(data.expected_return_pct);
  const retText = Number.isFinite(ret) ? `${ret >= 0 ? "+" : ""}${ret.toFixed(2)}%` : "—";
  const conf = confidenceLabel(data.confidence_bucket);
  const confPct = Number.isFinite(Number(data.confidence))
    ? `${Math.round(Number(data.confidence) * 100)}%`
    : "—";
  const probUp = Number.isFinite(Number(data.prob_up))
    ? `${Math.round(Number(data.prob_up) * 100)}%`
    : "—";
  const horizon = Number(data.pred_len) || 0;
  const intervalRaw = safeText(data.interval || "daily");
  const unit = intervalRaw === "daily" ? "d" : ` ${intervalRaw} bars`;
  const horizonLabel = intervalRaw === "daily" ? `${horizon}d` : `${horizon}${unit}`;
  const model = escapeHtml(safeText(data.model_version || "kronos"));
  const provider = escapeHtml(safeText(data.provider || ""));
  return `
    <div class="forecast-summary">
      <div class="forecast-metric ${dir.cls}">
        <span class="forecast-metric-label">Direction</span>
        <strong>${dir.text}</strong>
      </div>
      <div class="forecast-metric">
        <span class="forecast-metric-label">Expected move (${escapeHtml(horizonLabel)})</span>
        <strong>${retText}</strong>
      </div>
      <div class="forecast-metric ${dir.cls}">
        <span class="forecast-metric-label">P(up) vs flat</span>
        <strong>${probUp}</strong>
      </div>
      <div class="forecast-metric">
        <span class="forecast-metric-label">Consensus</span>
        <strong>${conf} (${confPct})</strong>
      </div>
      <p class="forecast-foot muted">
        Model ${model}${provider ? ` · data ${provider}` : ""} · median path with p10-p90 cone ·
        advisory only, not a trade signal.
      </p>
    </div>
  `;
}

/** Build a degraded/unavailable message for the forecast area. */
export function buildForecastUnavailable(message) {
  const msg = escapeHtml(safeText(message || "Kronos forecast unavailable."));
  return `<p class="muted forecast-degraded">${msg}</p>`;
}
