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
  return null;
}

/**
 * Operator-facing rank. Prefers the historically fitted blend, then rank v2 —
 * the score the live percentile cut is actually taken on — before falling back
 * to the legacy sort/composite fields for older payloads.
 */
export function getRankScore(row = {}) {
  return (
    optionalNum(
      row.historical_rank
      ?? row.rank_score_v2
      ?? row.sort_score
      ?? row.signal_score
      ?? row.composite_score
      ?? row.rank_score,
    )
    ?? getCompositeScore(row)
  );
}

/** Shadow/diagnostic rank v2 (component IC blend). */
export function getDiagnosticRankV2(row = {}) {
  return optionalNum(row.rank_score_v2);
}

/** Shadow historical blend score (HISTORICAL_RANK_MODE), when scored. */
export function getHistoricalRank(row = {}) {
  return optionalNum(row.historical_rank);
}

/** Pre-cap historical blend, when the scanner stamped it. */
export function getHistoricalRankRaw(row = {}) {
  return optionalNum(row.historical_rank_raw);
}

/**
 * Research rows pin sort_score, so their list place stays on the rank blend.
 * Tradeable rows number by signal points.
 */
export function isResearchRankRow(row = {}) {
  const family = safeText(row.entry_family || "").toLowerCase();
  return family === "horizon" || row.executable === false;
}

/**
 * What the Rank column's 1, 2, 3 follows.
 * `signal` is the live book key. `historical` is the shadow blend.
 */
export function listPlaceBasis(row = {}) {
  if (isResearchRankRow(row)) {
    const historical = getHistoricalRank(row);
    if (historical !== null) return { kind: "historical", text: "the historical blend", value: historical };
    const rank = getRankScore(row);
    if (rank !== null) return { kind: "rank", text: "the rank score", value: rank };
    return { kind: "none", text: "", value: null };
  }
  const signal = optionalNum(row.signal_score);
  if (signal !== null) {
    return { kind: "signal", text: `signal points ${signal.toFixed(1)}`, value: signal };
  }
  const rank = getRankScore(row);
  if (rank !== null) return { kind: "rank", text: "the rank score", value: rank };
  return { kind: "none", text: "", value: null };
}

/**
 * Shadow survival readout (CONFIDENCE_RANK_MODE): 0-100, higher means the setup
 * looks more like trades that ran 21-40 days than ones stopped out inside 20.
 * Display only — it never drives the live sort. Missing ≠ 0.
 */
export function getSetupConfidence(row = {}) {
  return optionalNum(row.setup_confidence);
}

export function getSetupConfidenceFeatures(row = {}) {
  return Array.isArray(row.setup_confidence_features)
    ? row.setup_confidence_features.map((f) => safeText(f)).filter(Boolean)
    : [];
}

export function getSetupConfidenceCoverage(row = {}) {
  return optionalNum(row.setup_confidence_coverage);
}

/**
 * 52-week extension points. Lower is less extended. Shadow display only —
 * the live sort remains signal_score. Missing ≠ 0.
 */
export function getExtensionPts(row = {}) {
  const comps = row.score_components && typeof row.score_components === "object" ? row.score_components : {};
  return optionalNum(row.pts_52w ?? comps.pts_52w);
}

/** Shadow sort score: higher means less extended (100 - pts_52w). */
export function getExtensionSortScore(row = {}) {
  return optionalNum(row.extension_sort_score);
}

/** Component span for pts_52w. Matches core/trust_score.py PTS_52W_SCALE. */
export const TRUST_PTS_SCALE = 40;

/**
 * Operator trust score, 0–100. Higher means less extended and a better
 * expected realized outcome on the measured book. Display only — it does
 * not change the live sort. Missing ≠ 0.
 */
export function getTrustScore(row = {}) {
  return optionalNum(row.trust_score);
}

/** Uncapped extension map. Present when the scanner stamped trust_score. */
export function getTrustScoreRaw(row = {}) {
  return optionalNum(row.trust_score_raw);
}

/** Map raw extension points onto 0–100. Higher is less extended. */
export function trustScoreFromPts(pts) {
  const n = optionalNum(pts);
  if (n === null) return null;
  return Math.max(0, Math.min(100, (1 - n / TRUST_PTS_SCALE) * 100));
}

/**
 * Number to show in the Rank cell. The scanner stamp wins. Older rows that
 * only carry pts_52w use the same map so the cell does not keep a
 * lower-is-better reading.
 */
export function getDisplayedTrustScore(row = {}) {
  const stamped = getTrustScore(row);
  if (stamped !== null) return stamped;
  return trustScoreFromPts(getExtensionPts(row));
}

/**
 * Trust number for the Rank cell. A literal 0/0 stack is unscored, not
 * "least extended".
 */
