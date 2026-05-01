import { safeText, timeAgo } from "../modules/format.js";
import { healthBadgeClass } from "../modules/logger.js";

function _setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function _setBadge(id, text, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = healthBadgeClass(ok);
  el.textContent = text;
}

export function renderDecisionDashboard(payload = {}) {
  const reliability = payload.reliability || {};
  const strategy = payload.strategy_quality || {};
  const readiness = payload.promotion_readiness || {};

  const validationPassed = reliability.validation_passed === true;
  const sloPassed = reliability.slo_gate_passed === true;
  const releaseReady = readiness.release_gate_ready === true;

  _setBadge("decisionReliabilityState", validationPassed && sloPassed ? "Healthy" : "At Risk", validationPassed && sloPassed);
  _setBadge("decisionPromotionState", releaseReady ? "Ready" : "Blocked", releaseReady);

  const runStatus = safeText(reliability.validation_run_status || "unknown");
  const validationLine = `Validation: ${runStatus}${validationPassed ? " (pass)" : ""}`;
  _setText("decisionValidationStatus", validationLine);

  const sloFailures = Array.isArray(reliability.slo_failures) ? reliability.slo_failures : [];
  _setText(
    "decisionSloStatus",
    sloPassed ? "SLO gate: pass" : `SLO gate: ${sloFailures[0] ? safeText(sloFailures[0]) : "failing"}`
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
}
