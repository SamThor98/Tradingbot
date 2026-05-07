/**
 * Provenance + freshness helpers for live UI surfaces.
 *
 * Every KPI, badge, and ribbon must answer three questions:
 *   1. Where did this number come from? (endpoint or source label)
 *   2. As of when? (`as_of` ISO timestamp from the API payload)
 *   3. Is it stale? (older than the surface's freshness budget)
 *
 * This module centralises that contract so panels stop re-implementing it
 * inline with subtly different rules and colours.
 */

import { safeText, timeAgo } from "./format.js";

/** Default freshness budgets (in seconds) keyed by logical surface name.
 *  Override per-call by passing an explicit `budgetSec`. */
export const FRESHNESS_BUDGETS_SEC = {
  health_ribbon: 90,
  decision_dashboard: 600,
  scan_results: 3600,
  portfolio: 300,
  pending_queue: 60,
  performance: 1800,
  calibration: 86400,
  status_details: 120,
};

/** Three-state freshness verdict. `unknown` means we have no timestamp. */
export const FRESHNESS_FRESH = "fresh";
export const FRESHNESS_STALE = "stale";
export const FRESHNESS_UNKNOWN = "unknown";

/**
 * Classify a timestamp against a budget.
 * @param {string|null|undefined} iso
 * @param {number} budgetSec
 * @returns {{ state: string, ageSec: number|null }}
 */
export function classifyFreshness(iso, budgetSec) {
  if (!iso) return { state: FRESHNESS_UNKNOWN, ageSec: null };
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return { state: FRESHNESS_UNKNOWN, ageSec: null };
  const ageSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  const budget = Number.isFinite(budgetSec) && budgetSec > 0 ? budgetSec : 60;
  return {
    state: ageSec > budget ? FRESHNESS_STALE : FRESHNESS_FRESH,
    ageSec,
  };
}

/**
 * Build a short human label like "12s ago • api/status" or
 * "2h ago — refreshing" suitable for putting under a KPI value.
 *
 * @param {object} opts
 * @param {string|null} opts.asOf       ISO timestamp (or null)
 * @param {string} opts.source          Endpoint or short label, e.g. "/api/status"
 * @param {number} [opts.budgetSec]     Freshness budget; if omitted, uses surface key.
 * @param {string} [opts.surface]       Logical surface name (key into FRESHNESS_BUDGETS_SEC).
 * @param {boolean} [opts.refreshing]   When true, append " — refreshing".
 * @param {string} [opts.unavailable]   Override label when no timestamp exists. Default "no data".
 */
export function freshnessLabel({
  asOf = null,
  source = "",
  budgetSec,
  surface = "",
  refreshing = false,
  unavailable = "no data",
} = {}) {
  const budget =
    Number.isFinite(budgetSec) && budgetSec > 0
      ? budgetSec
      : FRESHNESS_BUDGETS_SEC[surface] || 60;
  const cls = classifyFreshness(asOf, budget);
  const src = safeText(source).trim();
  if (cls.state === FRESHNESS_UNKNOWN) {
    const tail = src ? ` · ${src}` : "";
    return `${unavailable}${tail}`;
  }
  const ageStr = timeAgo(asOf);
  const stale = cls.state === FRESHNESS_STALE ? " · stale" : "";
  const refreshStr = refreshing ? " — refreshing" : "";
  const tail = src ? ` · ${src}` : "";
  return `${ageStr}${stale}${refreshStr}${tail}`;
}

/**
 * Apply a provenance label + state attribute to a DOM element.
 *
 * The element is expected to be a small "<small>" or "<span>" sibling of a
 * KPI value. We set `data-freshness="fresh|stale|unknown"` so CSS can colour
 * the label without each panel reinventing the colour palette.
 *
 * @param {HTMLElement|null} el
 * @param {object} opts  Same shape as `freshnessLabel`.
 */
export function applyFreshness(el, opts = {}) {
  if (!el) return;
  const budget =
    Number.isFinite(opts.budgetSec) && opts.budgetSec > 0
      ? opts.budgetSec
      : FRESHNESS_BUDGETS_SEC[opts.surface] || 60;
  const cls = classifyFreshness(opts.asOf, budget);
  el.textContent = freshnessLabel(opts);
  el.setAttribute("data-freshness", cls.state);
  el.setAttribute(
    "title",
    [
      opts.source ? `Source: ${opts.source}` : "",
      opts.asOf ? `As of: ${opts.asOf}` : "",
      `Budget: ${budget}s`,
      `State: ${cls.state}`,
    ]
      .filter(Boolean)
      .join("\n"),
  );
}

/**
 * Mark a value element as "unavailable" when an upstream call failed or
 * returned no usable data. Centralises the "—" treatment so panels stop
 * silently rendering 0 or stale prior values.
 *
 * @param {HTMLElement|null} el
 * @param {string} reason A short reason that goes into the `title` tooltip.
 */
export function markUnavailable(el, reason = "") {
  if (!el) return;
  el.textContent = "—";
  el.setAttribute("data-unavailable", "true");
  if (reason) el.setAttribute("title", `Unavailable: ${reason}`);
}

/**
 * Clear any prior "unavailable" treatment when a fresh value is about to be
 * written into the element.
 */
export function clearUnavailable(el) {
  if (!el) return;
  el.removeAttribute("data-unavailable");
  el.removeAttribute("title");
}
