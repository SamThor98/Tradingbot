/**
 * Calibration panel — surfaces self-study and hypothesis-ledger
 * snapshots from `/api/calibration/summary`, plus the "trading halt"
 * toggle (SaaS-only).
 *
 * Layout follows Figma "Calibration — Success" (node 5:2).
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { escapeHtml, prettyJson } from "../modules/format.js";
import { updateActionCenter } from "../modules/logger.js";
import {
  renderCalibrationKpiTiles,
  renderCalibrationLedgerSources,
  renderCalibrationReliabilityDiagram,
  renderCalibrationSummaryStrip,
  updateCalibrationFreshness,
} from "./calibrationCharts.js";

function _hypothesisCalibration(data) {
  return (
    data.hypothesis_calibration ||
    (data.self_study && typeof data.self_study === "object"
      ? data.self_study.hypothesis_calibration
      : null)
  );
}

function _renderRawDetails(label, payload) {
  if (!payload || typeof payload !== "object") return "";
  return `
    <details class="tool-json-details calibration-raw-details">
      <summary>${escapeHtml(label)}</summary>
      <pre class="code-block code-block--tight">${escapeHtml(prettyJson(payload))}</pre>
    </details>
  `;
}

export function renderCalibrationChrome(data) {
  renderCalibrationSummaryStrip(document.getElementById("calibrationSummaryStrip"), data);
  updateCalibrationFreshness(document.getElementById("calibrationFresh"), data);
}

export function renderCalibrationPanel(panel, data, error) {
  if (!panel) return;
  if (error) {
    panel.innerHTML = `<div class="report-empty">${escapeHtml(error)}</div>`;
    return;
  }
  if (!data) {
    panel.innerHTML = `<div class="report-empty">No data.</div>`;
    return;
  }
  renderCalibrationChrome(data);
  if (data.empty) {
    panel.innerHTML = `<div class="report-empty">${escapeHtml(data.hint || "No calibration snapshot yet.")}</div>`;
    return;
  }

  const ss = data.self_study;
  const hl = data.hypothesis_ledger;
  const hypothesisCalibration = _hypothesisCalibration(data);
  const parts = [];

  if (ss) {
    parts.push(`
      <section class="calibration-block" aria-label="Self-study KPIs">
        <div id="calibrationKpiTiles" class="decision-gate-tiles calibration-kpi-tiles"></div>
      </section>
    `);
  }

  if (hypothesisCalibration) {
    parts.push(`
      <section class="calibration-block" aria-label="Hypothesis reliability">
        <h3 class="calibration-board-title">Hypothesis reliability</h3>
        <div id="calibrationReliabilityChart"></div>
      </section>
    `);
  }

  if (hl) {
    parts.push(`
      <section class="calibration-block" aria-label="Hypothesis ledger">
        <h3 class="calibration-board-title">Hypothesis ledger</h3>
        <div id="calibrationLedgerSources"></div>
      </section>
    `);
  }

  const rawBlocks = [];
  if (ss) rawBlocks.push(_renderRawDetails("Self-study raw data", ss));
  if (hl) rawBlocks.push(_renderRawDetails("Ledger raw data", hl));
  if (rawBlocks.length) {
    parts.push(`<div class="calibration-raw-wrap">${rawBlocks.join("")}</div>`);
  }

  panel.innerHTML =
    parts.length > 0
      ? parts.join("")
      : `<div class="muted">No calibration data available yet.</div>`;

  if (ss) {
    renderCalibrationKpiTiles(document.getElementById("calibrationKpiTiles"), ss);
  }
  if (hypothesisCalibration) {
    renderCalibrationReliabilityDiagram(
      document.getElementById("calibrationReliabilityChart"),
      hypothesisCalibration,
    );
  }
  if (hl) {
    renderCalibrationLedgerSources(document.getElementById("calibrationLedgerSources"), hl);
  }
}

export async function refreshCalibration() {
  const panel = document.getElementById("calibrationPanel");
  const card = document.getElementById("calibrationSection");
  if (!panel) return;
  if (card) card.setAttribute("data-async-state", "loading");
  panel.innerHTML = `<div class="async-state async-state--loading muted" role="status">
    <span class="async-spinner" aria-hidden="true"></span>
    <span>Loading calibration snapshot…</span>
  </div>`;
  renderCalibrationSummaryStrip(document.getElementById("calibrationSummaryStrip"), {});
  const out = await api.get("/api/calibration/summary");
  if (!out.ok) {
    if (card) card.setAttribute("data-async-state", "error");
    const msg = out.user_message || out.error || "Request failed";
    panel.innerHTML = `<div class="async-state async-state--error" role="alert">
      <span>Calibration load failed: ${escapeHtml(String(msg))}</span>
      <button type="button" class="btn small secondary" data-calib-retry>Retry</button>
    </div>`;
    panel.querySelector("[data-calib-retry]")?.addEventListener("click", () => void refreshCalibration());
    updateCalibrationFreshness(document.getElementById("calibrationFresh"), { empty: true });
    return;
  }
  state.calibration = out.data;
  if (card) card.setAttribute("data-async-state", out.data?.empty ? "empty" : "success");
  renderCalibrationPanel(panel, out.data, null);
}

export async function submitTradingHaltSave({ refreshAccountMe = async () => {} } = {}) {
  if (!state.publicConfig.saas_mode) return;
  const halted = Boolean(document.getElementById("tradingHaltedCheckbox")?.checked);
  const out = await api.patch("/api/settings/trading-halt", { halted });
  if (!out.ok) {
    const msg =
      out.user_message ||
      (typeof out.error === "string" ? out.error : JSON.stringify(out.error || "Request failed"));
    updateActionCenter({ title: "Trading pause", message: msg, severity: "error" });
    return;
  }
  updateActionCenter({
    title: halted ? "Trading paused" : "Trading pause cleared",
    message: halted
      ? "New live approvals are blocked until you turn this off."
      : "You may approve live trades again when live trading is enabled.",
    severity: "success",
  });
  await refreshAccountMe();
}
