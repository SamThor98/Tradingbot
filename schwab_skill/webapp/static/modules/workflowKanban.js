import { safeText, safeNum, formatCount } from "./format.js";
import { state } from "./state.js";
import { setOperationsStatusStrip } from "./operationsStatus.js";
import { setPanelState } from "./operationsPanelState.js";
import { updateKanbanLaneSummaries } from "./kanbanLaneSummaries.js";

const STEP_IDS = Object.freeze({
  scan: "workflowStepScan",
  evaluate: "workflowStepEvaluate",
  approve: "workflowStepApprove",
});

const LANE_IDS = Object.freeze({
  scan: "scanSection",
  evaluate: "scanDetailPanel",
  approve: "pendingSection",
});

function setStepState(stepKey, stepState) {
  const el = document.getElementById(STEP_IDS[stepKey]);
  if (!el) return;
  el.dataset.state = stepState;
}

function setLaneFocus(laneKey, focused) {
  const el = document.getElementById(LANE_IDS[laneKey]);
  if (!el) return;
  if (focused) el.dataset.workflowFocus = "true";
  else delete el.dataset.workflowFocus;
}

function computeWorkflowContext(options = {}) {
  if (options.forceState === "loading") {
    return {
      state: "loading",
      title: options.title || "Scan running.",
      detail:
        options.detail ||
        "Workflow pauses here until the shortlist returns — then pick a row in Lane 2.",
      steps: { scan: "loading", evaluate: "pending", approve: "pending" },
      focusLane: "scan",
    };
  }

  const scanCount = Array.isArray(state.latestSignals) ? state.latestSignals.length : 0;
  const pendingCount = Number(state.lastPendingCount);
  const pendingKnown = Number.isFinite(pendingCount);
  const hasScan = Boolean(state.lastScanAt);
  const selected = safeText(state.selectedScanTicker || "").trim();
  const diag = state.lastScanDiagnostics || {};
  const dq = safeText(diag.data_quality || (hasScan ? "ok" : "")).toLowerCase();
  const blocked = safeNum(diag.scan_blocked, 0) > 0;
  const dqBad = ["failed", "stale", "conflict", "blocked"].includes(dq);
  const dqWarn = ["degraded", "partial", "unknown", "warning", "warn"].includes(dq);

  if (blocked || dqBad) {
    return {
      state: "error",
      title: "Workflow blocked.",
      detail: `Data ${dq || "unavailable"} — fix scan or quotes before approving.`,
      steps: { scan: "blocked", evaluate: "blocked", approve: "blocked" },
      focusLane: "scan",
    };
  }

  if (!hasScan) {
    return {
      state: "empty",
      title: "No scan this session.",
      detail: "Run scan → review a setup → approve staged trades.",
      steps: { scan: "active", evaluate: "pending", approve: "pending" },
      focusLane: "scan",
    };
  }

  const steps = {
    scan: "done",
    evaluate: pendingCount > 0 ? "done" : "active",
    approve: pendingCount > 0 ? "active" : "pending",
  };

  if (pendingCount > 0) {
    return {
      state: dqWarn || !pendingKnown ? "partial" : "success",
      title: `${formatCount(pendingCount)} trade(s) awaiting decision.`,
      detail: pendingKnown
        ? "Review pending approvals before placing live orders."
        : "Pending count still loading — refresh queue if this persists.",
      steps,
      focusLane: "approve",
    };
  }

  if (!pendingKnown || dqWarn) {
    return {
      state: "partial",
      title: "Workflow needs review.",
      detail: `Data ${dq || "unknown"} · ${scanCount} candidate(s) · queue ${pendingKnown ? pendingCount : "loading"}.`,
      steps,
      focusLane: selected ? "evaluate" : "scan",
    };
  }

  return {
    state: scanCount > 0 ? "success" : "empty",
    title:
      scanCount > 0
        ? `${scanCount} candidate(s) from last scan.`
        : "Scan finished with no kept candidates.",
    detail: selected
      ? `Reviewing ${selected} — queue from Lane 2 when ready.`
      : "Select a scan row to inspect evidence and stage a trade.",
    steps,
    focusLane: selected ? "evaluate" : "scan",
  };
}

/** W1d workflow chrome: strip, panel state, stepper, lane focus. */
export function updateWorkflowKanban(options = {}) {
  const ctx = computeWorkflowContext(options);
  setOperationsStatusStrip("workflowStatusStrip", ctx.state, ctx.title, ctx.detail);
  setPanelState("workflowPrimary", ctx.state);

  Object.keys(STEP_IDS).forEach((key) => {
    setStepState(key, ctx.steps[key] || "pending");
  });

  Object.keys(LANE_IDS).forEach((key) => {
    setLaneFocus(key, ctx.focusLane === key);
  });

  updateKanbanLaneSummaries(options);
  return ctx;
}
