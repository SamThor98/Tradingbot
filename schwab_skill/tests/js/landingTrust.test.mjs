/**
 * Landing-frame chips must stay live after boot — never "Pending …" / "Auth check".
 */
import test from "node:test";
import assert from "node:assert/strict";

import { resolveLandingFrameState, selectScanBlockerBanner } from "../../webapp/static/modules/landingTrust.js";

function labels(mode, ctx) {
  return resolveLandingFrameState(mode, ctx).chips.map((c) => c.label);
}

test("operations chips: loading queue and checking connection before first fetch", () => {
  const chips = labels("operations", { pending: null, health: { authState: "unknown" } });
  assert.deepEqual(chips, ["Loading queue", "No scan yet", "Checking connection"]);
  assert.equal(chips.includes("Pending …"), false);
  assert.equal(chips.includes("Auth check"), false);
});

test("operations chips: pending error is blocked, not a placeholder", () => {
  const resolved = resolveLandingFrameState("operations", {
    pending: null,
    pendingError: "Pending trades unavailable: network error",
    health: { authState: "connected" },
  });
  assert.equal(resolved.chips[0].label, "Queue unavailable");
  assert.equal(resolved.chips[0].tone, "blocked");
  assert.equal(resolved.chips[2].label, "Schwab connected");
});

test("operations chips: health probe failure is blocked", () => {
  const chips = labels("operations", {
    pending: 0,
    health: { authState: "unknown", probeFailed: true, probeError: "Status check unavailable." },
  });
  assert.equal(chips[0], "Queue clear");
  assert.equal(chips[2], "Health check failed");
});

test("operations chips: live pending count after load", () => {
  const chips = labels("operations", {
    pending: 3,
    lastScanAt: new Date().toISOString(),
    health: { authState: "connected" },
  });
  assert.equal(chips[0], "3 pending");
  assert.match(chips[1], /Last scan|Scan ·/);
  assert.equal(chips[2], "Schwab connected");
});

test("operations landing CTA: empty when queue is clear (topbar owns Run Scan)", () => {
  const resolved = resolveLandingFrameState("operations", {
    pending: 0,
    health: { authState: "connected" },
  });
  assert.equal(resolved.ctaLabel, "");
  assert.equal(resolved.altCtaLabel, "Open scan table");
});

test("operations landing CTA: Review pending when queue has work", () => {
  const resolved = resolveLandingFrameState("operations", {
    pending: 2,
    health: { authState: "connected" },
  });
  assert.equal(resolved.ctaLabel, "Review pending");
  assert.equal(resolved.ctaHref, "#pendingSection");
});

test("intel landing CTA: Look up ticker is the primary action", () => {
  const resolved = resolveLandingFrameState("intel", { intel: {} });
  assert.equal(resolved.ctaLabel, "Look up ticker");
  assert.equal(resolved.ctaHref, "#intelTickerInput");
  assert.equal(resolved.altCtaLabel, "Open tables");
});

test("intel chips: no Data — placeholder before first compute", () => {
  const chips = labels("intel", { intel: {} });
  assert.ok(chips.every((l) => !l.includes("—")));
  assert.ok(chips.includes("No intel yet") || chips.includes("Intel not loaded"));
});

test("system chips: checking API, not API check placeholder", () => {
  const chips = labels("diagnostics", { health: { authState: "unknown", quoteOk: null, errRate: null } });
  assert.equal(chips.includes("API check"), false);
  assert.equal(chips.includes("Can trade"), false);
  assert.ok(chips.includes("Checking API"));
  assert.ok(chips.includes("Checking safety"));
});

test("system landing CTA: Connect Schwab is the secondary action", () => {
  const resolved = resolveLandingFrameState("diagnostics", {
    health: { authState: "connected", quoteOk: true, errRate: 0 },
    blockers: [],
  });
  assert.equal(resolved.altCtaLabel, "Connect Schwab");
  assert.equal(resolved.altCtaHref, "#onboardingSection");
  assert.equal(resolved.ctaLabel, "Health summary");
});

test("a handful of empty price frames is not a scan blocker", () => {
  const banner = selectScanBlockerBanner({
    blockers: [
      { key: "df_empty", value: 6, severity: "error" },
      { key: "forensic_filtered", value: 6, severity: "warn" },
      { key: "bars_stale", value: 2, severity: "warn" },
    ],
    scanBlocked: false,
    signalsFound: 45,
    watchlistSize: 1503,
    dataQuality: "ok",
  });
  assert.equal(banner, null);
});

test("a large price-history gap names the count", () => {
  const banner = selectScanBlockerBanner({
    blockers: [{ key: "df_empty", value: 120, severity: "error" }],
    scanBlocked: false,
    signalsFound: 10,
    watchlistSize: 1500,
    dataQuality: "ok",
  });
  assert.equal(banner.title, "P2: Price history gap");
  assert.match(banner.message, /120 of 1500 names had no price history/);
  assert.match(banner.message, /10 signals/);
});

test("processing errors stay on the action center", () => {
  const banner = selectScanBlockerBanner({
    blockers: [{ key: "exceptions", value: 4, severity: "error" }],
    scanBlocked: false,
    signalsFound: 12,
    watchlistSize: 1500,
    dataQuality: "ok",
  });
  assert.equal(banner.title, "P2: Scan Blocker Identified");
  assert.match(banner.message, /Processing errors: 4/);
});

test("a regime block uses the scan headline", () => {
  const banner = selectScanBlockerBanner({
    blockers: [],
    headline: "Scan blocked by regime gate: SPY is below 200 SMA.",
    scanBlocked: true,
    signalsFound: 0,
    watchlistSize: 1500,
    dataQuality: "ok",
  });
  assert.equal(banner.title, "P2: Scan Blocked");
  assert.match(banner.message, /200 SMA/);
});

test("system chips: probe failure is blocked across the row", () => {
  const resolved = resolveLandingFrameState("diagnostics", {
    health: { probeFailed: true, probeError: "Status check unavailable." },
    blockers: [],
  });
  assert.deepEqual(
    resolved.chips.map((c) => c.label),
    ["Health unknown", "Quotes unknown", "API unknown"],
  );
  assert.ok(resolved.chips.every((c) => c.tone === "blocked"));
});
