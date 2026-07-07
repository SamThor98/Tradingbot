import { escapeHtml, safeNum, safeText, timeAgo } from "../modules/format.js";
import { decorateGlossary } from "../modules/glossary.js";
import { healthBadgeClass } from "../modules/logger.js";
import { paintStatusStrip } from "../modules/statusStripCore.js";
import {
  syncDecisionDashboardState,
  syncDecisionSignalEdgeState,
} from "../modules/operationsPanelState.js";
import {
  renderDecisionGateTiles,
  renderDecisionPfChart,
  renderDecisionSummaryStrip,
  summarizeDecisionGates,
} from "./decisionCharts.js";

function _setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

const EVIDENCE_ROW_IDS = [
  "decisionValidationStatus",
  "decisionSloStatus",
  "decisionLastScan",
  "decisionSignalsFound",
  "decisionStrategyLead",
  "decisionDataQuality",
  "decisionSignalEdgeState",
  "decisionEarlyStopConstraint",
  "decisionRankFilterShadow",
  "decisionEntryTimingExperiment",
  "decisionLatestPromotion",
  "decisionAblationStatus",
  "decisionAblationLift",
  "decisionAblationSummary",
];

function _setBadge(id, text, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = healthBadgeClass(ok);
  el.textContent = text;
}

function _fmtSignedPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const scaled = n * 100;
  return `${scaled >= 0 ? "+" : ""}${scaled.toFixed(1)}%`;
}

function _dataQualityState(value) {
  const dq = safeText(value || "").trim().toLowerCase();
  if (!dq || ["unknown", "—"].includes(dq)) return "empty";
  if (["ok", "fresh", "healthy", "good"].includes(dq)) return "success";
  if (["failed", "fail", "blocked", "conflict"].includes(dq)) return "error";
  return "partial";
}

function _hasDecisionEvidence(payload = {}) {
  const reliability = payload.reliability || {};
  const strategy = payload.strategy_quality || {};
  const signalEdge = payload.signal_edge || {};
  const stack = signalEdge.signal_stack_counterfactual || {};
  return Boolean(
    reliability.validation_run_status ||
      strategy.last_scan_at ||
      signalEdge.state ||
      Number.isFinite(Number(stack.pf_mean)) ||
      Number.isFinite(Number(signalEdge.hold_21_40d_pf)),
  );
}

function _setStateStrip(stateName, title, detail) {
  const strip = document.getElementById("decisionDashboardStateStrip");
  if (!strip) return;
  paintStatusStrip(strip, stateName, title, detail, "decision-dashboard-state-pill");
  syncDecisionDashboardState(stateName);
  syncDecisionSignalEdgeState(stateName);
}

function _renderSignalEdgeSummary(signalEdge, readiness, decisionState) {
  const el = document.getElementById("decisionSignalEdgeSummary");
  if (!el) return;
  const summary = summarizeDecisionGates(signalEdge, readiness, decisionState);
  el.dataset.state = summary.state;
  // Glossary tooltips on PF/gate jargon (plain-language companion, F17).
  el.innerHTML = decorateGlossary(escapeHtml(summary.text));
}

function _syncDecisionAsyncState(stateName) {
  const card = document.getElementById("decisionDashboardCard");
  if (!card) return;
  if (stateName === "loading") {
    card.setAttribute("data-async-state", "loading");
    return;
  }
  if (stateName === "error") {
    card.setAttribute("data-async-state", "error");
    return;
  }
  if (stateName === "empty") {
    card.setAttribute("data-async-state", "empty");
    return;
  }
  card.setAttribute("data-async-state", "success");
}

