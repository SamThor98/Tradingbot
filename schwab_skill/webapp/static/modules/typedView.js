/**
 * Operator-facing typed views. Flatten objects into chips / definition lists
 * so panels never JSON.stringify into the DOM.
 */

import { escapeHtml, safeText } from "./format.js";
import { humanizeFieldLabel } from "./humanize.js";

/**
 * Flatten a diagnostic / API value into one chip line.
 * Objects become "Label value · Label value"; arrays join with "; ".
 */
export function flattenDiagnosticValue(value, maxEntries = 6) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) {
    if (!value.length) return "—";
    return (
      value
        .slice(0, maxEntries)
        .map((item) => (item && typeof item === "object" ? flattenDiagnosticValue(item, 3) : safeText(item)))
        .filter((s) => s && s !== "—")
        .join("; ") || "—"
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value).slice(0, maxEntries);
    if (!entries.length) return "—";
    return entries
      .map(([k, v]) => {
        const child =
          v && typeof v === "object" ? flattenDiagnosticValue(v, 3) : safeText(v);
        return `${humanizeFieldLabel(k)} ${child}`;
      })
      .join(" · ");
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return safeText(value);
}

export function definitionListHtml(rows) {
  const body = (Array.isArray(rows) ? rows : [])
    .filter((r) => r && r.label)
    .map((r) => {
      const dd = r.html != null ? r.html : escapeHtml(r.value == null || r.value === "" ? "—" : String(r.value));
      const basis = r.basis ? `<small class="ol-dl-basis muted">${escapeHtml(r.basis)}</small>` : "";
      return `<div class="ol-dl-row"><dt>${escapeHtml(r.label)}</dt><dd>${dd}${basis}</dd></div>`;
    })
    .join("");
  return `<dl class="ol-dl">${body}</dl>`;
}

/**
 * Pass / fail / n/a checklist table. status: "pass" | "fail" | "na"
 */
export function passFailTableHtml(rows) {
  const body = (Array.isArray(rows) ? rows : [])
    .map((r) => {
      const status = r.status === "pass" || r.status === "fail" ? r.status : "na";
      const word = status === "pass" ? "Pass" : status === "fail" ? "Fail" : "n/a";
      const pill = status === "pass" ? "good" : status === "fail" ? "bad" : "neutral";
      return `<tr class="ol-check-row ol-check-row--${status}">
        <td>${escapeHtml(r.label || "Check")}</td>
        <td><span class="pill ${pill} small">${word}</span></td>
        <td>${escapeHtml(r.detail == null || r.detail === "" ? "—" : String(r.detail))}</td>
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table class="ol-check-table">
    <thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr></thead>
    <tbody>${body || `<tr><td colspan="3" class="muted">No checklist rows.</td></tr>`}</tbody>
  </table></div>`;
}

/** Related-evidence card from a flat summary object. */
export function relatedEvidenceHtml(summary) {
  if (!summary || typeof summary !== "object") {
    return `<p class="muted">Not generated yet.</p>`;
  }
  const rows = Object.entries(summary)
    .filter(([, v]) => v == null || (typeof v !== "object" && !Array.isArray(v)))
    .slice(0, 14)
    .map(([k, v]) => ({
      label: humanizeFieldLabel(k),
      value: v == null || v === "" ? "—" : String(v),
    }));
  if (!rows.length) return `<p class="muted">Not generated yet.</p>`;
  return definitionListHtml(rows);
}

export function toolResultsSummaryHtml(toolResults) {
  const rows = Array.isArray(toolResults) ? toolResults : [];
  if (!rows.length) return "";
  const items = rows
    .map((t) => {
      const name = humanizeFieldLabel(t?.name || t?.tool || t?.id || "Tool");
      const ok = t?.ok !== false && !t?.error;
      const detail =
        t?.summary ||
        t?.message ||
        t?.user_message ||
        (ok ? "Completed." : flattenDiagnosticValue(t?.error || t?.result || t));
      return `<li><span class="pill ${ok ? "good" : "bad"} small">${ok ? "Done" : "Failed"}</span> <strong>${escapeHtml(name)}</strong> — ${escapeHtml(String(detail))}</li>`;
    })
    .join("");
  return `<ul class="ol-tool-summary">${items}</ul>`;
}
