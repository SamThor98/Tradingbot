/**
 * Unit tests for backtest composer catalog mapping (no DOM).
 */
import test from "node:test";
import assert from "node:assert/strict";

const store = new Map();
globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => {
    store.set(String(key), String(value));
  },
  removeItem: (key) => {
    store.delete(key);
  },
};
if (typeof globalThis.document === "undefined") {
  globalThis.document = {
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
}
if (typeof globalThis.window === "undefined") {
  globalThis.window = globalThis;
}

const { normalizeBtUniverseValue, universeModeForPreset } = await import(
  "../../webapp/static/panels/backtest.js"
);

test("normalizeBtUniverseValue maps legacy watchlist/tickers onto catalog presets", () => {
  assert.equal(normalizeBtUniverseValue("watchlist"), "sp1500");
  assert.equal(normalizeBtUniverseValue("tickers"), "custom");
  assert.equal(normalizeBtUniverseValue("nasdaq100"), "nasdaq100");
  assert.equal(normalizeBtUniverseValue(""), "sp1500");
});

test("universeModeForPreset matches Scan studio custom vs named index", () => {
  assert.equal(universeModeForPreset("sp1500"), "watchlist");
  assert.equal(universeModeForPreset("nasdaq100"), "watchlist");
  assert.equal(universeModeForPreset("custom"), "tickers");
  assert.equal(universeModeForPreset("tickers"), "tickers");
});
