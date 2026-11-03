/**
 * Unit tests for webapp/static/panels/liquidPositioning.js (pure HTML builders).
 */
import test from "node:test";
import assert from "node:assert/strict";

import { fundingSparkline, liquidOverviewHtml, liquidTickerHtml } from "../../webapp/static/panels/liquidPositioning.js";

test("overview with no snapshots says collector has not run", () => {
  const html = liquidOverviewHtml({ coverage: { rows: 0 } });
  assert.match(html, /No Liquid snapshots collected yet/);
});

test("overview renders thin badge and never a skew for thin markets", () => {
  const html = liquidOverviewHtml({
    coverage: { rows: 10, tickers: 5, latest_ts: "2026-10-21T15:00:00" },
    thin_count: 1,
    min_oi_usd: 1_000_000,
    mode: "shadow",
    top_open_interest: [
      { ticker: "CVX", source_coin: "xyz:CVX", mark_px: 226, funding_rate: 0.00001, open_interest_usd: 232_000, day_volume_usd: 65_000, is_thin: true },
    ],
    funding_extremes: [],
  });
  assert.match(html, /thin — no signal/);
  assert.match(html, /shadow = display only/);
});

test("ticker not on Liquid is a coverage fact, not a signal", () => {
  const html = liquidTickerHtml({ ticker: "BIIB", status: "not_on_liquid" });
  assert.match(html, /not listed on Liquid/);
});

test("sparkline renders bars with zero line and titles", () => {
  const svg = fundingSparkline([
    { date: "2026-10-20", funding_mean: 0.0001 },
    { date: "2026-10-21", funding_mean: -0.0002 },
  ]);
  assert.match(svg, /<svg/);
  assert.match(svg, /liquid-bar--neg/);
  assert.match(svg, /2026-10-20/);
});
