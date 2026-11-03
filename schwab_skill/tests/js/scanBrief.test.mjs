/**
 * Unit tests for Evaluate-setup local brief builder.
 */
import test from "node:test";
import assert from "node:assert/strict";

const { buildLocalScanBrief, glanceLines, materialBriefLines, mergeScanBriefs } = await import(
  "../../webapp/static/modules/scanBrief.js"
);

test("buildLocalScanBrief fills every dropdown section from a sparse row", () => {
  const brief = buildLocalScanBrief({ ticker: "AAPL", symbol: "AAPL" });
  assert.match(brief.setup_summary, /AAPL/);
  assert.ok(brief.key_risks.length);
  assert.ok(brief.catalyst_notes.length);
  assert.ok(brief.forensic_flags.length);
  assert.ok(brief.sec_notes.length);
  assert.match(brief.expected_move_window, /10 trading days/);
  assert.ok(brief.entry_stop_ideas.length);
});

test("buildLocalScanBrief uses PEAD, forensic, and volume fields", () => {
  const brief = buildLocalScanBrief({
    ticker: "MSFT",
    price: 250,
    composite_score: 64,
    advisory: { confidence_bucket: "high", p_up_10d: 0.58 },
    strategy_attribution: { top_live: "trend_breakout" },
    volume_ratio: 1.4,
    pead_surprise_pct: 0.08,
    pead_beat: true,
    forensic_flags: ["sloan_high"],
    forensic_sloan: 0.12,
    sec_risk_tag: "medium",
    filing_recency_days: 5,
    sma_200: 200,
  });
  assert.ok(brief.catalyst_notes.some((x) => /earnings surprise/i.test(x)));
  assert.ok(brief.forensic_flags.includes("sloan_high"));
  assert.ok(brief.sec_notes.some((x) => /medium/i.test(x)));
  assert.ok(brief.entry_stop_ideas.some((x) => /200-day SMA/i.test(x)));
  assert.ok(brief.entry_stop_ideas.some((x) => /3% under the 200-day/i.test(x)));
  assert.equal(brief.entry_stop_ideas.some((x) => /entry zone/i.test(x)), false);
  assert.equal(brief.entry_stop_ideas.some((x) => /advisory 7%/i.test(x)), false);
  assert.match(brief.expected_move_window, /P\(up\)/);
});

test("mergeScanBriefs unions local and remote lists instead of replacing", () => {
  const local = buildLocalScanBrief({ ticker: "AAPL", volume_ratio: 1.2, sector_etf: "XLK" });
  const merged = mergeScanBriefs(local, {
    setup_summary:
      "Remote summary that is definitely longer than the local scan-row sentence, including volume, strategy, and confidence.",
    key_risks: [],
    catalyst_notes: ["Remote catalyst"],
    entry_stop_ideas: ["Sizing preview: 10 shares (~$5000.0)."],
  });
  assert.match(merged.setup_summary, /Remote summary/);
  assert.ok(merged.key_risks.length);
  assert.ok(merged.catalyst_notes.some((x) => /XLK|relative volume/i.test(x)));
  assert.ok(merged.catalyst_notes.includes("Remote catalyst"));
  assert.ok(merged.entry_stop_ideas.some((x) => /Sizing preview/i.test(x)));
});

test("large earnings surprise and breakout buffer stay fractions", () => {
  const brief = buildLocalScanBrief({
    ticker: "MSFT",
    price: 250,
    pead_surprise_pct: 1.2,
    pead_beat: true,
    sma_50: 119,
    entry_timing_shadow: { breakout_buffer_pct: 1.5, pct_above_sma50: 1.1 },
  });
  assert.ok(brief.catalyst_notes.some((x) => x.includes("120.0%")));
  assert.ok(brief.entry_stop_ideas.some((x) => x.includes("150.0%")));
  assert.ok(brief.entry_stop_ideas.some((x) => /7% under price, 200-day missing/i.test(x)));
  assert.equal(brief.entry_stop_ideas.some((x) => /entry zone/i.test(x)), false);
});

test("glance keeps catalysts that change the call and drops filler labels", () => {
  const material = materialBriefLines([
    "No hard risk blockers on this scan row.",
    "Data quality is degraded.",
    "SEC risk tag: unknown.",
    "SEC enrichment was not attached beyond the default unknown tag.",
  ]);
  assert.deepEqual(material, ["Data quality is degraded."]);
  assert.deepEqual(
    glanceLines([
      "Primary strategy signal: trend.",
      "Scan horizon: weekly.",
      "Positive earnings surprise: 8.0%.",
      "Relative volume vs 50-day average: 1.40x.",
      "Sector ETF: XLK.",
    ]),
    ["Positive earnings surprise: 8.0%.", "Relative volume vs 50-day average: 1.40x."],
  );
});

test("buildLocalScanBrief derives relative volume from latest/avg when ratio missing", () => {
  const brief = buildLocalScanBrief({
    ticker: "NVDA",
    latest_volume: 40,
    avg_vol_50: 100,
    timeframe: "weekly",
  });
  assert.ok(brief.catalyst_notes.some((x) => /0\.40x/i.test(x)));
  assert.ok(brief.catalyst_notes.some((x) => /weekly/i.test(x)));
});
