/**
 * Unit tests for webapp/static/panels/backtestLabels.js
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  filterLeaderboardRows,
  formatBacktestLabel,
  statusLabel,
  exitReasonLabel,
} from "../../webapp/static/panels/backtestLabels.js";

const CATALOG = {
  universes: [
    { id: "sp1500", display_name: "S&P 1500" },
    { id: "sp500", display_name: "S&P 500" },
    { id: "nasdaq100", display_name: "Nasdaq-100" },
  ],
  strategies: [
    { id: "trend_breakout", display_name: "Stage 2 / VCP breakout" },
    { id: "pead_primary", display_name: "PEAD earnings drift" },
    { id: "pullback", display_name: "Trend pullback" },
  ],
};

test("campaign theory becomes a readable sentence", () => {
  const out = formatBacktestLabel(
    {
      theory_name: "wknd w33 sp500 trend_breakout+pead_primary 2y stage2_52w_pct=0.85",
      universe_preset: "sp500",
      strategy_ids: ["trend_breakout", "pead_primary"],
    },
    CATALOG,
  );
  assert.equal(out.title.includes("_"), false);
  assert.equal(out.subtitle.includes("_"), false);
  assert.match(out.title, /Wave 33/);
  assert.match(out.title, /S&P 500/);
  assert.match(out.title, /Stage 2 \/ VCP breakout/);
  assert.match(out.title, /PEAD earnings drift/);
  assert.match(out.title, /2 years/);
  assert.match(out.subtitle, /Near 52-week high 0.85/);
});

test("status and exit labels drop snake_case", () => {
  assert.equal(statusLabel("queued"), "Waiting");
  assert.equal(statusLabel("success"), "Done");
  assert.equal(exitReasonLabel("trailing_stop"), "Trailing stop");
});

test("leaderboard window filter", () => {
  const rows = [
    { id: "a", window_years: 2, universe_id: "nasdaq100", total_trades: 200 },
    { id: "b", window_years: 5, universe_id: "sp1500", total_trades: 80 },
    { id: "c", window_years: 2, universe_id: "sector_etfs", total_trades: 40 },
  ];
  assert.equal(filterLeaderboardRows(rows, 2).length, 2);
  assert.equal(filterLeaderboardRows(rows, null).length, 3);
  assert.equal(filterLeaderboardRows(rows, null, { universeId: "nasdaq100" }).length, 1);
  assert.equal(filterLeaderboardRows(rows, 2, { minTrades: 100 }).length, 1);
});

test("date preset theory is not the title", () => {
  const out = formatBacktestLabel(
    {
      theory_name: "last 8 years",
      universe_preset: "sp1500",
      strategy_ids: ["trend_breakout"],
      start_date: "2018-09-14",
      end_date: "2026-09-14",
    },
    CATALOG,
  );
  assert.notEqual(out.title, "last 8 years");
  assert.match(out.title, /S&P 1500/);
  assert.match(out.title, /Stage 2 \/ VCP breakout/);
});
