import test from "node:test";
import assert from "node:assert/strict";

import {
  CAMPAIGN_PREFIX,
  defaultQueueFilter,
  filterQueueRows,
  formatElapsed,
  isCampaignRun,
  parseApiTime,
  pfBuckets,
  finishedTradeTape,
  inProgressTape,
  pfTone,
  shortTheory,
  splitTape,
  summarizeMonitor,
} from "../../webapp/static/panels/backtestMonitor.js";

test("campaign prefix and short labels", () => {
  assert.equal(CAMPAIGN_PREFIX, "wknd ");
  assert.equal(isCampaignRun({ spec: { theory_name: "wknd w36 nasdaq100 pullback 2y" } }), true);
  assert.equal(isCampaignRun({ spec: { theory_name: "manual check" } }), false);
  assert.equal(shortTheory("wknd w36 nasdaq100 pullback 2y"), "w36 nasdaq100 pullback 2y");
});

test("naive API timestamps are treated as UTC", () => {
  const ms = parseApiTime("2026-09-13T23:06:59.806248");
  assert.equal(Number.isFinite(ms), true);
  assert.equal(new Date(ms).toISOString().startsWith("2026-09-13T23:06:59"), true);
});

test("pf buckets and tones", () => {
  assert.equal(pfTone(1.4), "ok");
  assert.equal(pfTone(1.05), "warn");
  assert.equal(pfTone(0.7), "bad");
  const buckets = pfBuckets([0.5, 0.9, 1.1, 1.3, 2, Infinity]);
  const byId = Object.fromEntries(buckets.map((b) => [b.id, b.count]));
  assert.equal(byId.lt08, 1);
  assert.equal(byId.lt10, 1);
  assert.equal(byId.lt12, 1);
  assert.equal(byId.lt15, 1);
  assert.equal(byId.gte15, 2);
});

test("queue filters and monitor summary", () => {
  const runs = [
    { id: "1", status: "running", spec: { theory_name: "wknd w36 nasdaq100 trend_breakout 2y", universe_preset: "nasdaq100" }, created_at: "2026-09-13T23:00:00" },
    { id: "2", status: "queued", spec: { theory_name: "wknd w36 sector_etfs pullback 2y", universe_preset: "sector_etfs" }, created_at: "2026-09-13T23:01:00" },
    {
      id: "3",
      status: "success",
      has_result: true,
      spec: { theory_name: "wknd w35 nasdaq100 weekly_swing 5y", universe_preset: "nasdaq100" },
      result_summary: { profit_factor_net: 1.4, total_trades: 12 },
      created_at: "2026-09-13T20:00:00",
    },
    { id: "4", status: "success", has_result: true, spec: { theory_name: "manual" }, result_summary: { profit_factor_net: 0.8 } },
  ];
  assert.equal(filterQueueRows(runs, "live").length, 2);
  assert.equal(filterQueueRows(runs, "campaign").length, 3);
  assert.equal(defaultQueueFilter(runs), "live");
  const stats = summarizeMonitor(runs, Date.parse("2026-09-13T23:10:00Z"));
  assert.equal(stats.running.id, "1");
  assert.equal(stats.queued.length, 1);
  assert.equal(stats.campaign.status.success, 1);
  assert.equal(stats.campaign.aboveGate, 1);
  assert.equal(stats.elapsedMs, 10 * 60 * 1000);
  assert.equal(formatElapsed(stats.elapsedMs), "10m 0s");
});

test("tape keeps winners and losers from overlapping on a short sample", () => {
  const rows = [12, 8, 3, 1, -1, -4, -9, -20].map((net, i) => ({
    ticker: `T${i}`,
    net_return_pct: net,
    exit_reason: "time_exit",
  }));
  const tape = splitTape(rows, 5);
  assert.equal(tape.best.length, 5);
  assert.equal(tape.worst.length, 3);
  const bestTickers = new Set(tape.best.map((row) => row.ticker));
  assert.equal(tape.worst.some((row) => bestTickers.has(row.ticker)), false);
  assert.equal(tape.best[0].ticker, "T0");
  assert.equal(tape.worst[0].ticker, "T7");
});

test("finished tape re-ranks across runs and ignores a live partial", () => {
  const runs = [
    {
      id: "run-a",
      status: "success",
      has_result: true,
      spec: { theory_name: "wknd w36 nasdaq100 pullback 2y" },
      result_summary: {
        trade_ranks: {
          closed: 40,
          best: [{ ticker: "AAA", net_return_pct: 18, exit_date: "2024-01-02", exit_reason: "trailing_stop" }],
          worst: [{ ticker: "BBB", net_return_pct: -6, exit_date: "2024-02-02", exit_reason: "time_exit" }],
        },
      },
    },
    {
      id: "run-b",
      status: "success",
      has_result: true,
      spec: { theory_name: "manual check" },
      result_summary: {
        trade_ranks: {
          closed: 10,
          best: [{ ticker: "CCC", net_return_pct: 40, exit_date: "2024-03-02", exit_reason: "trailing_stop" }],
          worst: [{ ticker: "DDD", net_return_pct: -30, exit_date: "2024-03-03", exit_reason: "sma50_break" }],
        },
      },
    },
    {
      id: "live",
      status: "running",
      progress: {
        closed: 2,
        best: [{ ticker: "EEE", net_return_pct: 99 }],
        worst: [],
      },
    },
  ];
  const tape = finishedTradeTape(runs);
  assert.equal(tape.sources, 2);
  assert.equal(tape.best[0].ticker, "CCC");
  assert.equal(tape.best[0].runLabel, "manual check");
  assert.equal(tape.worst[0].ticker, "DDD");
  assert.equal(tape.best.some((row) => row.ticker === "EEE"), false);
  const live = inProgressTape(runs[2]);
  assert.equal(live.closed, 2);
  assert.equal(live.best[0].ticker, "EEE");
  assert.equal(inProgressTape({ status: "running" }), null);
});
