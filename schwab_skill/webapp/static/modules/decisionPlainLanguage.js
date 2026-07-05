import { safeText } from "./format.js";

const REASON_LABELS = Object.freeze({
  composite_score: "Signal score",
  reliability: "Reliability",
  confidence: "Confidence",
  strategy: "Strategy",
  rank_score_v2: "Rank score",
  rank_score: "Rank score",
  rank_basis: "Rank basis",
  mirofish_conviction: "Conviction",
  event_risk: "Event risk",
});

/** Turn `composite_score=55.1` into plain English for decision UI. */
export function formatDecisionReason(raw) {
  const s = safeText(raw).trim();
  if (!s) return "";
  const eq = s.indexOf("=");
  if (eq > 0) {
    const key = s.slice(0, eq).trim().toLowerCase();
    const val = s.slice(eq + 1).trim().replace(/_/g, " ");
    const label = REASON_LABELS[key] || key.replace(/_/g, " ");
    return `${label}: ${val}`;
  }
  return s.replace(/_/g, " ");
}

export function formatConfidenceBucket(bucket) {
  const b = safeText(bucket).trim().toLowerCase();
  if (!b || b === "unknown") return "Unknown";
  return b.charAt(0).toUpperCase() + b.slice(1);
}

export function formatStrategyName(raw) {
  return safeText(raw).trim().replace(/_/g, " ") || "Unknown";
}
