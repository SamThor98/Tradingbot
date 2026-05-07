/**
 * Pure formatting helpers. No DOM, no API calls, no module state.
 *
 * These are referenced from virtually every render function in the dashboard,
 * so the contract here is *do not introduce side effects*. If you need to
 * touch the DOM, put it in a panel module instead.
 *
 * Every function in this file is a literal extraction from the legacy
 * `app.js`; behaviour is byte-identical.
 */

export function safeText(value) {
  if (value === null || value === undefined) return "—";
  return String(value);
}

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function safeNum(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

export function prettyJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function formatMoney(value) {
  return `$${safeNum(value, 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Format a decimal with fixed precision (e.g. 12.3, 45.67). */
export function formatDecimal(value, digits = 1, fallback = "—") {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return n.toFixed(digits);
}

/** Format a fraction (0.42) as "42.0%". */
export function pct(value, digits = 1) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

/** Backtest metrics from API are already in percent points (e.g. 55.2 => 55.2%). */
export function formatPercentPoints(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

export function clampPct(v) {
  return Math.max(0, Math.min(100, safeNum(v, 0)));
}

/** Translate a 0–100 score into a coarse verdict label. */
export function verdictFromScore(score, high = 70, low = 45) {
  const n = safeNum(score, 0);
  if (n >= high) return "bullish";
  if (n <= low) return "bearish";
  return "neutral";
}

export function timeAgo(iso) {
  if (!iso) return "unknown";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "unknown";
  const sec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

export function durationSec(startIso, endIso) {
  const start = Date.parse(startIso || "");
  const end = Date.parse(endIso || "");
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return Math.max(0, Math.floor((end - start) / 1000));
}

/**
 * Format a basis points value as "X.X bps" or "X.XX%". One helper so panels
 * stop dividing by 100 inline and accidentally drifting between bps and pct.
 *
 * @param {number} bps integer-ish basis points (1 bps = 0.01%)
 * @param {{ digits?: number, asPercent?: boolean }} [opts]
 */
export function formatBps(bps, opts = {}) {
  const n = Number(bps);
  if (!Number.isFinite(n)) return "—";
  const digits = Number.isFinite(opts.digits) ? opts.digits : 1;
  if (opts.asPercent) {
    return `${(n / 100).toFixed(digits)}%`;
  }
  return `${n.toFixed(digits)} bps`;
}

/**
 * Format an integer count with thousands separators. Returns "—" for non-finite.
 */
export function formatInt(value, fallback = "—") {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.round(n).toLocaleString();
}

/**
 * Format a count from the API. Distinct from formatInt because callers often
 * want "0" preserved (a meaningful zero) while still rendering "—" when the
 * field is missing or non-numeric.
 *
 * @param {number|null|undefined} value
 * @param {string} [fallback]
 */
export function formatCount(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(0, Math.round(n)).toLocaleString();
}

/**
 * Cents → dollars formatter for endpoints that return integer cents.
 * Use `formatMoney` for endpoints that already return dollars.
 */
export function formatCents(cents) {
  const n = Number(cents);
  if (!Number.isFinite(n)) return "—";
  return formatMoney(n / 100);
}

/**
 * Format a timestamp in the user's local timezone as "Apr 14, 2026 09:33 AM PDT".
 * Falls back to the raw ISO string when the value cannot be parsed.
 *
 * @param {string|null|undefined} iso
 */
export function formatLocalTime(iso) {
  if (!iso) return "—";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return safeText(iso);
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(new Date(ts));
  } catch {
    return new Date(ts).toISOString();
  }
}

/**
 * Format an ISO timestamp as a short clock label: "09:33:11 PDT". Useful for
 * dense tables where the full date is implied by the surrounding context.
 */
export function formatClock(iso) {
  if (!iso) return "—";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return safeText(iso);
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short",
    }).format(new Date(ts));
  } catch {
    return new Date(ts).toTimeString().split(" ")[0];
  }
}

/**
 * Backtest-style percent-points (e.g. 55.2 → "55.20%"). Alias of
 * `formatPercentPoints` kept for API symmetry alongside `pct` (ratios).
 *
 * Use `pct(0.55)` when the API gives you a 0–1 ratio.
 * Use `formatPP(55)` when the API already gives you percent points.
 */
export function formatPP(value, digits = 2) {
  return formatPercentPoints(value, digits);
}

/**
 * Render a signed delta with a leading "+" for positive numbers, suitable for
 * day P/L columns. `formatter` controls the magnitude rendering.
 *
 * @param {number} value
 * @param {(n:number)=>string} formatter   Defaults to `(n)=>n.toFixed(2)`.
 */
export function formatSignedDelta(value, formatter) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const fmt = typeof formatter === "function" ? formatter : (x) => x.toFixed(2);
  const body = fmt(Math.abs(n));
  if (n > 0) return `+${body}`;
  if (n < 0) return `-${body}`;
  return body;
}

/**
 * Mark a value cell as "unknown / missing" while preserving the row layout.
 * Returns the em-dash so callers can do `el.textContent = unknown()`.
 */
export function unknown() {
  return "—";
}
