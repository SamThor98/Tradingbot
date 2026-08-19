/**
 * Unit tests for scan studio payload builder (pure prefs → POST /api/scan body).
 */
import test from "node:test";
import assert from "node:assert/strict";

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};

const { state } = await import("../../webapp/static/modules/state.js");
const { readScanStudioBody, scanStudioProgressLabel, primaryStrategyIdForTimeframe } = await import(
  "../../webapp/static/panels/scanStudio.js"
);

test.beforeEach(() => {
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
