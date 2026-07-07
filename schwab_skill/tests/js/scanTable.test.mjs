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

const { compareScanSignals, sortScanSignalsForRender, getRankExplainMode } = await import(
  "../../webapp/static/panels/scanTable.js"
);
const { state } = await import("../../webapp/static/modules/state.js");

test.beforeEach(() => {
  state.scanSort = { field: null, dir: "desc" };
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
