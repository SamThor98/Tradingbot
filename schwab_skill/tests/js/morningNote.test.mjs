import test from "node:test";
import assert from "node:assert/strict";

const { morningNoteCopy } = await import("../../webapp/static/modules/morningNote.js");

test("empty book is three sentences and does not invent a ticker", () => {
  const note = morningNoteCopy({});
  assert.deepEqual(note, {
    allow: "Scans are allowed.",
    name: "No name to open yet.",
    risk: "Run a scan.",
  });
});

test("blockers win in halt, then Schwab, then regime order", () => {
  assert.equal(
    morningNoteCopy({ killSwitch: true, schwabDisconnected: true, scanBlocked: true }).allow,
    "Scans are blocked: trading halt.",
  );
  assert.equal(
    morningNoteCopy({ schwabDisconnected: true, scanBlocked: true }).allow,
    "Scans are blocked: Schwab is not connected.",
  );
  assert.equal(morningNoteCopy({ scanBlocked: true }).allow, "Scans are blocked: regime.");
});

test("first skip-risk on the named row, one sentence", () => {
  const note = morningNoteCopy({
    row: {
      ticker: "nvda",
      sec_risk_tag: "high",
      advisory: { confidence_bucket: "low" },
      breakout_confirmed: false,
    },
    strategyLabel: "Stage 2 breakout",
  });
  assert.equal(note.name, "Open NVDA first.");
  assert.equal(note.risk, "Skip it if you will not hold through a high SEC-risk tag.");
  assert.equal(note.risk.includes("low"), false);
});

test("a clean name says why, including the stop rule", () => {
  const note = morningNoteCopy({
    row: {
      ticker: "AAPL",
      price: 100,
      sma_200: 90,
      _filter_status: "kept",
      sec_risk_tag: "low",
      breakout_confirmed: true,
      advisory: { confidence_bucket: "high" },
    },
    strategyLabel: "Stage 2 breakout",
  });
  assert.equal(note.risk, "Clean: Stage 2 breakout, stop is 3% under the 200-day.");
});
