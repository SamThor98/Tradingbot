/**
 * Funnel stage → scan row filter mapping for interactive diagnostics drill-down.
 */

import { safeText } from "./format.js";

/** Funnel stage keys from buildFunnelStages (scanDiagnostics.js). */
export const FUNNEL_EARLY_STAGES = new Set(["watchlist", "regime_gate", "stage2", "vcp", "sector", "stage_a"]);

const STATUS_TO_STAGE = Object.freeze({
  trimmed_top_n: "final",
  filtered_quality_gates: "quality",
  filtered_self_study: "quality",
  filtered_confluence: "quality",
  filtered_event_risk: "quality",
  filtered_meta_policy: "quality",
  filtered_ensemble: "quality",
});

const REASON_TO_STAGE = Object.freeze({
  weak_breakout_volume: "quality",
  forensic_sloan_high: "quality",
  forensic_beneish_manipulator: "quality",
  forensic_altman_distress: "quality",
  pead_negative_surprise: "quality",
  pead_large_negative_surprise: "quality",
  low_signal_score: "quality",
  missing_confluence: "quality",
  confluence_advisory_low: "quality",
  confluence_pead_missing: "quality",
  event_risk_earnings: "quality",
  event_risk_fomc: "quality",
  meta_policy_uncertainty: "quality",
  ensemble_disagreement: "quality",
  self_study_low_conviction: "quality",
  primary_provider_fallback: "provider",
  bars_stale: "provider",
});

/**
 * @param {object} sig
 * @returns {string|null}
 */
export function inferFunnelStageFromSignal(sig = {}) {
  const status = safeText(sig._filter_status || "kept").toLowerCase();
  if (status === "kept") return "final";
  if (STATUS_TO_STAGE[status]) return STATUS_TO_STAGE[status];
  const reasons = Array.isArray(sig._filter_reasons) ? sig._filter_reasons : [];
  for (const code of reasons) {
    const mapped = REASON_TO_STAGE[safeText(code).toLowerCase()];
    if (mapped) return mapped;
  }
  if (status.startsWith("filtered_")) return "quality";
  return "final";
}

/**
 * @param {object} sig
 * @param {string|null} stageKey
 * @returns {boolean}
 */
export function signalMatchesFunnelStage(sig, stageKey) {
  if (!stageKey) return true;
  if (stageKey === "watchlist") return true;
  if (FUNNEL_EARLY_STAGES.has(stageKey) && stageKey !== "stage_a" && stageKey !== "provider") {
    return false;
  }
  return inferFunnelStageFromSignal(sig) === stageKey;
}

/**
 * @param {object[]} signals
 * @param {string|null} stageKey
 * @returns {object[]}
 */
export function filterSignalsByFunnelStage(signals, stageKey) {
  if (!stageKey) return signals;
  return (Array.isArray(signals) ? signals : []).filter((sig) => signalMatchesFunnelStage(sig, stageKey));
}

/**
 * @param {string} stageKey
 * @param {object} diag
 * @returns {string}
 */
export function funnelStageFilterHint(stageKey, diag = {}) {
  if (!stageKey) return "";
  if (FUNNEL_EARLY_STAGES.has(stageKey) && stageKey !== "stage_a" && stageKey !== "provider") {
    const filteredMap = {
      stage2: diag.stage2_fail,
      vcp: diag.vcp_fail,
      sector: (Number(diag.no_sector_etf) || 0) + (Number(diag.sector_not_winning) || 0),
      regime_gate: diag.scan_blocked,
    };
    const n = Number(filteredMap[stageKey] ?? 0) || 0;
    return n > 0
      ? `${n} ticker(s) removed at this pipeline step before the shortlist — open Near-miss for post-shortlist filters.`
      : "No tickers recorded at this step for the current scan.";
  }
  return "Showing near-miss rows matching this funnel step. Click the stage again to clear.";
}
