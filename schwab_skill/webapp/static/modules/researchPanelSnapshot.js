/**
 * Shared 5-state contract for Research panels (Wave 3).
 *
 * Mirrors Figma Research Approval Matrix: OUTPUT / DATA / ACTION tiles
 * plus a single Confidence meter (not reliability + execution).
 */

import { escapeHtml, safeNum, safeText } from "./format.js";
import { setPanelState } from "./operationsPanelState.js";
import { paintStatusStrip } from "./statusStripCore.js";

const VALID = new Set(["success", "partial", "empty", "loading", "error"]);

/** Defaults keyed by state for Figma Metric / Output · Data · Action cards. */
export const RESEARCH_SNAPSHOT_DEFAULTS = Object.freeze({
  success: {
    output: { label: "OUTPUT", value: "Ready", sub: "panel status", tone: "success" },
    data: { label: "DATA", value: "Fresh", sub: "provenance", tone: "success" },
    action: { label: "ACTION", value: "Pass", sub: "next step", tone: "success" },
    confidence: 82,
  },
  partial: {
    output: { label: "OUTPUT", value: "Partial", sub: "panel status", tone: "warn" },
    data: { label: "DATA", value: "Limited", sub: "provenance", tone: "warn" },
    action: { label: "ACTION", value: "Review", sub: "next step", tone: "warn" },
    confidence: 58,
  },
  empty: {
    output: { label: "OUTPUT", value: "None", sub: "panel status", tone: "neutral" },
    data: { label: "DATA", value: "—", sub: "provenance", tone: "neutral" },
    action: { label: "ACTION", value: "Wait", sub: "next step", tone: "neutral" },
    confidence: 0,
  },
  loading: {
    output: { label: "OUTPUT", value: "…", sub: "panel status", tone: "loading" },
    data: { label: "DATA", value: "…", sub: "provenance", tone: "loading" },
    action: { label: "ACTION", value: "Wait", sub: "next step", tone: "loading" },
    confidence: 28,
  },
  error: {
    output: { label: "OUTPUT", value: "—", sub: "panel status", tone: "bad" },
    data: { label: "DATA", value: "—", sub: "provenance", tone: "bad" },
    action: { label: "ACTION", value: "Retry", sub: "next step", tone: "bad" },
    confidence: 0,
  },
});

function toneClass(tone) {
  const t = safeText(tone || "neutral").toLowerCase();
  if (["success", "good", "ready"].includes(t)) return "success";
  if (["warn", "partial", "degraded", "limited"].includes(t)) return "warn";
  if (["bad", "error", "unavailable"].includes(t)) return "bad";
  if (["loading"].includes(t)) return "loading";
  return "neutral";
}

function formatKpiValue(value, stateName) {
  if (stateName === "loading") return "…";
  if (stateName === "error") return "—";
  if (value === null || value === undefined || value === "") return "—";
  return safeText(value);
}

function meterWidth(value, stateName) {
  if (stateName === "loading") return 28;
  if (stateName === "error") return 0;
  const n = safeNum(value, NaN);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, n));
}

function mergeTile(defaults, override, label) {
  const base = defaults || {};
  const patch = override && typeof override === "object" ? override : {};
  return {
    label: safeText(patch.label || base.label || label),
    value: patch.value !== undefined ? patch.value : base.value,
    sub: safeText(patch.sub !== undefined ? patch.sub : base.sub || ""),
    tone: safeText(patch.tone || base.tone || "neutral"),
  };
}

/**
 * @param {string} sectionId
 * @param {string} stateName
 */
export function syncResearchSectionState(sectionId, stateName) {
  if (!sectionId) return;
  setPanelState(sectionId, stateName);
  const el = document.getElementById(sectionId);
  if (!el) return;
  const asyncMap = {
    loading: "loading",
    error: "error",
    empty: "empty",
    partial: "success",
    success: "success",
  };
  const state = VALID.has(stateName) ? stateName : "empty";
  el.setAttribute("data-async-state", asyncMap[state] || state);
  return state;
}

