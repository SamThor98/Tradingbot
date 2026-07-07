/**
 * Shared 5-state contract for System / Diagnostics panels (Wave 2).
 *
 * Mirrors the operations snapshot pattern: section data-state, optional
 * snapshot KPIs, and operator-alert markup for empty/error surfaces.
 */

import { buildOperatorAlertHtml, setAsyncState, ASYNC_ERROR, ASYNC_EMPTY, ASYNC_LOADING, ASYNC_SUCCESS } from "./asyncState.js";
import { renderOperationsPanelSnapshot, syncOperationsSectionState } from "./operationsPanelSnapshot.js";

const VALID = new Set(["success", "partial", "empty", "loading", "error"]);

/**
 * @param {string} sectionId
 * @param {string} stateName
 */
export function syncSystemSectionState(sectionId, stateName) {
  const state = VALID.has(stateName) ? stateName : "empty";
  syncOperationsSectionState(sectionId, state);
  const el = document.getElementById(sectionId);
  if (!el) return;
  const asyncMap = {
    loading: "loading",
    error: "error",
    empty: "empty",
    partial: "success",
    success: "success",
  };
  el.setAttribute("data-async-state", asyncMap[state] || state);
  return state;
}

/**
 * Paint a compact diagnostics snapshot (hint + 3 KPIs + lines).
 *
 * @param {string} containerId
 * @param {string} sectionId
 * @param {string} stateName
 * @param {object} config
 */
export function paintSystemPanelSnapshot(containerId, sectionId, stateName, config = {}) {
  const state = VALID.has(stateName) ? stateName : "empty";
  syncSystemSectionState(sectionId, state);
  renderOperationsPanelSnapshot(containerId, sectionId, state, {
    hint: config.hint || "",
    kpis: config.kpis || [],
    meters: config.meters || { reliability: null, execution: null },
    lines: config.lines || [],
    hidden: config.hidden === true,
  });
}

/**
 * Apply operator-alert empty/error into a panel body element.
 *
 * @param {HTMLElement|null} panel
 * @param {"empty"|"error"} kind
 * @param {object} opts
 * @param {() => void} [opts.onRetry]
 */
export function paintSystemPanelAlert(panel, kind, opts = {}) {
  if (!panel) return;
  if (kind === "empty") {
    setAsyncState(panel, ASYNC_EMPTY, {
      headline: opts.headline,
      message: opts.message || opts.detail,
    });
    return;
  }
  setAsyncState(panel, ASYNC_ERROR, {
    headline: opts.headline,
    message: opts.message,
    onRetry: opts.onRetry,
  });
}

/**
 * @param {HTMLElement|null} panel
 * @param {string} html
 */
export function paintSystemPanelSuccess(panel, html) {
  if (!panel) return;
  setAsyncState(panel, ASYNC_SUCCESS, { html });
}

export { buildOperatorAlertHtml, ASYNC_LOADING, ASYNC_SUCCESS };
