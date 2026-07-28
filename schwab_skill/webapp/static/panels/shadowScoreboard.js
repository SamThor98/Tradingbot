/**
 * Shadow scoreboard / plugin mode workbench.
 * Roster (working on vs already live), session gaps, and local mode toggles.
 */

import { api } from "../modules/api.js";
import { state } from "../modules/state.js";
import { escapeHtml, safeNum } from "../modules/format.js";
import { humanizeRolloutMode } from "../modules/humanize.js";
import { setSystemStatusStrip } from "../modules/systemStatus.js";
import {
  paintSystemPanelAlert,
  paintSystemPanelSnapshot,
  paintSystemPanelSuccess,
  syncSystemSectionState,
} from "../modules/systemPanelContract.js";

const PRIOR_SNAPSHOT_KEY = "tradingbot.shadow_scoreboard_prior";

const PROMOTION_GATES_PLAIN =
  "Base signal must clear PF mean ≥ 1.20 and worst-era PF ≥ 1.00 before any plugin goes LIVE.";

const WOULD_KEYS = new Set([
  "would_block",
  "would_demote",
  "would_filter",
  "would_drop",
  "would_drop_any",
  "would_partial_tp",
  "would_move_stop",
  "would_time_stop",
  "scan_blocked",
  "exec_blocked",
  "stage2_would_filter",
  "blocked",
]);

let _bound = false;
let _lastPayload = null;

function modeBadge(mode) {
  const m = String(mode || "off").toLowerCase();
  const liveish = m === "live" || m === "soft" || m === "hard";
  const cls = liveish ? "good" : m === "shadow" ? "warn" : "neutral";
  return `<span class="pill ${cls}">${escapeHtml(humanizeRolloutMode(m))}</span>`;
}

function sumWouldHave(counters = {}) {
  return Object.entries(counters).reduce((sum, [key, val]) => {
    if (!WOULD_KEYS.has(key) && !String(key).startsWith("would_")) return sum;
    return sum + (Number(val) || 0);
  }, 0);
}

function totalWouldHave(plugins = []) {
  return plugins.reduce((sum, plugin) => sum + sumWouldHave(plugin.counters), 0);
}

function writesEnabled(data) {
  if (data && typeof data.writes_enabled === "boolean") return data.writes_enabled;
  return Boolean(state.publicConfig?.plugin_mode_writes_enabled);
}