/**
 * Paint Figma Wave 3 research snapshot (3 metrics + confidence).
 *
 * @param {string} containerId
 * @param {string|null} sectionId
 * @param {string} stateName
 * @param {object} config
 */
export function paintResearchPanelSnapshot(containerId, sectionId, stateName, config = {}) {
  const state = VALID.has(stateName) ? stateName : "empty";
  syncResearchSectionState(sectionId, state);
  const el = document.getElementById(containerId);
  if (!el) return state;

  if (config.hidden) {
    el.hidden = true;
    el.innerHTML = "";
    el.dataset.state = state;
    return state;
  }

  el.hidden = false;
  el.dataset.state = state;

  const defaults = RESEARCH_SNAPSHOT_DEFAULTS[state] || RESEARCH_SNAPSHOT_DEFAULTS.empty;
  const tiles = [
    mergeTile(defaults.output, config.output, "OUTPUT"),
    mergeTile(defaults.data, config.data, "DATA"),
    mergeTile(defaults.action, config.action, "ACTION"),
  ];
  const confidence =
    config.confidence !== undefined && config.confidence !== null
      ? safeNum(config.confidence, defaults.confidence)
      : defaults.confidence;
  const width = meterWidth(confidence, state);
  const confidenceDisplay =
    state === "loading"
      ? "…"
      : state === "error" || !Number.isFinite(safeNum(confidence, NaN))
        ? "—"
        : `${Math.round(safeNum(confidence, 0))}%`;
  const lines = (Array.isArray(config.lines) ? config.lines : []).filter(Boolean).slice(0, 3);
  const hint = safeText(config.hint || "");

  el.innerHTML = `
    ${hint ? `<p class="research-panel-snapshot__hint muted">${escapeHtml(hint)}</p>` : ""}
    <div class="research-panel-snapshot__kpis" aria-label="Research panel metrics">
      ${tiles
        .map(
          (tile) => `
        <div class="research-panel-snapshot__kpi" data-tone="${escapeHtml(toneClass(tile.tone))}">
          <span class="research-panel-snapshot__kpi-label">${escapeHtml(tile.label)}</span>
          <strong class="research-panel-snapshot__kpi-value mono-nums">${escapeHtml(formatKpiValue(tile.value, state))}</strong>
          <small class="research-panel-snapshot__kpi-sub muted">${escapeHtml(tile.sub)}</small>
        </div>`,
        )
        .join("")}
    </div>
    <div class="research-panel-snapshot__meters" aria-label="Confidence">
      <div class="research-panel-snapshot__meter">
        <span class="research-panel-snapshot__meter-label">Confidence ${escapeHtml(confidenceDisplay)}</span>
        <div class="meter info"><span style="width:${width}%"></span></div>
      </div>
    </div>
    ${
      lines.length
        ? `<ul class="research-panel-snapshot__lines">${lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>`
        : ""
    }
  `;
  return state;
}

/**
 * Paint status strip + research snapshot together (Wave 3 contract).
 *
 * @param {object} opts
 * @param {string} opts.stripId
 * @param {string} opts.snapshotId
 * @param {string} opts.sectionId
 * @param {string} opts.stateName
 * @param {string} opts.title
 * @param {string} [opts.detail]
 * @param {string} [opts.hint]
 * @param {object} [opts.output]
 * @param {object} [opts.data]
 * @param {object} [opts.action]
 * @param {number|null} [opts.confidence]
 * @param {string[]} [opts.lines]
 */
export function paintResearchSurface(opts = {}) {
  const stateName = VALID.has(opts.stateName) ? opts.stateName : "empty";
  const title = safeText(opts.title || "");
  const detail = safeText(opts.detail || "");
  const strip = document.getElementById(opts.stripId);
  if (strip) {
    paintStatusStrip(strip, stateName, title, detail, "research-status-pill");
  }
  paintResearchPanelSnapshot(opts.snapshotId, opts.sectionId, stateName, {
    hint: opts.hint || "",
    output: opts.output,
    data: opts.data,
    action: opts.action,
    confidence: opts.confidence,
    lines: opts.lines || [title, detail].filter(Boolean),
    hidden: opts.hidden === true,
  });
  return stateName;
}
