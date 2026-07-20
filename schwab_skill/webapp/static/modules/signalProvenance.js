/**
 * Provenance chips for scan signal rows — mirrors backend Provenance contract
 * (core/contracts/provenance.py) without requiring a round-trip through cockpit.
 */

import { escapeHtml, safeText } from "./format.js";

/** True when the scanner shortlist marked this row as eligible for staging. */
export function isScanSignalStageable(sig = {}) {
  return safeText(sig._filter_status || "kept").toLowerCase() === "kept";
}

/**
 * @param {object} row
 * @returns {{ source: string, confidence: string, isStale: boolean, staleReason: string|null }}
 */
export function provenanceFromSignal(row = {}) {
  const usedFallback = Boolean(row.used_fallback_data || row.used_fallback);
  const source = safeText(
    row.fallback_provider || row.data_provider || row.data_provider_primary || "unknown",
  )
    .trim()
    .toLowerCase();
  const dataQuality = safeText(row._data_quality || row.data_quality || "")
    .trim()
    .toLowerCase();

  let confidence = "high";
  if (dataQuality === "stale" || dataQuality === "conflict") confidence = "low";
  else if (usedFallback || source !== "schwab" || dataQuality === "degraded") confidence = "medium";

  const isStale = dataQuality === "stale" || dataQuality === "conflict";
  const staleReason =
    safeText(row.fallback_reason || "").trim() ||
    (dataQuality && ["stale", "conflict", "degraded"].includes(dataQuality) ? dataQuality : "") ||
    null;

  return {
    source: source || "unknown",
    confidence,
    isStale,
    staleReason,
  };
}

/**
 * Compact HTML chip: `schwab · high` or `yfinance · medium · stale`
 * @param {object} row
 */
export function renderSignalProvenanceChip(row = {}) {
  const p = provenanceFromSignal(row);
  const title = p.staleReason
    ? `Data source: ${p.source}. Confidence: ${p.confidence}. ${p.staleReason}`
    : `Data source: ${p.source}. Confidence: ${p.confidence}.`;
  const staleHtml = p.isStale
    ? `<span class="prov-chip__stale">stale</span>`
    : "";
  return `<span class="prov-chip prov-${escapeHtml(p.confidence)}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}"><span class="prov-chip__source">${escapeHtml(p.source)}</span><span aria-hidden="true">&middot;</span><span class="prov-chip__confidence">${escapeHtml(p.confidence)}</span>${staleHtml}</span>`;
}

/**
 * Tradeable verdict for a scan row.
 * @param {object} sig Raw signal with `_filter_status`
 */
export function renderTradeableVerdict(sig = {}) {
  if (isScanSignalStageable(sig)) {
    return `<span class="scan-gate-chip scan-gate-chip--pass scan-tradeable-verdict" title="Eligible for staging">Tradeable</span>`;
  }
  const reasons = Array.isArray(sig._filter_reasons) ? sig._filter_reasons : [];
  const status = safeText(sig._filter_status || "filtered").toLowerCase();
  const hint = reasons.length ? safeText(reasons[0]).replace(/_/g, " ") : status.replace(/_/g, " ");
  return `<span class="scan-gate-chip scan-gate-chip--blocked scan-tradeable-verdict" title="Not stageable: ${escapeHtml(hint)}">Blocked</span>`;
}
