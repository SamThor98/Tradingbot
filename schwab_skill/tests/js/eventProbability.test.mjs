import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { eventRiskHtml } from "../../webapp/static/panels/eventProbability.js";

const EVENT = {
  event: {
    event_id: "stub:china-semi-export-2026",
    event_name: "Will the US impose tighter China export restrictions?",
    event_category: "export_control",
    adjusted_probability: 0.432,
    probability_change_7d: -0.031,
  },
  mapping: { fundamental_variable: "revenue_growth", materiality: 0.6 },
  sensitivity: { dollar_per_pp: -0.1948, modeled_valuation_impact: -8.41 },
};

const OK_PAYLOAD = {
  status: "ok",
  ticker: "NVDA",
  config: { ems_lookback_days: 7 },
  assumptions: ["Events are treated as statistically independent (Version 1)."],
  warnings: [],
  events: [EVENT],
  summary: {
    event_count: 1,
    base_value: 159.61,
    event_adjusted_expected_value: 154.6,
    valuation_adjustment: -5.01,
    valuation_adjustment_pct: -2.19,
    event_mispricing_score: 3.32,
    largest_negative_exposure: { modeled_valuation_impact: -8.41, fundamental_variable: "revenue_growth" },
    largest_positive_exposure: null,
    distribution: {
      expected_value: 154.6,
      median_value: 159.61,
      p10: 141.2,
      p90: 159.61,
      prob_positive_return: 0.0,
      prob_return_over_25: 0.0,
      prob_loss: 1.0,
    },
  },
};

describe("eventRiskHtml status branches", () => {
  it("explains how to turn the layer on when it is disabled", () => {
    const html = eventRiskHtml({ status: "disabled", ticker: "NVDA" });
    assert.match(html, /EVENT_PROB_ENABLED/);
    assert.doesNotMatch(html, /event-risk-kpis/);
  });

  it("points at the operator catalog when a ticker has no mapped events", () => {
    const html = eventRiskHtml({ status: "no_exposure", ticker: "MSFT" });
    assert.match(html, /No mapped events for MSFT/);
    assert.match(html, /event_exposure_catalog\.json/);
  });

  it("points at ingest when events are mapped but unobserved", () => {
    const html = eventRiskHtml({ status: "no_data", ticker: "NVDA" });
    assert.match(html, /event-probability\/ingest/);
  });

  it("still lists events when the valuation is unavailable", () => {
    const html = eventRiskHtml({
      status: "valuation_unavailable",
      ticker: "NVDA",
      reason: "fcf_unavailable:NVDA",
      events: [{ ...EVENT, sensitivity: null }],
    });
    assert.match(html, /valuation unavailable/i);
    assert.match(html, /fcf_unavailable:NVDA/);
    assert.match(html, /export control/);
    // No sensitivity means the numeric columns are blanked, not zeroed.
    assert.doesNotMatch(html, /\$0\.00/);
  });
});

describe("eventRiskHtml ok payload", () => {
  it("renders the four snapshot KPIs with signed values", () => {
    const html = eventRiskHtml(OK_PAYLOAD);
    assert.match(html, /Event-adjusted value/);
    assert.match(html, /\$154\.60/);
    assert.match(html, /base \$159\.61/);
    assert.match(html, /Valuation adjustment/);
    assert.match(html, /-\$5\.01/);
    assert.match(html, /-2\.19% of price/);
    assert.match(html, /Largest exposure/);
    assert.match(html, /Mispricing gap/);
    assert.match(html, /\+3\.32%/);
  });

  it("renders the event row with probability, drift, and per-pp sensitivity", () => {
    const html = eventRiskHtml(OK_PAYLOAD);
    assert.match(html, /43\.1%|43\.2%/);
    assert.match(html, /-3\.1pp/);
    assert.match(html, /-\$0\.19/);
    assert.match(html, /materiality 0\.60/);
  });

  it("tones a negative adjustment bad and a positive gap good", () => {
    const html = eventRiskHtml(OK_PAYLOAD);
    assert.match(html, /event-risk-kpi--bad/);
    assert.match(html, /event-risk-kpi--good/);
  });

  it("surfaces the independence assumption and the not-a-signal footnote", () => {
    const html = eventRiskHtml(OK_PAYLOAD);
    assert.match(html, /statistically independent/);
    assert.match(html, /Does not change scan scores/);
  });

  it("summarizes warnings instead of hiding them", () => {
    const html = eventRiskHtml({
      ...OK_PAYLOAD,
      warnings: [{ event_id: "stub:fed-cut-2026h2", warning: "stale_event" }],
    });
    assert.match(html, /1 event warning\./);
    assert.match(html, /stale_event/);
  });

  it("escapes event text rather than injecting it", () => {
    const html = eventRiskHtml({
      ...OK_PAYLOAD,
      events: [{ ...EVENT, event: { ...EVENT.event, event_name: "<img src=x onerror=alert(1)>" } }],
    });
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /&lt;img/);
  });

  it("does not fabricate numbers from a missing summary", () => {
    const html = eventRiskHtml({ status: "ok", ticker: "NVDA", events: [], summary: {} });
    assert.match(html, /—/);
    assert.match(html, /No events to show/);
  });
});
