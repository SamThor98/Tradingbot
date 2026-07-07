/**
 * Unit tests for webapp/static/modules/format.js (pure formatting helpers).
 * Run with `node --test tests/js` from schwab_skill/ (see tests/test_js_unit.py).
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  safeText,
  escapeHtml,
  safeNum,
  pct,
  formatPercentPoints,
  clampPct,
  verdictFromScore,
  timeAgo,
  durationSec,
  formatBps,
  formatInt,
  formatCount,
  formatCents,
  formatSignedDelta,
  formatDecimal,
} from "../../webapp/static/modules/format.js";

test("safeText renders em-dash for null/undefined and stringifies everything else", () => {
  assert.equal(safeText(null), "—");
  assert.equal(safeText(undefined), "—");
  assert.equal(safeText(0), "0");
  assert.equal(safeText(""), "");
  assert.equal(safeText("AAPL"), "AAPL");
});

test("escapeHtml escapes markup-significant characters", () => {
  assert.equal(escapeHtml('<b a="1">&</b>'), "&lt;b a=&quot;1&quot;&gt;&amp;&lt;/b&gt;");
  assert.equal(escapeHtml("plain"), "plain");
});

test("safeNum falls back on non-finite input", () => {
  assert.equal(safeNum("42.5"), 42.5);
  assert.equal(safeNum("not-a-number"), 0);
  assert.equal(safeNum(Infinity, 7), 7);
  // Number(null) coerces to 0, so the fallback does NOT apply — documented quirk.
  assert.equal(safeNum(null, -1), 0);
});

test("pct formats 0-1 ratios as percentages", () => {
  assert.equal(pct(0.423), "42.3%");
  assert.equal(pct(1), "100.0%");
  assert.equal(pct("bad"), "—");
});

test("formatPercentPoints leaves percent-point values unscaled", () => {
  assert.equal(formatPercentPoints(55.2), "55.20%");
  // Number(null) coerces to 0 — only truly non-numeric input gets the em-dash.
  assert.equal(formatPercentPoints(null), "0.00%");
  assert.equal(formatPercentPoints("junk"), "—");
  assert.equal(formatPercentPoints(undefined), "—");
});

test("clampPct clamps into [0, 100]", () => {
  assert.equal(clampPct(-5), 0);
  assert.equal(clampPct(150), 100);
  assert.equal(clampPct(33), 33);
});

test("verdictFromScore maps score bands to verdicts", () => {
  assert.equal(verdictFromScore(80), "bullish");
  assert.equal(verdictFromScore(70), "bullish");
  assert.equal(verdictFromScore(50), "neutral");
  assert.equal(verdictFromScore(45), "bearish");
  assert.equal(verdictFromScore(10), "bearish");
});

test("timeAgo buckets seconds/minutes/hours/days", () => {
  const now = Date.now();
  assert.equal(timeAgo(new Date(now - 5_000).toISOString()), "5s ago");
  assert.equal(timeAgo(new Date(now - 3 * 60_000).toISOString()), "3m ago");
  assert.equal(timeAgo(new Date(now - 2 * 3_600_000).toISOString()), "2h ago");
  assert.equal(timeAgo(new Date(now - 3 * 86_400_000).toISOString()), "3d ago");
  assert.equal(timeAgo(""), "unknown");
  assert.equal(timeAgo("garbage"), "unknown");
});

test("durationSec returns whole seconds or null for invalid ranges", () => {
  assert.equal(durationSec("2026-01-01T00:00:00Z", "2026-01-01T00:01:30Z"), 90);
  assert.equal(durationSec("2026-01-01T00:01:00Z", "2026-01-01T00:00:00Z"), null);
  assert.equal(durationSec("bad", "2026-01-01T00:00:00Z"), null);
});

test("formatBps renders bps or percent per options", () => {
  assert.equal(formatBps(25), "25.0 bps");
  assert.equal(formatBps(25, { asPercent: true, digits: 2 }), "0.25%");
  assert.equal(formatBps("x"), "—");
});

test("formatInt and formatCount distinguish missing from zero", () => {
  assert.equal(formatInt(1234.6), (1235).toLocaleString());
  assert.equal(formatInt("junk"), "—");
  assert.equal(formatCount(0), "0");
  assert.equal(formatCount(null), "—");
  assert.equal(formatCount(""), "—");
  assert.equal(formatCount(-3), "0");
});

test("formatCents converts integer cents to dollars", () => {
  assert.equal(formatCents(12345), `$${(123.45).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
  assert.equal(formatCents("bad"), "—");
});

test("formatSignedDelta signs positive and negative values", () => {
  assert.equal(formatSignedDelta(1.5), "+1.50");
  assert.equal(formatSignedDelta(-1.5), "-1.50");
  assert.equal(formatSignedDelta(0), "0.00");
  assert.equal(formatSignedDelta(NaN), "—");
  assert.equal(formatSignedDelta(-2, (n) => `$${n.toFixed(0)}`), "-$2");
});

test("formatDecimal honors digits and fallback", () => {
  assert.equal(formatDecimal(12.345, 2), "12.35");
  assert.equal(formatDecimal("nope"), "—");
  assert.equal(formatDecimal(undefined, 1, "n/a"), "n/a");
});