function _decisionState(payload = {}) {
  if (!_hasDecisionEvidence(payload)) {
    return {
      state: "empty",
      title: "No decision evidence yet.",
      detail: "Run validation, signal-stack counterfactuals, or strategy comparison to populate this board.",
    };
  }
  const reliability = payload.reliability || {};
  const strategy = payload.strategy_quality || {};
  const readiness = payload.promotion_readiness || {};
  const signalEdge = payload.signal_edge || {};
  const stack = signalEdge.signal_stack_counterfactual || {};
  const dataState = _dataQualityState(strategy.data_quality);
  const validationPassed = reliability.validation_passed === true;
  const sloPassed = reliability.slo_gate_passed === true;
  const releaseReady = readiness.release_gate_ready === true;
  const stackPass = stack.passes_promotion_gates === true;
  const pfMean = safeNum(stack.pf_mean ?? signalEdge.hold_21_40d_pf, NaN);
  const worstEra = safeNum(stack.worst_era_pf, NaN);
  if (dataState === "error") {
    return {
      state: "error",
      title: "Decision data quality is blocked.",
      detail: `Data quality: ${safeText(strategy.data_quality)}. Resolve market-data or artifact issues before promotion.`,
    };
  }
  if (releaseReady && validationPassed && sloPassed && (stackPass || Number.isFinite(pfMean)) && dataState === "success") {
    return {
      state: "success",
      title: "Release gates are ready.",
      detail: `Validation and SLO pass; PF ${Number.isFinite(pfMean) ? pfMean.toFixed(2) : "—"} / worst ${Number.isFinite(worstEra) ? worstEra.toFixed(2) : "—"}.`,
    };
  }
  const blockers = [];
  if (!releaseReady) blockers.push("release gate blocked");
  if (!validationPassed) blockers.push("validation not passed");
  if (!sloPassed) blockers.push("SLO at risk");
  if (dataState === "partial") blockers.push(`data ${safeText(strategy.data_quality || "degraded")}`);
  if (stack.passes_promotion_gates === false) blockers.push("PF gate below threshold");
  return {
    state: "partial",
    title: "Promotion needs review.",
    detail: blockers.slice(0, 3).join(" · ") || "Some evidence is incomplete; review gates before promotion.",
  };
}

export function renderDecisionDashboardLoading() {
  _setStateStrip(
    "loading",
    "Loading decision summary.",
    "Fetching signal-edge gates, validation health, and promotion readiness.",
  );
  _syncDecisionAsyncState("loading");
  const loadingOpts = { state: "loading" };
  renderDecisionSummaryStrip(document.getElementById("decisionDashboardSummaryStrip"), null, loadingOpts);
  renderDecisionGateTiles(document.getElementById("decisionGateTiles"), {}, {}, loadingOpts);
  renderDecisionPfChart(document.getElementById("decisionEraPfChart"), {}, loadingOpts);
  _renderSignalEdgeSummary({}, {}, loadingOpts);
  _setBadge("decisionReliabilityState", "Loading", false);
  _setBadge("decisionPromotionState", "Loading", false);
  EVIDENCE_ROW_IDS.forEach((id) => _setText(id, `${document.getElementById(id)?.textContent?.split(":")[0] || "Status"}: loading…`));
}

export function renderDecisionDashboardUnavailable(message, onRetry) {
  const msg = safeText(message || "Decision dashboard unavailable.");
  _setStateStrip("error", "Decision data unavailable.", msg);
  _syncDecisionAsyncState("error");
  const errorOpts = { state: "error", message: msg };
  renderDecisionSummaryStrip(document.getElementById("decisionDashboardSummaryStrip"), null, errorOpts);
  renderDecisionGateTiles(document.getElementById("decisionGateTiles"), {}, {}, errorOpts);
  renderDecisionPfChart(document.getElementById("decisionEraPfChart"), {}, errorOpts);
  _renderSignalEdgeSummary({}, {}, errorOpts);
  // Retry affordance for the idempotent dashboard GET.
  if (typeof onRetry === "function") {
    const strip = document.getElementById("decisionDashboardStateStrip");
    if (strip && !strip.querySelector("[data-decision-retry]")) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn small secondary";
      btn.setAttribute("data-decision-retry", "");
      btn.textContent = "Retry";
      btn.addEventListener("click", () => void onRetry());
      strip.appendChild(btn);
    }
  }
}

