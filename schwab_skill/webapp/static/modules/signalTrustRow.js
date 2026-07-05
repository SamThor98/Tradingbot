/**
 * Shared trust chips for scan detail + pending queue (provenance, data quality, filters).
 */

import { escapeHtml, safeText } from "./format.js";
import { formatFilterReasons } from "./filterReasons.js";
import {
  renderSignalProvenanceChip,
  renderTradeableVerdict,
} from "./signalProvenance.js";

function dataQualityPillClass(value) {
  const dq = safeText(value || "").trim().toLowerCase();
  if (["ok", "fresh", "healthy", "good"].includes(dq)) return "good";
  if (["stale", "conflict", "blocked", "failed", "fail"].includes(dq)) return "bad";
  if (["degraded", "partial", "unknown", "warning", "warn"].includes(dq)) return "warn";
  return "neutral";
}

function renderDataQualityChip(sig = {}, title = "Data quality for this signal") {
  const dq = safeText(sig._data_quality || sig.data_quality || "").trim().toLowerCase();
  if (!dq || dq === "ok") return "";
  const cls = dataQualityPillClass(dq);
  return `<span class="pill ${cls}" title="${escapeHtml(title)}">Data: ${escapeHtml(dq)}</span>`;
}

function renderFilterHint(sig = {}) {
  const reasons = formatFilterReasons(sig._filter_reasons);
  if (!reasons.length) return "";
  const preview = reasons.slice(0, 2).join(" · ");
  const suffix = reasons.length > 2 ? ` (+${reasons.length - 2} more)` : "";
  return `<span class="signal-trust-filter-hint muted" title="${escapeHtml(reasons.join("; "))}">${escapeHtml(preview)}${suffix}</span>`;
}

/** HTML trust row: tradeable verdict, provenance, optional data-quality pill, filter hint. */
export function renderSignalTrustRow(sig = {}) {
  const parts = [
    renderTradeableVerdict(sig),
    renderSignalProvenanceChip(sig),
    renderDataQualityChip(sig),
  ].filter(Boolean);
  const filterHint = renderFilterHint(sig);
  if (!parts.length && !filterHint) return "";
  return `
    <div class="signal-trust-row">
      <div class="signal-trust-chips">${parts.join("")}</div>
      ${filterHint}
    </div>
  `;
}
