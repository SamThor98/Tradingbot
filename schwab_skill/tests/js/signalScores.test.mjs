/**
 * Unit tests for webapp/static/modules/signalScores.js (pure score accessors
 * shared by the scan table and pending board).
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  optionalNum,
  normalizeProbability,
  formatConfidenceLabel,
  getCompositeScore,
  getRankScore,
  getCalibratedPUp,
  getReliabilityScore,
  isReliabilityEstimated,
  getEdgeScore,
  getExecutionScore,
  getHistoricalRank,
  getSetupConfidence,
  getSetupConfidenceFeatures,
  getSetupConfidenceCoverage,
  getAnalogNeighborhoodPf,
  getAnalogN,
  getAnalogThin,
  getAnalogRegime,
  getRankPercentile,
  getRankLiveCut,
  percentileRank,
  buildRankPercentileResolver,
} from "../../webapp/static/modules/signalScores.js";

test("optionalNum returns null for missing/placeholder values", () => {
  assert.equal(optionalNum(null), null);
  assert.equal(optionalNum(undefined), null);
  assert.equal(optionalNum(""), null);
  assert.equal(optionalNum("—"), null);
  assert.equal(optionalNum("  "), null);
  assert.equal(optionalNum("12.5"), 12.5);
  assert.equal(optionalNum(0), 0);
  assert.equal(optionalNum("abc"), null);
});

test("normalizeProbability accepts ratios and legacy percent points", () => {
  assert.equal(normalizeProbability(0.62), 0.62);
  assert.equal(normalizeProbability(62.4), 0.624);
  assert.equal(normalizeProbability(1), 1);
  assert.equal(normalizeProbability(100), 1);
  assert.equal(normalizeProbability(-0.5), 0);
  assert.equal(normalizeProbability(null), null);
});

test("formatConfidenceLabel normalizes casing and placeholders", () => {
  assert.equal(formatConfidenceLabel("high"), "HIGH");
  assert.equal(formatConfidenceLabel("very_high"), "VERY HIGH");
  assert.equal(formatConfidenceLabel("unknown"), "—");
  assert.equal(formatConfidenceLabel(""), "—");
  assert.equal(formatConfidenceLabel("—"), "—");
});

test("getCompositeScore falls back composite → signal → score", () => {
  assert.equal(getCompositeScore({ composite_score: 81 }), 81);
  assert.equal(getCompositeScore({ signal_score: 72 }), 72);
  assert.equal(getCompositeScore({ score: 65 }), 65);
  assert.equal(getCompositeScore({}), null);
});

test("getRankScore prefers historical blend, then rank v2, then legacy fields", () => {
  assert.equal(
    getRankScore({ historical_rank: 77, rank_score_v2: 64, sort_score: 9, signal_score: 5 }),
    77,
    "historical blend wins when scored",
  );
  assert.equal(
    getRankScore({ rank_score_v2: 64, sort_score: 9, signal_score: 5 }),
    64,
    "rank v2 is the score the live cut uses",
  );
  assert.equal(getRankScore({ sort_score: 9, signal_score: 5 }), 9);
  assert.equal(getRankScore({ signal_score: 5, composite_score: 3 }), 5);
  assert.equal(getRankScore({ composite_score: 3 }), 3);
  assert.equal(getRankScore({}), null);
});

test("getHistoricalRank only reports the shadow blend score", () => {
  assert.equal(getHistoricalRank({ historical_rank: 71.5 }), 71.5);
  assert.equal(getHistoricalRank({ rank_score_v2: 64 }), null);
});

test("setup confidence is display-only and missing is not zero", () => {
  assert.equal(getSetupConfidence({ setup_confidence: 61.2 }), 61.2);
  assert.equal(getSetupConfidence({}), null);
  assert.deepEqual(
    getSetupConfidenceFeatures({ setup_confidence_features: ["sma_200_slope", "trend_strength_score"] }),
    ["sma_200_slope", "trend_strength_score"],
  );
  assert.deepEqual(getSetupConfidenceFeatures({}), []);
  assert.equal(getSetupConfidenceCoverage({ setup_confidence_coverage: 0.67 }), 0.67);
  assert.equal(getSetupConfidenceCoverage({}), null);
});

test("analog accessors treat missing as null, not zero", () => {
  assert.equal(getAnalogNeighborhoodPf({ analog_neighborhood_pf: 1.41 }), 1.41);
  assert.equal(getAnalogNeighborhoodPf({}), null);
  assert.equal(getAnalogN({ analog_n: 47 }), 47);
  assert.equal(getAnalogN({}), null);
  assert.equal(getAnalogThin({ analog_thin: true }), true);
  assert.equal(getAnalogThin({}), false);
  assert.equal(getAnalogRegime({ analog_regime: "bear_rates|spy_below_200" }), "bear_rates|spy_below_200");
});

test("getRankPercentile accepts 0-100 points and 0-1 ratios", () => {
  assert.equal(getRankPercentile({ rank_percentile: 82 }), 82);
  assert.equal(getRankPercentile({ rank_percentile: 0.82 }), 82);
  assert.equal(getRankPercentile({ rank_percentile: 140 }), 100);
  assert.equal(getRankPercentile({}), null);
});

test("getRankLiveCut surfaces the scanner rank_filter_v2 block", () => {
  const cut = getRankLiveCut({
    rank_filter_v2: { mode: "live", score: 50, threshold: 61.2, would_drop: true },
  });
  assert.equal(cut.mode, "live");
  assert.equal(cut.threshold, 61.2);
  assert.equal(cut.wouldDrop, true);
  const missing = getRankLiveCut({});
  assert.deepEqual(missing, { mode: null, threshold: null, wouldDrop: null });
});

test("percentileRank uses mid-rank so ties share one percentile", () => {
  assert.equal(percentileRank([10, 20, 30, 40], 10), 12.5);
  assert.equal(percentileRank([10, 20, 30, 40], 40), 87.5);
  assert.equal(percentileRank([10, 10, 30, 30], 10), 25);
  assert.equal(percentileRank([], 10), null);
  assert.equal(percentileRank([10, 20], null), null);
});

test("buildRankPercentileResolver prefers backend percentile then cohort rank", () => {
  const resolver = buildRankPercentileResolver([10, 20, 30, 40]);
  assert.equal(resolver({ rank_percentile: 91 }), 91, "backend value wins");
  assert.equal(resolver({ rank_score_v2: 40 }), 87.5);
  assert.equal(resolver({}), null, "no score means no percentile");
  // Cohorts too small to rank meaningfully report nothing rather than p50.
  assert.equal(buildRankPercentileResolver([10, 20])({ rank_score_v2: 20 }), null);
});

test("getCalibratedPUp checks calibrated field then advisory fallbacks", () => {
  assert.equal(getCalibratedPUp({ p_up_calibrated: 0.61 }), 0.61);
  assert.equal(getCalibratedPUp({ advisory: { p_up_10d: 0.55 } }), 0.55);
  assert.equal(getCalibratedPUp({ advisory: { p_up_10d_raw: 58 } }), 0.58);
  assert.equal(getCalibratedPUp({}), null);
});

test("reliability accessors flag estimated reliability", () => {
  assert.equal(getReliabilityScore({ reliability_score: 44 }), 44);
  assert.equal(getReliabilityScore({}), null);
  assert.equal(isReliabilityEstimated({ reliability_score: 44 }), false);
  assert.equal(isReliabilityEstimated({}), true);
});

test("edge/execution scores fall back sensibly", () => {
  assert.equal(getEdgeScore({ edge_score: 12 }), 12);
  assert.equal(getEdgeScore({ composite_score: 70 }), 70);
  assert.equal(getExecutionScore({ execution_score: 33 }), 33);
  assert.equal(getExecutionScore({}), 60);
});