function _renderAblationTop(topRows = []) {
  const listEl = document.getElementById("decisionAblationTopList");
  const wrapEl = document.getElementById("decisionAblationTopWrap");
  if (!listEl || !wrapEl) return;
  listEl.innerHTML = "";
  if (!Array.isArray(topRows) || !topRows.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No strategy variants available.";
    listEl.appendChild(li);
    wrapEl.open = false;
    return;
  }
  topRows.slice(0, 5).forEach((row) => {
    const li = document.createElement("li");
    const id = safeText(row.variant_id || "variant");
    const lift = _fmtSignedPct(row.relative_lift_vs_baseline);
    const pass = row.pass === true ? "pass" : "fail";
    const flags = Array.isArray(row.regression_flags) ? row.regression_flags : [];
    const flagNote = flags.length ? ` | flags: ${flags.slice(0, 2).join(", ")}` : "";
    li.textContent = `${id}: ${lift} (${pass})${flagNote}`;
    listEl.appendChild(li);
  });
}

export function renderDecisionDashboard(payload = {}) {
  const reliability = payload.reliability || {};
  const strategy = payload.strategy_quality || {};
  const readiness = payload.promotion_readiness || {};
  const ablation = payload.ablation || {};
  const signalEdge = payload.signal_edge || {};
  const liveShadow = strategy.signal_edge_shadow || {};

  const validationPassed = reliability.validation_passed === true;
  const sloPassed = reliability.slo_gate_passed === true;
  const releaseReady = readiness.release_gate_ready === true;
  const decisionState = _decisionState(payload);

  _setStateStrip(decisionState.state, decisionState.title, decisionState.detail);
  _syncDecisionAsyncState(decisionState.state);

  _setBadge("decisionReliabilityState", validationPassed && sloPassed ? "Healthy" : "At Risk", validationPassed && sloPassed);
  _setBadge("decisionPromotionState", releaseReady ? "Ready" : "Blocked", releaseReady);

  renderDecisionSummaryStrip(document.getElementById("decisionDashboardSummaryStrip"), payload, decisionState);
  const edgeOpts = decisionState.state === "empty" ? { state: "empty" } : {};
  renderDecisionGateTiles(document.getElementById("decisionGateTiles"), signalEdge, readiness, edgeOpts);
  renderDecisionPfChart(document.getElementById("decisionEraPfChart"), signalEdge, edgeOpts);
  _renderSignalEdgeSummary(signalEdge, readiness, decisionState);

  const runStatus = safeText(reliability.validation_run_status || "unknown");
  const validationLine = `Validation: ${runStatus}${validationPassed ? " (pass)" : ""}`;
  _setText("decisionValidationStatus", validationLine);

  const sloFailures = Array.isArray(reliability.slo_failures) ? reliability.slo_failures : [];
  _setText(
    "decisionSloStatus",
    sloPassed ? "Reliability check: pass" : `Reliability check: ${sloFailures[0] ? safeText(sloFailures[0]) : "needs attention"}`
  );

  const lastScanAt = safeText(strategy.last_scan_at || "");
  _setText("decisionLastScan", lastScanAt ? `Last scan ${timeAgo(lastScanAt)}` : "Last scan unavailable");
  _setText("decisionSignalsFound", `${strategy.signals_found ?? 0} signals`);
  _setText(
    "decisionStrategyLead",
    safeText(strategy.dominant_strategy)
      ? `${safeText(strategy.dominant_strategy)} (${strategy.dominant_count ?? 0})`
      : "No dominant strategy"
  );
  _setText(
    "decisionDataQuality",
    safeText(strategy.data_quality) ? `Data quality: ${safeText(strategy.data_quality)}` : "Data quality: unknown"
  );

  const edgeState = safeText(signalEdge.state || "unknown");
  const earlyPct = Number(signalEdge.early_stopout_pct);
  const holdPf = Number(signalEdge.hold_21_40d_pf);
  const edgeParts = [`Signal edge: ${edgeState.replaceAll("_", " ")}`];
  if (Number.isFinite(earlyPct)) edgeParts.push(`early stops ${earlyPct.toFixed(1)}%`);
  if (Number.isFinite(holdPf)) edgeParts.push(`21-40d PF ${holdPf.toFixed(2)}`);
  _setText("decisionSignalEdgeState", edgeParts.join(" | "));

  const entryReason = safeText(
    signalEdge.entry_timing_reason || signalEdge.entry_quality_reason || "",
  );
  _setText(
    "decisionEarlyStopConstraint",
    entryReason
      ? `Entry constraint: ${entryReason.slice(0, 140)}${entryReason.length > 140 ? "…" : ""}`
      : "Entry constraint: run early-stop cohort analysis"
  );

  const rankRec = safeText(signalEdge.rank_filter_recommendation || "unknown");
  const liveDrop = Number(liveShadow.rank_filter_would_drop_any);
  const rankLine = [`Rank-filter shadow: ${rankRec.replaceAll("_", " ")}`];
  if (Number.isFinite(liveDrop) && liveDrop > 0) rankLine.push(`last scan would-drop ${liveDrop}`);
  _setText("decisionRankFilterShadow", rankLine.join(" | "));

  const experiment = signalEdge.entry_timing_experiment || signalEdge.offline_experiment_targets || {};
  const expRetention = Number(experiment.retention_pct ?? experiment.would_drop_retention_pct);
  const expEarly = Number(experiment.delta_early_stopout_pp);
  const expPf = Number(experiment.delta_overlap_pf_mean);
  const expParts = ["Entry-timing experiment: breakout buffer only (shadow)"];
  const experimentEnv = signalEdge.experiment_env || {};
  if (experimentEnv.ready === true) {
    expParts.push("env ready");
  } else if (experimentEnv.ready === false) {
    expParts.push("env not configured");
  }
  if (Number.isFinite(expRetention)) expParts.push(`offline retain ${expRetention.toFixed(1)}%`);
  if (Number.isFinite(expEarly)) expParts.push(`d early ${expEarly >= 0 ? "+" : ""}${expEarly.toFixed(1)}pp`);
  if (Number.isFinite(expPf)) expParts.push(`d overlap PF ${expPf >= 0 ? "+" : ""}${expPf.toFixed(2)}`);
  const liveCompare = signalEdge.live_entry_shadow_compare || {};
  const livePct = Number(liveCompare.would_filter_pct);
  const liveVerdict = safeText(liveCompare.verdict || "");
  if (Number.isFinite(livePct)) {
    expParts.push(`live would-filter ${livePct.toFixed(1)}%`);
  }
  if (liveVerdict) expParts.push(`live/offline ${liveVerdict}`);
  _setText("decisionEntryTimingExperiment", expParts.join(" | "));

  const latestDecision = readiness.latest_decision || {};
  const decisionAt = safeText(latestDecision.recorded_at || "");
  if (decisionAt) {
    _setText(
      "decisionLatestPromotion",
      `${safeText(latestDecision.target || "promotion")} → ${safeText(latestDecision.decision || "unknown")} (${timeAgo(decisionAt)})`
    );
  } else {
    _setText("decisionLatestPromotion", "No promotion decision recorded yet");
  }

  const ablationExists = ablation.exists === true;
  const ablationBest = ablation.best || {};
  const ablationSummary = ablation.summary || {};
  if (!ablationExists) {
    _setText("decisionAblationStatus", "Strategy test: no report yet");
    _setText("decisionAblationLift", "Best lift: —");
    _setText("decisionAblationSummary", "Run a strategy comparison to populate this panel.");
    _renderAblationTop([]);
    return;
  }
  const bestId = safeText(ablationBest.variant_id || "unknown");
  const bestPass = ablationBest.pass === true;
  const bestLift = _fmtSignedPct(ablationBest.relative_lift_vs_baseline);
  const ciLo = _fmtSignedPct(ablationBest.ci_relative_lift_lower);
  const ciHi = _fmtSignedPct(ablationBest.ci_relative_lift_upper);
  const passCount = Number(ablationSummary.pass_count ?? 0);
  const failCount = Number(ablationSummary.fail_count ?? 0);
  const variantCount = Number(ablationSummary.variant_count ?? passCount + failCount);
  _setText("decisionAblationStatus", `Strategy test: ${bestPass ? "pass" : "needs review"} (${bestId})`);
  _setText("decisionAblationLift", `Best lift: ${bestLift} | 95% range ${ciLo} to ${ciHi}`);
  _setText(
    "decisionAblationSummary",
    `Summary: ${passCount} of ${variantCount} passing, ${failCount} flagged`
  );
  _renderAblationTop(ablation.top_variants || []);
}
