/**
 * Shadow scoreboard panel — would-have counters for every shadow-mode plugin
 * (confluence gate, correlation guard, regime v2, exit manager,
 * quality gates) merged from the last scan's diagnostics and the 7-day
 * execution safety metrics. Read-only evidence view for OFF -> SHADOW -> LIVE
 * promotion decisions.
 */

import { api } from "../modules/api.js";
import { escapeHtml, safeNum } from "../modules/format.js";
import { humanizeRolloutMode } from "../modules/humanize.js";
import { setSystemStatusStrip } from "../modules/systemStatus.js";

const PRIOR_SNAPSHOT_KEY = "tradingbot.shadow_scoreboard_prior";

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

const WOULD_KEYS = new Set([
  "would_block",
  "would_demote",
  "would_filter",
  "would_partial_tp",
  "would_move_stop",
  "would_time_stop",
  "scan_blocked",
  "exec_blocked",
]);

function modeBadge(mode) {
  const m = String(mode || "off").toLowerCase();
  const cls = m === "live" ? "good" : m === "shadow" ? "warn" : "neutral";
  return `<span class="pill ${cls}">${escapeHtml(humanizeRolloutMode(m))}</span>`;
}

function sumWouldHave(counters = {}) {
  return Object.entries(counters).reduce((sum, [key, val]) => {
    if (!WOULD_KEYS.has(key)) return sum;
    return sum + (Number(val) || 0);
  }, 0);
}

function totalWouldHave(plugins = []) {
  return plugins.reduce((sum, plugin) => sum + sumWouldHave(plugin.counters), 0);
}

function nextRolloutStep(mode, counters = {}) {
  const m = String(mode || "off").toLowerCase();
  const would = sumWouldHave(counters);
  if (m === "live") return "Maintain LIVE — review counters weekly";
  if (m === "shadow") {
    return would > 0
      ? `SHADOW active — ${would} would-have action(s) this window`
      : "SHADOW active — no would-have actions; evaluate LIVE promotion";
  }
  if (would > 0) return `OFF — ${would} would-have action(s); consider SHADOW`;
  return "OFF — no shadow signal yet";
}

function readPriorSnapshot() {
  try {
    const raw = sessionStorage.getItem(PRIOR_SNAPSHOT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function writePriorSnapshot(data) {
  try {
    sessionStorage.setItem(
      PRIOR_SNAPSHOT_KEY,
      JSON.stringify({
        scan_at: data.scan_at || null,
        total_would_have: totalWouldHave(data.plugins || []),
        plugins: (data.plugins || []).map((p) => ({
          id: p.id,
          would: sumWouldHave(p.counters),
        })),
      }),
    );
  } catch {
    /* storage unavailable */
  }
}

function renderHeadline(data, prior) {
  const total = totalWouldHave(data.plugins || []);
  const priorTotal = safeNum(prior?.total_would_have, NaN);
  let trend = "";
  if (Number.isFinite(priorTotal)) {
    const delta = total - priorTotal;
    if (delta > 0) trend = `<span class="shadow-scoreboard-trend shadow-scoreboard-trend--up">+${delta} vs prior scan</span>`;
    else if (delta < 0) trend = `<span class="shadow-scoreboard-trend shadow-scoreboard-trend--down">${delta} vs prior scan</span>`;
    else trend = `<span class="shadow-scoreboard-trend muted">unchanged vs prior scan</span>`;
  }
  return `
    <div class="shadow-scoreboard-headline">
      <strong>${total} would-have action${total === 1 ? "" : "s"}</strong>
      <span class="muted">across trial-run plugins this window</span>
      ${trend}
    </div>
  `;
}

function renderPlugin(p, priorPlugin) {
  const counters = p.counters || {};
  const would = sumWouldHave(counters);
  const priorWould = safeNum(priorPlugin?.would, NaN);
  let trendHtml = "";
  if (Number.isFinite(priorWould)) {
    const delta = would - priorWould;
    if (delta !== 0) {
      trendHtml = `<span class="shadow-plugin-trend mono-nums">${delta > 0 ? "+" : ""}${delta}</span>`;
    }
  }
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
  const nextStep = nextRolloutStep(p.mode, counters);
  return `<div class="preset-subsection shadow-plugin-card">
    <h3>${escapeHtml(p.label || p.id)} ${modeBadge(p.mode)} ${trendHtml} <small class="muted">${escapeHtml(p.scope || "")}</small></h3>
    ${contextHtml}
    <p class="shadow-plugin-next muted">${escapeHtml(nextStep)}</p>
    ${rows || '<div class="muted">No counters.</div>'}
  </div>`;
}

export function renderShadowScoreboardPanel(panel, data, error) {
  if (!panel) return;
  if (error) {
    panel.innerHTML = `<div class="report-empty">${escapeHtml(error)}</div>`;
    setSystemStatusStrip(
      "shadowScoreboardStatusStrip",
      "error",
      "Shadow scoreboard unavailable.",
      error,
    );
    return;
  }
  if (!data || !Array.isArray(data.plugins) || data.plugins.length === 0) {
    panel.innerHTML = `<div class="report-empty">No shadow scoreboard data yet — run a scan first.</div>`;
    setSystemStatusStrip(
      "shadowScoreboardStatusStrip",
      "empty",
      "No shadow scoreboard data.",
      "Run a scan first to populate would-have counters.",
    );
    return;
  }
  const prior = readPriorSnapshot();
  const priorById = Object.fromEntries((prior?.plugins || []).map((p) => [p.id, p]));
  const meta = [];
  if (data.scan_at) meta.push(`Last scan: ${escapeHtml(String(data.scan_at))}`);
  if (data.execution_window_days) {
    meta.push(`Execution window: ${data.execution_window_days}d (${data.execution_days_present || 0} days with data)`);
  }
  const metaHtml = meta.length ? `<div class="muted" style="margin-bottom: 8px;">${meta.join(" · ")}</div>` : "";
  panel.innerHTML =
    metaHtml +
    renderHeadline(data, prior) +
    data.plugins.map((p) => renderPlugin(p, priorById[p.id])).join("");
  const total = totalWouldHave(data.plugins || []);
  setSystemStatusStrip(
    "shadowScoreboardStatusStrip",
    total > 0 ? "partial" : "success",
    `${total} would-have action${total === 1 ? "" : "s"}.`,
    total > 0
      ? "Review shadow counters before promoting any plugin."
      : "No trial-run actions in this window; continue monitoring.",
  );
  writePriorSnapshot(data);
}

export async function refreshShadowScoreboard() {
  const panel = document.getElementById("shadowScoreboardPanel");
  const card = document.getElementById("shadowScoreboardSection");
  if (!panel) return;
  if (card) card.setAttribute("data-async-state", "loading");
  setSystemStatusStrip(
    "shadowScoreboardStatusStrip",
    "loading",
    "Loading shadow scoreboard.",
    "Fetching last-scan plugin would-have counters.",
  );
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
    setSystemStatusStrip(
      "shadowScoreboardStatusStrip",
      "error",
      "Shadow scoreboard load failed.",
      String(msg),
    );
    panel.querySelector("[data-shadow-retry]")?.addEventListener("click", () => void refreshShadowScoreboard());
    return;
  }
  if (card) card.setAttribute("data-async-state", "success");
  renderShadowScoreboardPanel(panel, out.data, null);
}
