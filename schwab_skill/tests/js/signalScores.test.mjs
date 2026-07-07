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

test("getRankScore prefers sort_score then signal_score", () => {
  assert.equal(getRankScore({ sort_score: 9, signal_score: 5 }), 9);
  assert.equal(getRankScore({ signal_score: 5, composite_score: 3 }), 5);
  assert.equal(getRankScore({ composite_score: 3 }), 3);
  assert.equal(getRankScore({}), null);
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
