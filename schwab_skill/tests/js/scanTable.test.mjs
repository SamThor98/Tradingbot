/**
 * Unit tests for the pure sort/compare logic in webapp/static/panels/scanTable.js.
 *
 * The module graph touches document/localStorage at import time (float
 * tooltip global listeners), so minimal shims are installed first.
 */
import test from "node:test";
import assert from "node:assert/strict";

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};
globalThis.document = {
  getElementById: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  createElement: () => ({ style: {}, classList: { add: () => {} }, setAttribute: () => {}, appendChild: () => {} }),
  body: { appendChild: () => {} },
};
globalThis.window = { addEventListener: () => {} };

const { compareScanSignals, sortScanSignalsForRender, getRankExplainMode, scanRowTriageBucket, pickNonEmptyScanStatusFilter, strategyLabelsForRow, renderRankScoreCell, assignDisplayRanks } = await import(
  "../../webapp/static/panels/scanTable.js"
);
const { state } = await import("../../webapp/static/modules/state.js");

test.beforeEach(() => {
  state.scanSort = { field: null, dir: "desc" };
});

test("compareScanSignals sorts analog neighborhood PF as operator sort only", () => {
  const hi = { ticker: "AAA", analog_neighborhood_pf: 1.6 };
  const lo = { ticker: "BBB", analog_neighborhood_pf: 0.8 };
  const missing = { ticker: "CCC" };
  assert.ok(compareScanSignals(hi, lo, "analog_neighborhood_pf", "desc") < 0);
  assert.ok(compareScanSignals(missing, lo, "analog_neighborhood_pf", "desc") > 0);
});

test("compareScanSignals sorts numeric fields and pushes missing to the bottom", () => {
  const hi = { ticker: "AAA", composite_score: 90 };
  const lo = { ticker: "BBB", composite_score: 40 };
  const missing = { ticker: "CCC" };
  assert.ok(compareScanSignals(hi, lo, "score", "desc") < 0, "higher score first when desc");
  assert.ok(compareScanSignals(hi, lo, "score", "asc") > 0, "lower score first when asc");
  // Missing values sort last regardless of direction.
  assert.ok(compareScanSignals(missing, lo, "score", "desc") > 0);
  assert.ok(compareScanSignals(missing, lo, "score", "asc") > 0);
});

test("compareScanSignals sorts text fields with locale compare", () => {
  const a = { ticker: "AAPL" };
  const b = { ticker: "MSFT" };
  assert.ok(compareScanSignals(a, b, "ticker", "asc") < 0);
  assert.ok(compareScanSignals(a, b, "ticker", "desc") > 0);
});

test("compareScanSignals ranks confidence buckets HIGH > MEDIUM > LOW", () => {
  const high = { advisory: { confidence_bucket: "high" } };
  const med = { advisory: { confidence_bucket: "medium" } };
  const low = { advisory: { confidence_bucket: "low" } };
  assert.ok(compareScanSignals(high, med, "confidence", "desc") < 0);
  assert.ok(compareScanSignals(med, low, "confidence", "desc") < 0);
});

test("compareScanSignals ranks kept status above filtered rows", () => {
  const kept = { _filter_status: "kept" };
  const filtered = { _filter_status: "filtered_quality_gates" };
  assert.ok(compareScanSignals(kept, filtered, "status", "desc") < 0);
});

test("sortScanSignalsForRender without explicit sort uses default breakout blend", () => {
  const fresh = { ticker: "FRESH", composite_score: 95 };
  const stale = { ticker: "STALE", composite_score: 20 };
  const sorted = sortScanSignalsForRender([stale, fresh]);
  assert.equal(sorted[0].ticker, "FRESH");
});

test("sortScanSignalsForRender honors state.scanSort and keeps stable order for ties", () => {
  state.scanSort = { field: "score", dir: "asc" };
  const rows = [
    { ticker: "B", composite_score: 50 },
    { ticker: "A", composite_score: 10 },
    { ticker: "C", composite_score: 50 },
  ];
  const sorted = sortScanSignalsForRender(rows);
  assert.deepEqual(
    sorted.map((r) => r.ticker),
    ["A", "B", "C"],
    "ascending by score; equal scores keep original relative order",
  );
  // Input array is not mutated.
  assert.deepEqual(rows.map((r) => r.ticker), ["B", "A", "C"]);
});

test("getRankExplainMode defaults to tooltip and honors state override", () => {
  state.scanRankExplainMode = "";
  assert.equal(getRankExplainMode(), "tooltip");
  state.scanRankExplainMode = "inline";
  assert.equal(getRankExplainMode(), "inline");
  state.scanRankExplainMode = "";
});

test("scanRowTriageBucket maps weekly kept names to Research, not Tradeable", () => {
  assert.equal(scanRowTriageBucket({ _filter_status: "kept" }), "pass");
  assert.equal(
    scanRowTriageBucket({ _filter_status: "kept", timeframe: "weekly" }),
    "review",
  );
  assert.equal(
    scanRowTriageBucket({ _filter_status: "filtered_quality_gates" }),
    "blocked",
  );
  assert.equal(scanRowTriageBucket({ _filter_status: "trimmed_top_n" }), "info");
});

test("pickNonEmptyScanStatusFilter falls back from empty Tradeable to Research", () => {
  assert.equal(
    pickNonEmptyScanStatusFilter({ pass: 0, review: 27, blocked: 0, info: 0 }, "pass"),
    "review",
  );
  assert.equal(
    pickNonEmptyScanStatusFilter({ pass: 4, review: 10, blocked: 0, info: 0 }, "pass"),
    "pass",
  );
  assert.equal(
    pickNonEmptyScanStatusFilter({ pass: 0, review: 27, blocked: 0, info: 0 }, "blocked"),
    "blocked",
  );
});

