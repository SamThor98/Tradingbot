import { escapeHtml } from "./format.js";

const VALID_STATES = new Set(["success", "partial", "empty", "loading", "error"]);

/** Factual operator-facing pill labels (avoid Error / Failed / Broken). */
export const STATUS_PILL_LABELS = {
  success: "Ready",
  partial: "Degraded",
  empty: "Clear",
  loading: "Loading",
  error: "Unavailable",
};

/**
 * Paint a 5-state status strip. Used by operations + system status modules.
 *
 * @param {HTMLElement} strip
 * @param {string} stateName
 * @param {string} title
 * @param {string} [detail]
 * @param {string} pillClass CSS class for the state pill span
 */
export function paintStatusStrip(strip, stateName, title, detail = "", pillClass = "operations-status-pill") {
  const state = VALID_STATES.has(stateName) ? stateName : "empty";
  const label = STATUS_PILL_LABELS[state] || STATUS_PILL_LABELS.empty;
  strip.dataset.state = state;
  strip.innerHTML = `
    <span class="${pillClass}">${escapeHtml(label)}</span>
    <strong>${escapeHtml(title)}</strong>
    <span class="muted">${escapeHtml(detail)}</span>
  `;
}
