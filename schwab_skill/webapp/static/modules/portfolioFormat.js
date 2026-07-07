/**
 * Shared portfolio formatting helpers used by the positions panel
 * (panels/portfolio.js) and the risk dashboard (panels/portfolioRisk.js).
 */

import { formatDecimal } from "./format.js";

/** Format a numeric metric with a fallback dash and optional suffix. */
export function metricValue(value, digits = 2, suffix = "") {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${formatDecimal(n, digits, "—")}${suffix}`;
}

/** Signed variant: prefixes "+" for positive values. */
export function signedMetric(value, digits = 2, suffix = "") {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${formatDecimal(n, digits, "—")}${suffix}`;
}

/** Compact dollar formatting for stress P&L (e.g. -$597.1K, $1.2M). */
export function compactMoney(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}$${formatDecimal(abs / 1_000_000, 2)}M`;
  if (abs >= 1_000) return `${sign}$${formatDecimal(abs / 1_000, 1)}K`;
  return `${sign}$${formatDecimal(abs, 0)}`;
}

/** Inline SVG sparkline from an equity curve ({equity} points). */
export function renderEquitySparkline(points) {
  const rows = Array.isArray(points) ? points.slice(-60) : [];
  if (rows.length < 2) return "";
  const values = rows.map((p) => Number(p.equity)).filter((n) => Number.isFinite(n));
  if (values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1);
  const step = 100 / Math.max(values.length - 1, 1);
  const d = values
    .map((v, idx) => {
      const x = idx * step;
      const y = 34 - ((v - min) / span) * 30;
      return `${idx === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return `
    <svg class="portfolio-analytics-sparkline" viewBox="0 0 100 38" preserveAspectRatio="none" aria-hidden="true">
      <path d="${d}" fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke"></path>
    </svg>`;
}
