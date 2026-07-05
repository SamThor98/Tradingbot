import { escapeHtml, safeNum, safeText } from "./format.js";
import { setPanelState } from "./operationsPanelState.js";

const VALID_STATES = new Set(["success", "partial", "empty", "loading", "error"]);

function toneClass(tone) {
  const t = safeText(tone || "neutral").toLowerCase();
  if (["success", "good", "ready"].includes(t)) return "success";
  if (["warn", "partial", "degraded"].includes(t)) return "warn";
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
  if (stateName === "loading") return 44;
  if (stateName === "error") return 0;
  const n = safeNum(value, NaN);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, n));
}

function renderKpiTile(tile, stateName) {
  const tone = toneClass(tile.tone);
  return `
    <div class="operations-panel-snapshot__kpi" data-tone="${escapeHtml(tone)}">
      <span class="operations-panel-snapshot__kpi-label">${escapeHtml(tile.label || "—")}</span>
      <strong class="operations-panel-snapshot__kpi-value mono-nums">${escapeHtml(formatKpiValue(tile.value, stateName))}</strong>
      <small class="operations-panel-snapshot__kpi-sub muted">${escapeHtml(tile.sub || "")}</small>
    </div>
  `;
}

function renderMeter(label, value, stateName, meterClass = "") {
  const width = meterWidth(value, stateName);
  const display =
    stateName === "loading"
      ? "…"
      : stateName === "error" || !Number.isFinite(safeNum(value, NaN))
        ? "—"
        : String(Math.round(safeNum(value, 0)));
  return `
    <div class="operations-panel-snapshot__meter">
      <span class="operations-panel-snapshot__meter-label">${escapeHtml(label)} ${display}</span>
      <div class="meter ${meterClass}"><span style="width:${width}%"></span></div>
    </div>
  `;
}

/**
 * Mirror a 5-state strip onto a section container (kanban lane, card, etc.).
 *
 * @param {string} sectionId
 * @param {string} stateName
 */
export function syncOperationsSectionState(sectionId, stateName) {
  if (!sectionId) return;
  setPanelState(sectionId, stateName);
}

/**
 * Render the Figma-style operations snapshot: hint, 3 KPI tiles, reliability +
 * execution meters, and up to three summary lines.
 *
 * @param {string} containerId
 * @param {string|null} sectionId
 * @param {string} stateName
 * @param {object} config
 * @param {boolean} [config.hidden]
 * @param {string} [config.hint]
 * @param {Array<{label:string,sub?:string,value?:string|number,tone?:string}>} [config.kpis]
 * @param {{reliability?:number|null,execution?:number|null}} [config.meters]
 * @param {string[]} [config.lines]
 */
export function renderOperationsPanelSnapshot(containerId, sectionId, stateName, config = {}) {
  const el = document.getElementById(containerId);
  const state = VALID_STATES.has(stateName) ? stateName : "empty";
  syncOperationsSectionState(sectionId, state);
  if (!el) return;

  if (config.hidden) {
    el.hidden = true;
    el.innerHTML = "";
    el.dataset.state = state;
    return;
  }

  el.hidden = false;
  el.dataset.state = state;

  const kpis = Array.isArray(config.kpis) ? config.kpis.slice(0, 3) : [];
  while (kpis.length < 3) {
    kpis.push({ label: "—", sub: "", value: "—", tone: "neutral" });
  }

  const meters = config.meters || {};
  const lines = (Array.isArray(config.lines) ? config.lines : []).filter(Boolean).slice(0, 3);
  const hint = safeText(config.hint || "");

  el.innerHTML = `
    ${hint ? `<p class="operations-panel-snapshot__hint muted">${escapeHtml(hint)}</p>` : ""}
    <div class="operations-panel-snapshot__kpis" aria-label="Panel KPIs">
      ${kpis.map((tile) => renderKpiTile(tile, state)).join("")}
    </div>
    <div class="operations-panel-snapshot__meters" aria-label="Evidence meters">
      ${renderMeter("Reliability", meters.reliability, state, "info")}
      ${renderMeter("Execution", meters.execution, state)}
    </div>
    ${
      lines.length
        ? `<ul class="operations-panel-snapshot__lines">${lines.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>`
        : ""
    }
  `;
}

/**
 * Average a numeric getter across signal rows; returns null when no values.
 *
 * @param {object[]} rows
 * @param {(row: object) => number|null|undefined} getter
 */
export function averageSignalMetric(rows, getter) {
  if (!Array.isArray(rows) || !rows.length) return null;
  const vals = rows
    .map((row) => {
      const n = safeNum(getter(row), NaN);
      return Number.isFinite(n) ? n : NaN;
    })
    .filter((n) => Number.isFinite(n));
  if (!vals.length) return null;
  return vals.reduce((sum, n) => sum + n, 0) / vals.length;
}
