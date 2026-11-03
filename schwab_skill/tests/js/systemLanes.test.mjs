/**
 * System lane URL / panel-id mapping.
 */
import test from "node:test";
import assert from "node:assert/strict";

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {},
};
globalThis.document = { getElementById: () => null, body: { classList: { contains: () => false } } };
globalThis.window = { location: { search: "", hash: "" }, addEventListener: () => {} };

const {
  getSystemLaneFromUrl,
  readExplicitSystemLane,
  systemLaneForPanelId,
  normalizeSystemLane,
  buildSystemNextDecision,
} = await import("../../webapp/static/modules/systemLanes.js");
const { buildMarketDataFix } = await import("../../webapp/static/modules/marketDataFix.js");

test("systemLaneForPanelId maps onboarding hash to connect", () => {
  assert.equal(systemLaneForPanelId("onboardingSection"), "connect");
  assert.equal(systemLaneForPanelId("connect"), "connect");
  assert.equal(systemLaneForPanelId("healthRibbon"), "ready");
  assert.equal(systemLaneForPanelId("calibrationSection"), "calibrate");
  assert.equal(systemLaneForPanelId("unknown"), "");
});

test("getSystemLaneFromUrl prefers lane, then section, then hash", () => {
  assert.equal(getSystemLaneFromUrl("?lane=decide", ""), "decide");
  assert.equal(getSystemLaneFromUrl("?section=connect", ""), "connect");
  assert.equal(getSystemLaneFromUrl("?screen=diagnostics", "#onboardingSection"), "connect");
  assert.equal(getSystemLaneFromUrl("", "#healthRibbon"), "ready");
  assert.equal(normalizeSystemLane("settings"), "connect");
});

test("readExplicitSystemLane is empty when the URL does not name a lane", () => {
  assert.equal(readExplicitSystemLane("", ""), "");
  assert.equal(readExplicitSystemLane("?screen=diagnostics&wave=3", ""), "");
  assert.equal(readExplicitSystemLane("?screen=diagnostics", "#onboardingSection"), "connect");
});

test("buildSystemNextDecision uses plain English, not allocator jargon", () => {
  const green = buildSystemNextDecision({ authState: "connected", systemState: "ok" });
  assert.equal(green.title.includes("allocator"), false);
  assert.match(green.why, /shadow/i);
  const connect = buildSystemNextDecision({
    blockers: ["Schwab auth disconnected — reconnect account and market sessions."],
    authState: "disconnected",
  });
  assert.equal(connect.ctaLane, "connect");
  assert.equal(connect.title.includes("Auth is green"), false);
});

test("SEC cache degradation replaces the provenance card with the Schwab fix", () => {
  const market = buildMarketDataFix({
    authState: "connected",
    quoteOk: true,
    diag: {
      data_quality: "degraded",
      data_quality_reasons: ["sec_cache_stale_age_h_72.6_gt_72.0"],
      data_provider_primary_count: 1503,
      data_provider_fallback_count: 0,
      regime_history_provider: "schwab",
    },
  });
  const card = buildSystemNextDecision({
    blockers: [market.blocker],
    authState: "connected",
    quoteOk: true,
    marketData: market,
  });
  assert.equal(card.title, "Schwab market data is live.");
  assert.equal(card.ctaHref, "#scanSection");
  assert.equal(card.ctaLane, "");
  assert.match(card.why, /36 minutes/);
});