export function getRankCellTrust(row = {}) {
  const stamped = getTrustScore(row) !== null;
  const pts = getExtensionPts(row);
  const live = optionalNum(row.signal_score);
  const show = stamped || (pts !== null && (pts > 0 || (live !== null && live > 0)));
  if (!show) return null;
  return getDisplayedTrustScore(row);
}

export function getTrustScoreEvidence(row = {}) {
  const raw = safeText(row.trust_score_evidence || "").trim();
  return raw && raw !== "—" ? raw : null;
}

export function getTrustScoreCaps(row = {}) {
  return Array.isArray(row.trust_score_caps)
    ? row.trust_score_caps.map((cap) => safeText(cap)).filter(Boolean)
    : [];
}

/**
 * File confidence, 0–100. How much of the factor ledger was measured, and
 * whether the stretch reading and safety caps agree with the Rank.
 * Display only — it does not change the Rank or the live sort. Missing ≠ 0.
 */
export function getEquityConfidence(row = {}) {
  return optionalNum(row.equity_confidence);
}

export function getEquityConfidenceSummary(row = {}) {
  const raw = safeText(row.equity_confidence_summary || "").trim();
  return raw && raw !== "—" ? raw : null;
}

/** Neighborhood profit factor from analog overlay (shadow). Missing ≠ 0. */
export function getAnalogNeighborhoodPf(row = {}) {
  return optionalNum(row.analog_neighborhood_pf);
}

export function getAnalogPWin(row = {}) {
  return optionalNum(row.analog_p_win);
}

export function getAnalogN(row = {}) {
  return optionalNum(row.analog_n);
}

export function getAnalogThin(row = {}) {
  return row.analog_thin === true;
}

export function getAnalogRegime(row = {}) {
  const raw = safeText(row.analog_regime || "").trim();
  return raw && raw !== "—" ? raw : null;
}

export function getAnalogReasons(row = {}) {
  return Array.isArray(row.analog_reasons) ? row.analog_reasons.map((r) => safeText(r)).filter(Boolean) : [];
}

export function getAnalogNeighbors(row = {}) {
  return Array.isArray(row.analog_neighbors) ? row.analog_neighbors : [];
}

export function getAnalogCalibrationNote(row = {}) {
  const raw = safeText(row.analog_calibration_note || "").trim();
  return raw && raw !== "—" ? raw : null;
}

/**
 * Backend cohort percentile (0-100) when the scanner supplied one. Accepts a
 * 0-1 fraction for forward compatibility with ratio-shaped payloads.
 */
export function getRankPercentile(row = {}) {
  const direct = optionalNum(row.rank_percentile ?? row?.prob_rank?.rank_percentile);
  if (direct === null) return null;
  const scaled = direct > 0 && direct <= 1 ? direct * 100 : direct;
  return Math.max(0, Math.min(100, scaled));
}

/**
 * Live percentile cut context from the scanner's `rank_filter_v2` block, so the
 * table can mark rows the live filter drops (or would drop in shadow).
 */
export function getRankLiveCut(row = {}) {
  const block = row.rank_filter_v2;
  if (!block || typeof block !== "object") {
    return { mode: null, threshold: null, wouldDrop: null };
  }
  return {
    mode: typeof block.mode === "string" ? block.mode : null,
    threshold: optionalNum(block.threshold),
    wouldDrop: block.would_drop === true ? true : block.would_drop === false ? false : null,
  };
}

/** Percentile rank (0-100) of `value` within an ascending-sorted cohort. */
export function percentileRank(sortedValues, value) {
  const v = optionalNum(value);
  if (v === null || !Array.isArray(sortedValues) || !sortedValues.length) return null;
  let below = 0;
  let equal = 0;
  for (const item of sortedValues) {
    if (item < v) below += 1;
    else if (item === v) equal += 1;
  }
  // Mid-rank so ties land on one shared percentile instead of straddling the cut.
  return ((below + equal / 2) / sortedValues.length) * 100;
}

/**
 * Build a row -> percentile resolver for one rendered cohort. Backend
 * `rank_percentile` wins; otherwise the percentile is derived from the cohort
 * so the number stays on the same scale as the live cut.
 */
export function buildRankPercentileResolver(cohortScores = []) {
  const sorted = (Array.isArray(cohortScores) ? cohortScores : [])
    .map((v) => optionalNum(v))
    .filter((v) => v !== null)
    .sort((a, b) => a - b);
  return (row = {}) => {
    const direct = getRankPercentile(row);
    if (direct !== null) return direct;
    if (sorted.length < 3) return null;
    return percentileRank(sorted, getRankScore(row));
  };
}

/** Legacy composite-based rank (v1); for comparison only. */
export function getLegacyRankScore(row = {}) {
  return optionalNum(row.rank_score_v1 ?? row.rank_score);
}

/** True when reliability was inferred from advisory bucket (legacy payloads). */
export function isReliabilityEstimated(row = {}) {
  return optionalNum(row.reliability_score) === null;
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
