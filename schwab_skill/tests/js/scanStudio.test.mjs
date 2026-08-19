/**
 * Unit tests for scan studio payload builder (pure prefs → POST /api/scan body).
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

const { state, SCAN_STUDIO_PREFS_KEY, LEGACY_SCAN_STUDIO_PREFS_KEY } = await import(
  "../../webapp/static/modules/state.js"
);
const {
  readScanStudioBody,
  scanStudioProgressLabel,
  primaryStrategyIdForTimeframe,
  loadScanStudioPrefs,
  scanStudioMarkup,
} = await import("../../webapp/static/panels/scanStudio.js");

test.beforeEach(() => {
  store.clear();
  state.scanCatalog = {
    timeframes: [{ id: "daily", display_name: "Daily", description: "Daily engine." }],
    strategies: [{ id: "trend_breakout", display_name: "Stage 2 / VCP breakout", timeframe: "daily", runnable: true }],
    universes: [
      { id: "sp1500", display_name: "S&P 1500", available: true },
      { id: "nasdaq100", display_name: "Nasdaq-100", available: true },
      { id: "custom", display_name: "Custom tickers", available: true },
    ],
    defaults: { timeframe: "daily", universe_preset: "sp1500", strategy_ids: ["trend_breakout"] },
  };
  state.scanStudioPrefs = {
    timeframe: "daily",
    universe_preset: "nasdaq100",
    strategy_ids: ["trend_breakout"],
    tickersText: "",
  };
});

test("readScanStudioBody posts universe preset and strategy ids", () => {
  const out = readScanStudioBody();
  assert.equal(out.error, undefined);
  assert.equal(out.body.universe_preset, "nasdaq100");
  assert.equal(out.body.scan_timeframe, "daily");
  assert.deepEqual(out.body.strategy_ids, ["trend_breakout"]);
  assert.equal(out.body.universe_mode, undefined);
});

test("readScanStudioBody requires tickers for custom universe", () => {
  state.scanStudioPrefs.universe_preset = "custom";
  state.scanStudioPrefs.tickersText = "";
  const empty = readScanStudioBody();
  assert.match(empty.error, /ticker/i);
  state.scanStudioPrefs.tickersText = "aapl, msft";
  const ok = readScanStudioBody();
  assert.equal(ok.error, undefined);
  assert.equal(ok.body.universe_mode, "tickers");
  assert.deepEqual(ok.body.tickers, ["AAPL", "MSFT"]);
});

test("readScanStudioBody requires a selected strategy", () => {
  state.scanStudioPrefs.strategy_ids = [];
  const out = readScanStudioBody();
  assert.match(out.error, /strategy/i);
});

test("scanStudioProgressLabel names the universe", () => {
  assert.match(scanStudioProgressLabel(), /Nasdaq-100/);
});

test("primaryStrategyIdForTimeframe uses catalog default", () => {
  state.scanCatalog.timeframes = [
    { id: "weekly", display_name: "Weekly", default_strategy_id: "weekly_swing" },
    { id: "daily", display_name: "Daily", default_strategy_id: "trend_breakout" },
  ];
  state.scanCatalog.strategies = [
    { id: "weekly_swing", timeframe: "weekly", runnable: true },
    { id: "weekly_reversal", timeframe: "weekly", runnable: true },
    { id: "trend_breakout", timeframe: "daily", runnable: true },
  ];
  assert.equal(primaryStrategyIdForTimeframe("weekly"), "weekly_swing");
  assert.equal(primaryStrategyIdForTimeframe("daily"), "trend_breakout");
});

test("scan studio prefs key is versioned past the select-all era", () => {
  assert.equal(SCAN_STUDIO_PREFS_KEY, "tradingbot.scan.studio.v2");
  assert.equal(LEGACY_SCAN_STUDIO_PREFS_KEY, "tradingbot.scan.studio");
});

test("loadScanStudioPrefs migrates v1 select-all sessions to the tab primary", () => {
  state.scanCatalog.timeframes = [
    { id: "daily", display_name: "Daily", default_strategy_id: "trend_breakout" },
  ];
  state.scanCatalog.strategies = [
    { id: "trend_breakout", timeframe: "daily", runnable: true },
    { id: "pullback", timeframe: "daily", runnable: true },
    { id: "donchian_20", timeframe: "daily", runnable: true },
    { id: "nr7_breakout", timeframe: "daily", runnable: true },
    { id: "pead_primary", timeframe: "daily", runnable: true },
  ];
  store.set(
    LEGACY_SCAN_STUDIO_PREFS_KEY,
    JSON.stringify({
      timeframe: "daily",
      universe_preset: "nasdaq100",
      strategy_ids: ["trend_breakout", "pullback", "donchian_20", "nr7_breakout", "pead_primary"],
      tickersText: "AAPL",
    }),
  );
  const prefs = loadScanStudioPrefs();
  assert.equal(prefs.timeframe, "daily");
  assert.equal(prefs.universe_preset, "nasdaq100");
  assert.equal(prefs.tickersText, "AAPL");
  assert.deepEqual(prefs.strategy_ids, ["trend_breakout"]);
  assert.ok(store.has(SCAN_STUDIO_PREFS_KEY));
  const saved = JSON.parse(store.get(SCAN_STUDIO_PREFS_KEY));
  assert.deepEqual(saved.strategy_ids, ["trend_breakout"]);
});

test("loadScanStudioPrefs prefers v2 over a leftover v1 select-all blob", () => {
  store.set(
    LEGACY_SCAN_STUDIO_PREFS_KEY,
    JSON.stringify({
      timeframe: "daily",
      universe_preset: "sp1500",
      strategy_ids: ["trend_breakout", "pullback"],
      tickersText: "",
    }),
  );
  store.set(
    SCAN_STUDIO_PREFS_KEY,
    JSON.stringify({
      timeframe: "weekly",
      universe_preset: "focused",
      strategy_ids: ["weekly_swing"],
      tickersText: "",
    }),
  );
  const prefs = loadScanStudioPrefs();
  assert.equal(prefs.timeframe, "weekly");
  assert.deepEqual(prefs.strategy_ids, ["weekly_swing"]);
  assert.equal(prefs.universe_preset, "focused");
});

test("scanStudioMarkup groups Yours vs Paper and marks the primary card", () => {
  state.scanCatalog.timeframes = [
    { id: "daily", display_name: "Daily", default_strategy_id: "trend_breakout" },
  ];
  state.scanCatalog.strategies = [
    { id: "pullback", display_name: "Trend pullback", timeframe: "daily", origin: "iterated", status: "shadow", runnable: true },
    { id: "trend_breakout", display_name: "Stage 2 / VCP breakout", timeframe: "daily", origin: "iterated", status: "live", runnable: true },
    { id: "st_reversal_5d", display_name: "5-day loser bounce (screen)", timeframe: "daily", origin: "literature", status: "research", runnable: true },
  ];
  const html = scanStudioMarkup(state.scanStudioPrefs);
  assert.match(html, /Scan lens/);
  assert.match(html, /scan-studio-group--paper/);
  assert.match(html, /scan-studio-strategy--primary/);
  assert.match(html, /data-scan-strategy="trend_breakout"[^>]*checked/);
  assert.match(html, /Literature screens/);
  const primaryAt = html.indexOf("scan-studio-strategy--primary");
  const paperAt = html.indexOf("scan-studio-group--paper");
  assert.ok(primaryAt >= 0 && paperAt > primaryAt);
});
