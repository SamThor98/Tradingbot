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

function _fmtSignedPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const scaled = n * 100;
  return `${scaled >= 0 ? "+" : ""}${scaled.toFixed(1)}%`;
}

function _renderAblationTop(topRows = []) {
  const listEl = document.getElementById("decisionAblationTopList");
  const wrapEl = document.getElementById("decisionAblationTopWrap");
  if (!listEl || !wrapEl) return;
  listEl.innerHTML = "";
  if (!Array.isArray(topRows) || !topRows.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "No ablation variants available.";
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

  const ablationExists = ablation.exists === true;
  const ablationBest = ablation.best || {};
  const ablationSummary = ablation.summary || {};
  if (!ablationExists) {
    _setText("decisionAblationStatus", "Ablation: no report yet");
    _setText("decisionAblationLift", "Best lift: —");
    _setText("decisionAblationSummary", "Ablation summary: run ablation cycle to populate this panel.");
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
  _setText("decisionAblationStatus", `Ablation: ${bestPass ? "pass" : "at risk"} (${bestId})`);
  _setText("decisionAblationLift", `Best lift: ${bestLift} | 95% CI ${ciLo} to ${ciHi}`);
  _setText(
    "decisionAblationSummary",
    `Ablation summary: ${passCount}/${variantCount} passing, ${failCount} flagged`
  );
  _renderAblationTop(ablation.top_variants || []);
}
