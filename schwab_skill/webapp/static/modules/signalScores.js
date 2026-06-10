/**
 * Pure score/probability accessors for signal rows. Shared by the scan
 * results table (app.js) and the pending board (panels/pendingBoard.js),
 * so the same row renders identical scores on both surfaces.
 *
 * Extracted from app.js per the module decomposition policy in
 * docs/FRONTEND_DESIGN_SYSTEM.md.
 */

import { safeText } from "./format.js";

export function optionalNum(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed || trimmed === "—") return null;
  }
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function normalizeProbability(value) {
  const n = optionalNum(value);
  if (n === null) return null;
  // Backward compatibility for older payloads that persisted percent points (e.g. 62.4).
  const ratio = n > 1 && n <= 100 ? n / 100 : n;
  return Math.max(0, Math.min(1, ratio));
}

export function formatConfidenceLabel(value) {
  const raw = safeText(value || "").trim();
  if (!raw || raw === "—") return "—";
  const lowered = raw.toLowerCase();
  if (lowered === "unknown" || lowered === "none" || lowered === "null") return "—";
  return raw.replace(/[_-]+/g, " ").toUpperCase();
}

export function getCompositeScore(row = {}) {
  return optionalNum(row.composite_score ?? row.signal_score ?? row.score);
}

export function getConvictionScore(row = {}) {
  return optionalNum(row.mirofish_conviction ?? row.conviction_score ?? row?.mirofish_result?.conviction_score);
}

export function getCalibratedPUp(row = {}) {
  const advisory = row.advisory || {};
  return normalizeProbability(
    row.p_up_calibrated ?? advisory.p_up_10d ?? advisory.p_up_10d_raw ?? row.p_up_10d ?? row.advisory_p_up,
  );
}

export function getReliabilityScore(row = {}) {
  const direct = optionalNum(row.reliability_score);
  if (direct !== null) return direct;
  const advisory = row.advisory || {};
  const bucket = formatConfidenceLabel(advisory.confidence_bucket ?? row.confidence_bucket ?? row.advisory_confidence);
  if (bucket === "HIGH") return 82;
  if (bucket === "MEDIUM") return 64;
  if (bucket === "LOW") return 46;
  return 35;
}

export function getEdgeScore(row = {}) {
  const direct = optionalNum(row.edge_score);
  if (direct !== null) return direct;
  return getCompositeScore(row);
}

export function getExecutionScore(row = {}) {
  const direct = optionalNum(row.execution_score);
  if (direct !== null) return direct;
  return 60;
}

export function getEv10d(row = {}) {
  return optionalNum(row.ev_10d);
}
