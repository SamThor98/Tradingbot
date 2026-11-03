/**
 * Schwab market-data card: live quotes vs last-scan data_quality.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  buildMarketDataFix,
  dataQualityChip,
  marketDataCardOrNull,
  parseSecCacheReason,
  scanQualityBlocker,
} from "../../webapp/static/modules/marketDataFix.js";

const LAST_SCAN = {
  data_quality: "degraded",
  data_quality_reasons: ["sec_cache_stale_age_h_72.6_gt_72.0"],
  data_provider_primary_count: 1503,
  data_provider_fallback_count: 0,
  regime_history_provider: "schwab",
};

test("parseSecCacheReason reads the hour stamp", () => {
  const parsed = parseSecCacheReason("sec_cache_stale_age_h_72.6_gt_72.0");
  assert.equal(parsed.kind, "stale");
  assert.equal(parsed.age, 72.6);
  assert.equal(parsed.limit, 72);
});

test("stale SEC cache says Schwab is live and names the refresh command", () => {
  const view = buildMarketDataFix({
    authState: "connected",
    quoteOk: true,
    diag: LAST_SCAN,
  });
  assert.equal(view.state, "fix");
  assert.equal(view.card.title, "Schwab market data is live.");
  assert.match(view.card.why, /1,503 names with Schwab/);
  assert.match(view.card.why, /0 with the yfinance fallback/);
  assert.match(view.card.why, /36 minutes past the 72-hour limit/);
  assert.match(view.card.why, /Reconnecting Schwab will not clear/);
  assert.equal(view.card.steps[0].command, "python scripts/refresh_sec_cache.py");
  assert.equal(view.card.ctaHref, "#scanSection");
  assert.equal(view.card.ctaLane, "");
  assert.equal(dataQualityChip(LAST_SCAN).label, "SEC cache stale");
  assert.match(scanQualityBlocker(LAST_SCAN), /SEC filing cache/);
  assert.doesNotMatch(scanQualityBlocker(LAST_SCAN), /provenance/);
});

test("quote failure points at Connect Market Data, including a 401 entitlement", () => {
  const view = buildMarketDataFix({
    authState: "connected",
    quoteOk: false,
    quoteHint: "Schwab returned 401 Unauthorized for market-data quotes.",
    diag: LAST_SCAN,
  });
  assert.equal(view.state, "down");
  assert.equal(view.card.title, "Schwab market data is not live.");
  assert.equal(view.card.ctaLane, "connect");
  assert.match(view.card.steps[1], /Ready For Use/);
  assert.equal(marketDataCardOrNull(["Quote feed degraded — data quality may fail closed."], view).ctaLabel, "Connect market data");
});

test("missing session tells the operator to connect market data", () => {
  const view = buildMarketDataFix({ authState: "disconnected", quoteOk: false, diag: {} });
  assert.equal(view.state, "down");
  assert.match(view.card.title, /not connected/);
  assert.match(view.card.steps[2], /Connect Market Data/);
});

test("kill switch stays ahead of the market-data card", () => {
  const view = buildMarketDataFix({ authState: "connected", quoteOk: true, diag: LAST_SCAN });
  const card = marketDataCardOrNull(
    ["Platform kill switch is active — new risk-increasing orders are blocked."],
    view,
  );
  assert.equal(card, null);
});

test("healthy quote and clean scan produce no fix card", () => {
  const view = buildMarketDataFix({
    authState: "connected",
    quoteOk: true,
    diag: { data_quality: "ok", data_provider_primary_count: 10, data_provider_fallback_count: 0 },
  });
  assert.equal(view.state, "ok");
  assert.equal(view.card, null);
  assert.equal(scanQualityBlocker({ data_quality: "ok" }), "");
});
