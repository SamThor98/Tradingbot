/**
 * Shared "can I act" pills for Today and System.
 * Reads the last scan diagnostics and the decision-dashboard payload already
 * stored on state. Does not fetch.
 */

import { state } from "./state.js";
import { escapeHtml, safeNum, safeText } from "./format.js";
import { dataQualityChip } from "./marketDataFix.js";
import { humanizeKey } from "./humanize.js";

const PF_MEAN_GATE = 1.2;
const WORST_ERA_GATE = 1.0;

function finite(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/**
 * @param {object} diag
 * @param {object|null} decision
 * @returns {{label: string, tone: string}[]}
 */
export function buildCanActPills(diag = {}, decision = null) {
  const pills = [];
  const dq = safeText(diag.data_quality || "").trim();
  const dqChip = dataQualityChip(diag);
  if (dqChip) {
    pills.push(dqChip);
  } else if (dq && dq !== "—") {
    const ok = ["ok", "fresh", "healthy", "good"].includes(dq.toLowerCase());
    pills.push({ label: `Data ${dq}`, tone: ok ? "good" : "bad" });
  }
  if (dq || diag.scan_blocked != null) {
    const blocked = safeNum(diag.scan_blocked, 0) > 0;
    pills.push({ label: blocked ? "Regime blocked" : "Regime open", tone: blocked ? "bad" : "good" });
  }
  const extMode = safeText(diag.extension_sort_mode || "").toLowerCase();
  if (extMode) {
    pills.push({
      label: `Extension sort ${extMode}`,
      tone: extMode === "live" ? "bad" : "neutral",
    });
  }
  const liveKey = safeText(diag.extension_sort_live_sort_key || "").trim();
  if (liveKey) {
    pills.push({ label: `Live sort ${humanizeKey(liveKey)}`, tone: "neutral" });
  }
  const peadDrop = safeText(diag.pead_extension_drop_mode || "").toLowerCase();
  if (peadDrop) {
    const marked = finite(diag.pead_extension_drop_would_drop);
    const extra = marked !== null && marked > 0 ? ` · ${marked} marked` : "";
    pills.push({
      label: `PEAD extension ${peadDrop}${extra}`,
      tone: peadDrop === "live" ? "bad" : "neutral",
    });
  }
  const corr = safeText(diag.correlation_guard_mode || "").toLowerCase();
  if (corr) pills.push({ label: `Correlation ${corr}`, tone: "neutral" });
  const regime = safeText(diag.regime_v2_mode || "").toLowerCase();
  if (regime) pills.push({ label: `Regime v2 ${regime}`, tone: "neutral" });

  const edge = (decision && decision.signal_edge) || {};
  const stack = edge.signal_stack_counterfactual || {};
  const gates = stack.promotion_gates || {};
  const pfMean = finite(stack.pf_mean ?? edge.hold_21_40d_pf);
  const worst = finite(stack.worst_era_pf);
  const pfGate = finite(gates.pf_mean_min) ?? PF_MEAN_GATE;
  const worstGate = finite(gates.worst_era_pf_min) ?? WORST_ERA_GATE;
  if (pfMean !== null) {
    const short = pfGate - pfMean;
    const gap = short > 0.005 ? ` · ${short.toFixed(2)} short` : "";
    pills.push({
      label: `Stack mean PF ${pfMean.toFixed(2)} / ${pfGate.toFixed(2)}${gap}`,
      tone: pfMean >= pfGate ? "good" : "warn",
    });
  }
  if (worst !== null) {
    pills.push({
      label: `Stack worst era ${worst.toFixed(2)} / ${worstGate.toFixed(2)}`,
      tone: worst >= worstGate ? "good" : "warn",
    });
  }
  return pills;
}

function paint(elementId, pills, emptyLabel) {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (!pills.length) {
    el.innerHTML = `<span class="decision-summary-pill neutral">${escapeHtml(emptyLabel)}</span>`;
    return;
  }
  el.innerHTML = pills
    .map((pill) => {
      const title = pill.title ? ` title="${escapeHtml(pill.title)}"` : "";
      return `<span class="decision-summary-pill ${escapeHtml(pill.tone)}"${title}>${escapeHtml(pill.label)}</span>`;
    })
    .join("");
}

/** Paint Today and System from the latest scan and decision payload. */
export function renderCanActStrips() {
  const diag = state.lastScanDiagnostics && typeof state.lastScanDiagnostics === "object"
    ? state.lastScanDiagnostics
    : {};
  const decision = state.lastDecisionDashboard || null;
  const pills = buildCanActPills(diag, decision);
  paint("todayCanActStrip", pills, "Run a scan to see data quality, regime, and the promotion gap.");
  paint(
    "systemCanActStrip",
    pills,
    "Open Decide or run a scan to see the promotion gap.",
  );
}
