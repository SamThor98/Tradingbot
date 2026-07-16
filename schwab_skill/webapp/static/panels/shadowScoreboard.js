/**
 * Shadow scoreboard panel — promotion clarity for OFF → SHADOW → LIVE plugins.
 * Answers one question per plugin: is this earning promotion?
 */

import { api } from "../modules/api.js";
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

function promotionVerdict(mode, would = 0) {
  const m = String(mode || "off").toLowerCase();
  if (m === "live") {
    return {
      label: "LIVE — monitor weekly",
      tone: "good",
      detail: would > 0 ? `${would} would-have action(s) still logged this window.` : "No would-have friction this window.",
    };
  }
  if (m === "shadow") {
    if (would > 0) {
      return {
        label: "SHADOW — gathering evidence",
        tone: "warn",
        detail: `${would} would-have action(s). Review counters before promoting to LIVE.`,
      };
    }
    return {
      label: "SHADOW — candidate for LIVE",
      tone: "good",
      detail: "No would-have blocks this window. Still requires base-signal PF gates.",
    };
  }
  if (would > 0) {
    return {
      label: "OFF — shadow recommended",
      tone: "warn",
      detail: `${would} would-have action(s) detected. Turn on SHADOW before LIVE.`,
    };
  }
  return {
    label: "OFF — no signal yet",
    tone: "neutral",
    detail: "No would-have actions in this window.",
  };
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
    <strong>Promotion path:</strong> OFF → SHADOW → LIVE.
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
  const liveCount = (data.plugins || []).filter((p) => String(p.mode || "").toLowerCase() === "live").length;
  const shadowCount = (data.plugins || []).filter((p) => String(p.mode || "").toLowerCase() === "shadow").length;
  return `
    <div class="shadow-scoreboard-headline">
      <strong>${total} would-have action${total === 1 ? "" : "s"}</strong>
      <span class="muted">across trial-run plugins · ${shadowCount} shadow · ${liveCount} live</span>
      ${trend}
    </div>
  `;
}

function renderPlugin(p) {
  const counters = p.counters || {};
  const would = sumWouldHave(counters);
  const verdict = promotionVerdict(p.mode, would);
  const verdictCls = verdict.tone === "good" ? "good" : verdict.tone === "warn" ? "warn" : "neutral";
  const topCounters = Object.entries(counters)
    .filter(([key, val]) => Number(val) > 0 && WOULD_KEYS.has(key))
    .slice(0, 3)
    .map(([key, val]) => `${key.replaceAll("_", " ")}: ${val}`)
    .join(" · ");
  return `<div class="preset-subsection shadow-plugin-card shadow-plugin-card--${verdictCls}">
    <h3>${escapeHtml(p.label || p.id)} ${modeBadge(p.mode)} <small class="muted">${escapeHtml(p.scope || "")}</small></h3>
    <p class="shadow-plugin-verdict"><strong>${escapeHtml(verdict.label)}</strong> — ${escapeHtml(verdict.detail)}</p>
    ${topCounters ? `<p class="muted shadow-plugin-counters">${escapeHtml(topCounters)}</p>` : ""}
  </div>`;
}

function paintShadowSnapshot(stateName, opts = {}) {
  paintSystemPanelSnapshot("shadowScoreboardSnapshot", "shadowScoreboardSection", stateName, {
    hint: "Plugin path: OFF → SHADOW → LIVE",
    kpis: [
      { label: "WOULD-HAVE", sub: "actions", value: opts.would ?? "—", tone: opts.would > 0 ? "warn" : "success" },
      { label: "PLUGINS", sub: "tracked", value: opts.pluginCount ?? "—", tone: "neutral" },
      { label: "VERDICT", sub: "rollout", value: opts.modeLabel || "—", tone: opts.would > 0 ? "warn" : "success" },
    ],
    lines: [opts.title, opts.detail].filter(Boolean),
  });
}

export function renderShadowScoreboardPanel(panel, data, error) {
  if (!panel) return;
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
    return;
  }
  const prior = readPriorSnapshot();
  const meta = [];
  if (data.scan_at) meta.push(`Last scan: ${escapeHtml(String(data.scan_at))}`);
  if (data.execution_window_days) {
    meta.push(`Execution window: ${data.execution_window_days}d (${data.execution_days_present || 0} days with data)`);
  }
  const metaHtml = meta.length ? `<div class="muted" style="margin-bottom: 8px;">${meta.join(" · ")}</div>` : "";
  const html =
    metaHtml +
    renderPromotionLegend() +
    renderHeadline(data, prior) +
    data.plugins.map((p) => renderPlugin(p)).join("");
  paintSystemPanelSuccess(panel, html);
  const total = totalWouldHave(data.plugins || []);
  const statusState = total > 0 ? "partial" : "success";
  setSystemStatusStrip(
    "shadowScoreboardStatusStrip",
    statusState,
    `${total} would-have action${total === 1 ? "" : "s"}.`,
    total > 0
      ? "Review shadow verdicts before promoting any plugin to LIVE."
      : "No trial-run friction this window; base-signal PF gates still apply.",
  );
  paintShadowSnapshot(statusState, {
    would: total,
    pluginCount: data.plugins.length,
    modeLabel: total > 0 ? "review" : "clear",
    title: `${total} would-have action${total === 1 ? "" : "s"}.`,
    detail:
      total > 0
        ? "Review shadow verdicts before promoting any plugin to LIVE."
        : "No trial-run friction this window; base-signal PF gates still apply.",
  });
  writePriorSnapshot(data);
}

export async function refreshShadowScoreboard() {
  const panel = document.getElementById("shadowScoreboardPanel");
  if (!panel) return;
  syncSystemSectionState("shadowScoreboardSection", "loading");
  setSystemStatusStrip(
    "shadowScoreboardStatusStrip",
    "loading",
    "Loading shadow scoreboard.",
    "Fetching last-scan plugin would-have counters.",
  );
  paintShadowSnapshot("loading", {
    title: "Loading shadow scoreboard.",
    detail: "Fetching last-scan plugin would-have counters.",
  });
  panel.innerHTML = `<div class="async-state async-state--loading muted" role="status">
    <span class="async-spinner" aria-hidden="true"></span>
    <span>Loading shadow scoreboard…</span>
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
