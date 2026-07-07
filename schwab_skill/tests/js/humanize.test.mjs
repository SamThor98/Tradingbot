/**
 * Unit tests for webapp/static/modules/humanize.js (plain-English labels).
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  humanizeKey,
  humanizeApiSource,
  humanizeRolloutMode,
  humanizeScanStageLabel,
  humanizeEnvParam,
} from "../../webapp/static/modules/humanize.js";

test("humanizeKey uses curated labels before generic prettifying", () => {
  assert.equal(humanizeKey("stage_2"), "In uptrend");
  assert.equal(humanizeKey("vcp_detected"), "Volatility pattern");
  assert.equal(humanizeKey("SIGNAL_UNIVERSE_MODE"), "Universe mode");
});

test("humanizeKey prettifies unknown snake_case with acronym fixes", () => {
  assert.equal(humanizeKey("max_drawdown_pct"), "Max Drawdown Pct");
  assert.equal(humanizeKey("api_latency"), "API Latency");
  assert.equal(humanizeKey("sec_filing_dcf"), "SEC Filing DCF");
  assert.equal(humanizeKey(""), "");
  // safeText(null) yields the em-dash placeholder, which passes through.
  assert.equal(humanizeKey(null), "—");
});

test("humanizeApiSource maps known endpoints and prettifies unknown /api paths", () => {
  assert.equal(humanizeApiSource("/api/status"), "connection status");
  assert.equal(humanizeApiSource("/api/pending-trades"), "pending trades");
  assert.equal(humanizeApiSource("/api/shadow/scoreboard"), "Shadow · Scoreboard");
  assert.equal(humanizeApiSource("something-else"), "something-else");
  assert.equal(humanizeApiSource(""), "");
});

test("humanizeRolloutMode covers the plugin rollout ladder", () => {
  assert.equal(humanizeRolloutMode("off"), "Off");
  assert.equal(humanizeRolloutMode("shadow"), "Observe only");
  assert.equal(humanizeRolloutMode("live"), "Enforced");
  assert.equal(humanizeRolloutMode(undefined), "Off");
});

test("humanizeScanStageLabel prefers stage map, then fallback, then prettify", () => {
  assert.equal(humanizeScanStageLabel("stage2"), "Passed uptrend check");
  assert.equal(humanizeScanStageLabel("unknown_stage", "Custom"), "Custom");
  assert.equal(humanizeScanStageLabel("liquidity_gate"), "Liquidity Gate");
});

test("humanizeEnvParam labels known tunables", () => {
  assert.equal(humanizeEnvParam("REGIME_V2_MODE"), "Market regime filter");
  assert.equal(humanizeEnvParam("SOME_NEW_FLAG"), "SOME NEW FLAG".replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()));
});
