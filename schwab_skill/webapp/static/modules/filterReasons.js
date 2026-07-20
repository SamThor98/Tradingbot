/**
 * Human-readable labels for scanner filter dispositions and gate reasons.
 * Shared by the scan table, near-miss panel, and staging checklist.
 */

import { safeText } from "./format.js";

/** Map `_filter_reasons` codes to plain English. */
const REASON_LABELS = Object.freeze({
  low_signal_score: "Signal score below minimum",
  weak_breakout_volume: "Breakout volume too weak",
  forensic_sloan_high: "Forensic: high Sloan accrual ratio",
  forensic_beneish_manipulator: "Forensic: Beneish manipulator flag",
  forensic_altman_distress: "Forensic: Altman distress zone",
  pead_negative_surprise: "PEAD: negative earnings surprise",
  pead_large_negative_surprise: "PEAD: large negative earnings surprise",
  missing_confluence: "Confluence: insufficient independent confirmations",
  confluence_advisory_low: "Confluence: advisory confidence too low",
  confluence_pead_missing: "Confluence: PEAD confirmation missing",
  event_risk_earnings: "Event risk: earnings window",
  event_risk_fomc: "Event risk: FOMC / macro event",
  meta_policy_uncertainty: "Meta-policy: uncertainty too high",
  ensemble_disagreement: "Strategy ensemble: models disagree",
  self_study_low_conviction: "Self-study: below learned conviction floor",
  primary_provider_fallback: "Data: non-primary market data provider",
  bars_stale: "Data: price bars are stale",
});

/** Status badge config keyed by `_filter_status`. */
const STATUS_BADGES = Object.freeze({
  kept: {
    label: "Tradeable",
    cls: "pill good",
    title: "Survived all filters and is eligible for trade staging.",
  },
  filtered_quality_gates: {
    label: "Quality gate",
    cls: "pill bad",
    title: "Dropped by quality gates (forensic / breakout-volume / score).",
  },
  filtered_self_study: {
    label: "Self-study",
    cls: "pill warn",
    title: "Dropped by self-study learned minimum conviction.",
  },
  filtered_confluence: {
    label: "Confluence",
    cls: "pill warn",
    title: "Dropped — needs more independent confirmations (PEAD + advisory).",
  },
  filtered_event_risk: {
    label: "Event risk",
    cls: "pill bad",
    title: "Suppressed by event-risk policy (earnings, FOMC, etc).",
  },
  filtered_meta_policy: {
    label: "Meta-policy",
    cls: "pill bad",
    title: "Suppressed by the meta-policy / uncertainty combiner.",
  },
  filtered_ensemble: {
    label: "Ensemble",
    cls: "pill warn",
    title: "Removed by the strategy ensemble step.",
  },
  trimmed_top_n: {
    label: "Top-N trim",
    cls: "pill neutral",
    title: "Survived gates but ranked below SIGNAL_TOP_N — kept for review.",
  },
});

function humanizeReasonCode(code) {
  const raw = safeText(code || "").trim();
  if (!raw) return "";
  if (REASON_LABELS[raw]) return REASON_LABELS[raw];
  return raw.replace(/[_-]+/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
}

/**
 * @param {string[]|null|undefined} reasons
 * @returns {string[]}
 */
export function formatFilterReasons(reasons) {
  if (!Array.isArray(reasons) || !reasons.length) return [];
  return reasons.map(humanizeReasonCode).filter(Boolean);
}

/**
 * Primary near-miss explanation: status label + first reason when present.
 * @param {string} status
 * @param {string[]|null|undefined} reasons
 */
export function formatNearMissSummary(status, reasons) {
  const badge = formatScanStatusBadge(status, reasons);
  const human = formatFilterReasons(reasons);
  if (human.length) return `${badge.label}: ${human[0]}`;
  return badge.label;
}

/**
 * @param {string} status
 * @param {string[]|null|undefined} reasons
 */
export function formatScanStatusBadge(status, reasons) {
  const safeStatus = safeText(status || "").toLowerCase();
  const human = formatFilterReasons(reasons);
  const reasonText = human.join("; ");
  const base = STATUS_BADGES[safeStatus];
  if (base) {
    const title =
      reasonText && safeStatus.startsWith("filtered")
        ? `${base.title} Reasons: ${reasonText}.`
        : base.title;
    return { ...base, title };
  }
  return {
    label: safeStatus ? safeStatus.replace(/_/g, " ") : "—",
    cls: "pill neutral",
    title: reasonText || "No disposition reported.",
  };
}

/** Map `_filter_status` to triage severity bucket used by the scan filter. */
export function scanStatusSeverityBucket(status) {
  const safeStatus = safeText(status || "kept").toLowerCase() || "kept";
  if (safeStatus === "kept") return "pass";
  const badge = STATUS_BADGES[safeStatus];
  const cls = safeText(badge?.cls || "");
  if (cls.includes("bad")) return "blocked";
  if (cls.includes("warn")) return "review";
  return "info";
}

/** Gate mode label for scan toolbar / integrity banner. */
export function formatGateModeLabel(mode) {
  const m = safeText(mode || "").toLowerCase();
  if (!m) return "unknown";
  if (m === "hard") return "blocking";
  if (m === "soft") return "soft (2+ reasons block)";
  if (m === "shadow") return "shadow (count only)";
  if (m === "off") return "off";
  return m;
}