test("rank cell leads with list place and keeps 52w as the extension line", () => {
  const html = renderRankScoreCell(
    {
      trust_score: 40,
      pts_52w: 24,
      signal_score: 70,
      historical_rank: 80,
    },
    { place: 3 },
  );
  assert.match(html, />3</);
  assert.match(html, /52w 24/);
  assert.match(html, /list place 3 by signal points 70\.0/);
  assert.doesNotMatch(html, />40</);
});

test("assignDisplayRanks separates research rows that share a pinned sort score", () => {
  const rows = [
    { ticker: "LOW", executable: false, sort_score: 100, historical_rank: 10 },
    { ticker: "HIGH", executable: false, sort_score: 100, historical_rank: 80 },
  ];
  const places = assignDisplayRanks(rows);
  assert.equal(places.get(rows[1]), 1);
  assert.equal(places.get(rows[0]), 2);
});

test("assignDisplayRanks counts 1, 2, 3 even when extension matches", () => {
  const rows = [
    { ticker: "ZZZ", composite_score: 40, trust_score: 55, pts_52w: 18 },
    { ticker: "AAA", composite_score: 90, trust_score: 55, pts_52w: 18 },
    { ticker: "MMM", composite_score: 40, trust_score: 55, pts_52w: 18 },
  ];
  const places = assignDisplayRanks(rows);
  assert.equal(places.get(rows[1]), 1);
  assert.equal(places.get(rows[2]), 2);
  assert.equal(places.get(rows[0]), 3);
  assert.equal(new Set(places.values()).size, 3);
});

test("rank cell tooltip names survival confidence as shadow", () => {
  const html = renderRankScoreCell(
    {
      historical_rank: 71.2,
      rank_score_v2: 64,
      setup_confidence: 58,
      setup_confidence_features: ["sma_200_slope"],
      setup_confidence_coverage: 1,
    },
    { percentile: 80 },
  );
  assert.match(html, /survival confidence 58\/100 \(shadow\)/);
  assert.match(html, /historical blend 71\.2 \(shadow\)/);
  assert.match(html, /historical cohort p80/);
  assert.match(html, /from sma_200_slope/);
  assert.doesNotMatch(html, /live sort.*setup_confidence/);
  assert.doesNotMatch(html, /rank 71\.2/);
});

test("assignDisplayRanks follows signal points ahead of the historical blend", () => {
  const rows = [
    { ticker: "CAPPED", signal_score: 20, historical_rank: 90 },
    { ticker: "LIVE", signal_score: 40, historical_rank: 10 },
  ];
  const places = assignDisplayRanks(rows);
  assert.equal(places.get(rows[1]), 1);
  assert.equal(places.get(rows[0]), 2);
});

test("rank tooltip shows a forensic ceiling and splits volume from the session ratio", () => {
  const html = renderRankScoreCell(
    {
      signal_score: 32.5,
      trust_score: 45,
      trust_score_raw: 50,
      trust_score_caps: ["forensic"],
      pts_52w: 20,
      pts_volume_rank: 10,
      volume_ratio: 0.09,
      composite_score: 45,
      composite_score_raw: 81.5,
      historical_rank: 45,
      historical_rank_raw: 62,
      p_up_calibrated: 0.184,
      equity_confidence: 42,
      equity_confidence_summary: "cap inputs 3/3",
    },
    { place: 1 },
  );
  assert.match(html, /list place 1 by signal points 32\.5/);
  assert.match(html, /trust 45\/100, capped from 50 \(forensic\)/);
  assert.match(html, /coverage × agreement × freshness/);
  assert.match(html, /cap inputs 3\/3/);
  assert.match(html, /historical blend 45\.0 \(capped from 62\.0\) \(shadow\)/);
  assert.match(html, /composite 45\.0 \(capped from 81\.5\)/);
  assert.match(html, /vol dry-up 10\.0/);
  assert.match(html, /session volume 0\.09×/);
  assert.match(html, /advisory p\(up\) 18\.4% \(weight 0 on the live score\)/);
  assert.doesNotMatch(html, /by the live score/);
  assert.doesNotMatch(html, /vol pts/);
});

test("rank tooltip does not call an under-ceiling trust score capped", () => {
  const html = renderRankScoreCell(
    {
      signal_score: 45.6,
      trust_score: 17,
      trust_score_raw: 17,
      trust_score_caps: ["forensic"],
      composite_score: 45,
      reliability_reasons: ["composite_capped_risk"],
      equity_confidence_summary: "caps 3/3",
    },
    { place: 1 },
  );
  assert.match(html, /safety flag: forensic \(trust already under the ceiling\)/);
  assert.match(html, /composite 45\.0 \(forensic ceiling\)/);
  assert.match(html, /cap inputs 3\/3/);
  assert.doesNotMatch(html, /trust capped/);
  assert.doesNotMatch(html, /caps risk/);
});

test("strategyLabelsForRow lists every passed sleeve instead of a +N overflow", () => {
  const { labels, timeframes } = strategyLabelsForRow({
    strategy_attribution: {
      passed_ids: ["trend_breakout", "weekly_swing"],
      passed_names: ["Stage 2 / VCP breakout", "Weekly Stage 2"],
      timeframe: "daily",
    },
  });
  assert.deepEqual(labels, ["Stage 2 / VCP breakout", "Weekly Stage 2"]);
  assert.ok(timeframes.includes("daily") || timeframes.includes("weekly") || timeframes.length >= 1);
});
