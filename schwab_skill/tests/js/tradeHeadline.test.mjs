import test from "node:test";
import assert from "node:assert/strict";

const { tradeHeadlineLines } = await import("../../webapp/static/modules/tradeHeadline.js");

test("empty selection is one line", () => {
  const lines = tradeHeadlineLines({});
  assert.equal(lines.empty, true);
  assert.equal(lines.entry, "Select a name from the list.");
  assert.equal(lines.stop, "");
  assert.equal(lines.size, "");
});

test("stop names the 200-day rule and does not invent size", () => {
  const lines = tradeHeadlineLines({ ticker: "NVDA", price: 142.1, sma200: 132.371, qty: null, sizeUsd: null });
  assert.equal(lines.entry, "Entry 142.10");
  assert.equal(lines.stop, "Stop 128.40 — 3% under the 200-day");
  assert.equal(lines.size, "Size unavailable.");
});

test("missing 200-day uses the 7% band and size comes only from the card", () => {
  const lines = tradeHeadlineLines({ ticker: "NVDA", price: 142.1, sma200: null, qty: 70, sizeUsd: 9947 });
  assert.equal(lines.stop, "Stop 132.15 — 7% under price, 200-day missing");
  assert.match(lines.size, /^70 shares · \$9,947\.00$/);
  assert.equal(lines.stop.includes("Entry zone"), false);
});