function isLiveTier(mode) {
  const m = String(mode || "").toLowerCase();
  return m === "live" || m === "soft" || m === "hard";
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

function renderPromotionLegend() {
  return `<div class="shadow-scoreboard-legend muted">
    <strong>How to read a card:</strong>
    <span><em>Last scan</em> = what this guardrail would have done · <em>Evidence</em> = good shadow sessions · <em>Backtest</em> = offline PF / retention when an artifact exists.</span>
    <span>${escapeHtml(PROMOTION_GATES_PLAIN)}</span>
  </div>`;
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
  const summary = data.summary || {};
  const working = safeNum(summary.working_on_count, 0);
  const liveCount = safeNum(summary.already_live_count, 0);
  const lacking = safeNum(summary.lacking_sessions_count, 0);
  return `
    <div class="shadow-scoreboard-headline">
      <strong>${total} would-have action${total === 1 ? "" : "s"}</strong>
      <span class="muted">· ${working} working on · ${liveCount} live · ${lacking} lacking sessions</span>
      ${trend}
    </div>
  `;
}

function renderRestartBanner(data) {
  if (!data?.restart_required) return "";
  return `<div class="banner warn shadow-workbench-restart" role="alert">
    <strong>Restart required.</strong>
    <span>${escapeHtml(data.restart_detail || "Last mode write did not verify in-process. Restart the dashboard, then refresh.")}</span>
    <span class="muted">Further toggles are frozen until modes match.</span>
  </div>`;
}

function renderChecklist(p) {
  const items = p.checklist?.items || [];
  if (!items.length) return "";
  const rows = items
    .map((it) => {
      const ok = Boolean(it.ok);
      return `<li class="${ok ? "good" : "warn"}">
        <strong>${escapeHtml(it.label || it.id)}</strong>
        — ${escapeHtml(String(it.detail || (ok ? "ok" : "missing")))}
      </li>`;
    })
    .join("");
  return `<details class="shadow-plugin-checklist">
    <summary class="muted">LIVE checklist · ${escapeHtml(p.checklist?.lacking_summary || "see items")}</summary>
    <ul class="shadow-checklist-list">${rows}</ul>
  </details>`;
}

function modeOptions(p) {
  const allowed = Array.isArray(p.allowed_modes) && p.allowed_modes.length
    ? p.allowed_modes
    : ["off", "shadow", "live"];
  const current = String(p.mode || "off").toLowerCase();
  return allowed
    .map((m) => {
      const sel = m === current ? " selected" : "";
      return `<option value="${escapeHtml(m)}"${sel}>${escapeHtml(humanizeRolloutMode(m))}</option>`;
    })
    .join("");
}

function renderPlugin(p, { canWrite, frozen }) {
  const stats = p.stats || {};
  const sessions = p.sessions || {};
  const collected = safeNum(sessions.collected, 0);
  const target = safeNum(sessions.target, 5);
  const lastScan = stats.last_scan || {};
  const evidence = stats.evidence || {};
  const backtest = stats.backtest || {};
  const evidenceTone = evidence.tone === "good" ? "good" : evidence.tone === "warn" ? "warn" : "muted";
  const backtestTone = backtest.available
    ? backtest.passes_promotion_gates === false
      ? "warn"
      : "good"
    : "muted";
  const scanLines = (lastScan.lines || []).slice(0, 4)
    .map((line) => `<li>${escapeHtml(line)}</li>`)
    .join("");

  const controls = canWrite
    ? `<div class="shadow-plugin-controls inline-form">
        <label class="muted" for="shadow-mode-${escapeHtml(p.id)}">Mode</label>
        <select id="shadow-mode-${escapeHtml(p.id)}"
          data-plugin-id="${escapeHtml(p.id)}"
          data-current-mode="${escapeHtml(String(p.mode || "off"))}"
          class="shadow-mode-select"
          ${frozen ? "disabled" : ""}>
          ${modeOptions(p)}
        </select>
      </div>`
    : `<p class="muted small">Read-only on this server (mode writes are local-only).</p>`;

  return `<article class="preset-subsection shadow-plugin-card" data-plugin-id="${escapeHtml(p.id)}">
    <header class="shadow-plugin-head">
      <h3>${escapeHtml(p.label || p.id)} ${modeBadge(p.mode)}</h3>
      <p class="shadow-plugin-purpose">${escapeHtml(stats.purpose || p.purpose || "")}</p>
      <p class="muted small">${escapeHtml(stats.scope_plain || p.scope || "")}</p>
    </header>
    <div class="shadow-stat-grid" role="group" aria-label="Guardrail stats">
      <div class="shadow-stat-cell">
        <span class="shadow-stat-label">Last scan</span>
        <strong class="shadow-stat-value">${escapeHtml(lastScan.headline || "No scan data")}</strong>
        ${scanLines ? `<ul class="shadow-stat-lines">${scanLines}</ul>` : ""}
      </div>
      <div class="shadow-stat-cell">
        <span class="shadow-stat-label">Evidence</span>
        <strong class="shadow-stat-value ${evidenceTone}">${escapeHtml(evidence.headline || `${collected}/${target} sessions`)}</strong>
      </div>
      <div class="shadow-stat-cell">
        <span class="shadow-stat-label">Backtest</span>
        <strong class="shadow-stat-value ${backtestTone}">${escapeHtml(backtest.headline || "No offline backtest attached")}</strong>
      </div>
    </div>
    <p class="shadow-plugin-next"><strong>Next:</strong> ${escapeHtml(stats.next_step || "")}</p>
    ${renderChecklist(p)}
    ${controls}
  </article>`;
}

function renderTier(data, { canWrite, frozen }) {
  const working = data.tiers?.working_on || (data.plugins || []).filter((p) => p.tier === "working_on");
  const live = data.tiers?.already_live || (data.plugins || []).filter((p) => p.tier === "already_live");
  const section = (title, blurb, list) => {
    if (!list.length) {
      return `<div class="shadow-tier"><h3 class="shadow-tier-title scan-funnel-board-title">${escapeHtml(title)}</h3>
        <p class="muted">${escapeHtml(blurb)}</p>
        <p class="muted">None right now.</p></div>`;
    }
    return `<div class="shadow-tier">
      <h3 class="shadow-tier-title scan-funnel-board-title">${escapeHtml(title)} (${list.length})</h3>
      <p class="muted shadow-tier-blurb">${escapeHtml(blurb)}</p>
      ${list.map((p) => renderPlugin(p, { canWrite, frozen })).join("")}
    </div>`;
  };
  return (
    section(
      "Still testing",
      "Observe-only or off. Read Last scan / Evidence / Backtest before turning anything on.",
      working,
    ) +
    section(
      "Already enforcing",
      "These change live scan or trade behavior. Demote only if you intend to turn enforcement off.",
      live,
    )
  );
}

function paintShadowSnapshot(stateName, opts = {}) {
  paintSystemPanelSnapshot("shadowScoreboardSnapshot", "shadowScoreboardSection", stateName, {
    hint: "Plugin path: OFF → SHADOW → LIVE",
    kpis: [
      { label: "WOULD-HAVE", sub: "actions", value: opts.would ?? "—", tone: opts.would > 0 ? "warn" : "success" },
      { label: "WORKING", sub: "on", value: opts.working ?? "—", tone: "neutral" },
      { label: "LACKING", sub: "sessions", value: opts.lacking ?? "—", tone: opts.lacking > 0 ? "warn" : "success" },
    ],
    lines: [opts.title, opts.detail].filter(Boolean),
  });
}

export function updateShadowWorkbenchTeaser(data) {
  const el = document.getElementById("shadowWorkbenchTeaser");
  if (!el) return;
  if (!data || !data.summary) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  const teaser = data.summary.teaser || "";
  const lacking = safeNum(data.summary.lacking_sessions_count, 0);
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="shadow-workbench-teaser-inner">
      <strong>Shadow workbench</strong>
      <span class="muted">${escapeHtml(teaser)}</span>
      <a class="btn small secondary" href="#shadowScoreboardSection" data-shadow-workbench-jump>Open scoreboard</a>
      ${lacking > 0 ? `<span class="warn small">${lacking} lacking sessions</span>` : ""}
    </div>
  `;
  el.querySelector("[data-shadow-workbench-jump]")?.addEventListener("click", (ev) => {
    ev.preventDefault();
    window.dispatchEvent(
      new CustomEvent("shadow_workbench_open", {
        detail: { sectionId: "shadowScoreboardSection", lazyKey: "shadowScoreboard" },
      }),
    );
  });
}

async function applyModeChange(pluginId, nextMode, currentMode) {
  const demoting = isLiveTier(currentMode) && !isLiveTier(nextMode);
  const promoting = isLiveTier(nextMode) && !isLiveTier(currentMode);

  if (demoting) {
    const ok = window.confirm(
      `Demote ${pluginId} from ${currentMode} → ${nextMode}?\n\nThis turns off live enforcement.`,
    );
    if (!ok) return false;
  } else if (!promoting) {
    const ok = window.confirm(`Set ${pluginId} to ${nextMode}?`);
    if (!ok) return false;
  }

  const body = {
    plugin_id: pluginId,
    mode: nextMode,
    reason: "",
    confirm_demote: demoting,
  };

  if (promoting) {
    const phrase = window.prompt(
      `Enable LIVE for ${pluginId}.\nChecklist must already be green (no break-glass).\n\nType exactly: PROMOTE ${String(pluginId).toUpperCase()}`,
      "",
    );
    if (phrase == null) return false;
    body.confirm_phrase = phrase;
    const reason = window.prompt("Reason for promotion ledger (optional):", "") || "";
    body.reason = reason;
    if (state.publicConfig?.api_key_required) {
      const key = window.prompt("Re-enter WEB_API_KEY to authorize LIVE:", "");
      if (key == null) return false;
      body.api_key = key;
    } else {
      body.api_key = (localStorage.getItem("tradingbot.api_key") || "").trim() || null;
    }
  }

  const out = await api.post("/api/cockpit/shadow-scoreboard/mode", body);
  if (!out.ok) {
    window.alert(out.user_message || out.error || "Mode update failed");
    return false;
  }
  if (out.data?.restart_required) {
    window.alert(out.data.restart_detail || "Restart required to apply mode change.");
  }
  return true;
}

function bindModeControls(panel) {
  if (!panel || _bound) return;
  panel.addEventListener("change", (ev) => {
    const sel = ev.target?.closest?.(".shadow-mode-select");
    if (!sel) return;
    const pluginId = sel.getAttribute("data-plugin-id");
    const current = sel.getAttribute("data-current-mode") || "off";
    const next = sel.value;
    if (!pluginId || next === current) return;
    void (async () => {
      const ok = await applyModeChange(pluginId, next, current);
      if (!ok) {
        sel.value = current;
        return;
      }
      await refreshShadowScoreboard();
    })();
  });
  _bound = true;
}

export function renderShadowScoreboardPanel(panel, data, error) {
  if (!panel) return;
  _lastPayload = data;
  if (error) {
    paintSystemPanelAlert(panel, "error", {
      headline: "Data unavailable",
      message: error,
      onRetry: () => void refreshShadowScoreboard(),
    });
    setSystemStatusStrip(
      "shadowScoreboardStatusStrip",
      "error",
      "Shadow scoreboard unavailable.",
      error,
    );
    paintShadowSnapshot("error", { title: "Shadow scoreboard unavailable.", detail: error });
    updateShadowWorkbenchTeaser(null);
    return;
  }
  if (!data || !Array.isArray(data.plugins) || data.plugins.length === 0) {
    paintSystemPanelAlert(panel, "empty", {
      headline: "No results yet",
      message: "Run a scan first to populate would-have counters.",
    });
    setSystemStatusStrip(
      "shadowScoreboardStatusStrip",
      "empty",
      "No shadow scoreboard data.",
      "Run a scan first to populate would-have counters.",
    );
    paintShadowSnapshot("empty", {
      title: "No shadow scoreboard data.",
      detail: "Run a scan first to populate would-have counters.",
    });
    updateShadowWorkbenchTeaser(null);
    return;
  }
  const prior = readPriorSnapshot();
  const canWrite = writesEnabled(data);
  const frozen = Boolean(data.restart_required);
  const meta = [];
  if (data.scan_at) meta.push(`Last scan: ${escapeHtml(String(data.scan_at))}`);
  if (data.execution_window_days) {
    meta.push(`Execution window: ${data.execution_window_days}d (${data.execution_days_present || 0} days with data)`);
  }
  if (canWrite) meta.push("Local toggles enabled");
  else meta.push("Read-only workbench");
  const metaHtml = meta.length ? `<div class="muted" style="margin-bottom: 8px;">${meta.join(" · ")}</div>` : "";
  const html =
    metaHtml +
    renderRestartBanner(data) +
    renderPromotionLegend() +
    renderHeadline(data, prior) +
    renderTier(data, { canWrite, frozen });
  paintSystemPanelSuccess(panel, html);
  bindModeControls(panel);
  const total = totalWouldHave(data.plugins || []);
  const working = safeNum(data.summary?.working_on_count, 0);
  const lacking = safeNum(data.summary?.lacking_sessions_count, 0);
  const statusState = data.restart_required ? "error" : lacking > 0 || total > 0 ? "partial" : "success";
  setSystemStatusStrip(
    "shadowScoreboardStatusStrip",
    statusState,
    data.summary?.teaser || `${total} would-have action${total === 1 ? "" : "s"}.`,
    data.restart_required
      ? data.restart_detail || "Restart required."
      : lacking > 0
        ? "Review session gaps before promoting any plugin to LIVE."
        : "No trial-run friction this window; base-signal PF gates still apply.",
  );
  paintShadowSnapshot(statusState, {
    would: total,
    working,
    lacking,
    title: data.summary?.teaser || `${total} would-have actions.`,
    detail: data.restart_required
      ? data.restart_detail
      : "Toggle modes below; LIVE requires green checklist + typed PROMOTE phrase.",
  });
  writePriorSnapshot(data);
  updateShadowWorkbenchTeaser(data);
}

export async function refreshShadowScoreboard() {
  const panel = document.getElementById("shadowScoreboardPanel");
  if (!panel) return;
  syncSystemSectionState("shadowScoreboardSection", "loading");
  setSystemStatusStrip(
    "shadowScoreboardStatusStrip",
    "loading",
    "Loading shadow scoreboard.",
    "Fetching plugin roster and would-have counters.",
  );
  paintShadowSnapshot("loading", {
    title: "Loading shadow scoreboard.",
    detail: "Fetching plugin roster and would-have counters.",
  });
  panel.innerHTML = `<div class="async-state async-state--loading muted" role="status">
    <span class="async-spinner" aria-hidden="true"></span>
    <span>Loading shadow workbench…</span>
  </div>`;
  const out = await api.get("/api/cockpit/shadow-scoreboard");
  if (!out.ok) {
    const msg = out.user_message || out.error || "Request failed";
    paintSystemPanelAlert(panel, "error", {
      headline: "Data unavailable",
      message: String(msg),
      onRetry: () => void refreshShadowScoreboard(),
    });
    setSystemStatusStrip(
      "shadowScoreboardStatusStrip",
      "error",
      "Shadow scoreboard load failed.",
      String(msg),
    );
    paintShadowSnapshot("error", { title: "Shadow scoreboard load failed.", detail: String(msg) });
    return;
  }
  renderShadowScoreboardPanel(panel, out.data, null);
}

/** Lightweight teaser refresh for the Scan screen (does not require panel). */
export async function refreshShadowWorkbenchTeaser() {
  const out = await api.get("/api/cockpit/shadow-scoreboard");
  if (!out.ok) {
    updateShadowWorkbenchTeaser(null);
    return;
  }
  updateShadowWorkbenchTeaser(out.data);
  _lastPayload = out.data;
}

export function getLastShadowWorkbenchPayload() {
  return _lastPayload;
}
