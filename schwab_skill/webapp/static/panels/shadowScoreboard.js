/**
 * Shadow scoreboard panel — would-have counters for every shadow-mode plugin
 * (confluence gate, correlation guard, regime v2, Kronos, exit manager,
 * quality gates) merged from the last scan's diagnostics and the 7-day
 * execution safety metrics. Read-only evidence view for OFF -> SHADOW -> LIVE
 * promotion decisions.
 */

import { api } from "../modules/api.js";
import { escapeHtml } from "../modules/format.js";

const COUNTER_LABELS = {
  confirmed: "Confirmed",
  would_block: "Would block",
  blocked: "Blocked",
  would_demote: "Would demote",
  demoted: "Demoted",
  scan_blocked: "Scan blocked",
  exec_blocked: "Exec blocked",
  exec_sized: "Exec resized",
  scored: "Scored",
  high_confidence: "High conf",
  medium_confidence: "Medium conf",
  low_confidence: "Low conf",
  live_adjustments: "Live adjustments",
  errors: "Errors",
  skipped_budget: "Skipped (budget)",
  high: "High",
  medium: "Medium",
  low: "Low",
  unavailable: "Unavailable",
  would_partial_tp: "Would partial-TP",
  would_move_stop: "Would move stop",
  would_time_stop: "Would time-stop",
  would_filter: "Would filter",
  filtered: "Filtered",
};

function modeBadge(mode) {
  const m = String(mode || "off").toLowerCase();
  const cls = m === "live" ? "good" : m === "shadow" ? "warn" : "neutral";
  return `<span class="pill ${cls}">${escapeHtml(m.toUpperCase())}</span>`;
}

function renderPlugin(p) {
  const counters = p.counters || {};
  const rows = Object.entries(counters)
    .map(([key, val]) => {
      const label = COUNTER_LABELS[key] || key;
      const n = Number(val) || 0;
      const valueCls = n > 0 ? "value" : "value muted";
      return `<div class="perf-metric"><span class="label">${escapeHtml(label)}</span><span class="${valueCls}">${n}</span></div>`;
    })
    .join("");
  let contextHtml = "";
  if (p.context && (p.context.score != null || p.context.bucket != null)) {
    const bits = [];
    if (p.context.score != null) bits.push(`score ${Number(p.context.score).toFixed(2)}`);
    if (p.context.bucket != null) bits.push(`bucket ${escapeHtml(String(p.context.bucket))}`);
    contextHtml = `<small class="muted">${bits.join(" · ")}</small>`;
  }
  return `<div class="preset-subsection">
    <h3>${escapeHtml(p.label || p.id)} ${modeBadge(p.mode)} <small class="muted">${escapeHtml(p.scope || "")}</small></h3>
    ${contextHtml}
    ${rows || '<div class="muted">No counters.</div>'}
  </div>`;
}

export function renderShadowScoreboardPanel(panel, data, error) {
  if (!panel) return;
  if (error) {
    panel.innerHTML = `<div class="report-empty">${escapeHtml(error)}</div>`;
    return;
  }
  if (!data || !Array.isArray(data.plugins) || data.plugins.length === 0) {
    panel.innerHTML = `<div class="report-empty">No shadow scoreboard data yet — run a scan first.</div>`;
    return;
  }
  const meta = [];
  if (data.scan_at) meta.push(`Last scan: ${escapeHtml(String(data.scan_at))}`);
  if (data.execution_window_days) {
    meta.push(`Execution window: ${data.execution_window_days}d (${data.execution_days_present || 0} days with data)`);
  }
  const metaHtml = meta.length ? `<div class="muted" style="margin-bottom: 8px;">${meta.join(" · ")}</div>` : "";
  panel.innerHTML = metaHtml + data.plugins.map(renderPlugin).join("");
}

export async function refreshShadowScoreboard() {
  const panel = document.getElementById("shadowScoreboardPanel");
  const card = document.getElementById("shadowScoreboardSection");
  if (!panel) return;
  if (card) card.setAttribute("data-async-state", "loading");
  panel.innerHTML = `<div class="async-state async-state--loading muted" role="status">
    <span class="async-spinner" aria-hidden="true"></span>
    <span>Loading shadow scoreboard…</span>
  </div>`;
  const out = await api.get("/api/cockpit/shadow-scoreboard");
  if (!out.ok) {
    if (card) card.setAttribute("data-async-state", "error");
    const msg = out.user_message || out.error || "Request failed";
    panel.innerHTML = `<div class="async-state async-state--error" role="alert">
      <span>Shadow scoreboard load failed: ${escapeHtml(String(msg))}</span>
      <button type="button" class="btn small secondary" data-shadow-retry>Retry</button>
    </div>`;
    panel.querySelector("[data-shadow-retry]")?.addEventListener("click", () => void refreshShadowScoreboard());
    return;
  }
  if (card) card.setAttribute("data-async-state", "success");
  renderShadowScoreboardPanel(panel, out.data, null);
}
