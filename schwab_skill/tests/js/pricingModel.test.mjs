import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { pricingModelHtml } from "../../webapp/static/panels/pricingModel.js";

const OK_PAYLOAD = {
  status: "ok",
  ticker: "AAPL",
  price_source: "schwab.quote",
  warnings: [],
  provenance: {
    source: "sec.companyfacts",
    cik: "0000320193",
    as_of: "2023-09-30",
    from_cache: true,
  },
  inputs: {
    growth: 0.12,
    wacc: 0.10,
    terminal_growth: 0.025,
    last_fcf: 19200000000,
    net_debt: 30000000000,
    shares: 15000000000,
    price: 180,
  },
  implied_growth: 0.11,
  implied_wacc: 0.09,
  dcf: {
    intrinsic_value: 210.5,
    current_price: 180,
    margin_of_safety: 16.9,
    fcf_history: [{ year: "2023", fcf: 19200000000 }],
    projected_fcf: [{ year: 1, fcf: 21504000000, pv: 19549090909 }],
    sensitivity: [{ wacc: 0.1, "tg_0.02": 200, "tg_0.025": 210.5, "tg_0.03": 222 }],
  },
};

describe("pricingModelHtml status branches", () => {
  it("explains how to enable companyfacts when disabled", () => {
    const html = pricingModelHtml({ status: "disabled", ticker: "AAPL" });
    assert.match(html, /COMPANYFACTS_ENABLED/);
    assert.doesNotMatch(html, /pricing-model-kpis/);
  });

  it("asks the operator to ingest when cache is empty", () => {
    const html = pricingModelHtml({ status: "not_ingested", ticker: "AAPL" });
    assert.match(html, /No cached companyfacts/);
    assert.match(html, /Refresh fundamentals/);
  });

  it("renders intrinsic value, MOS, reverse DCF, and assumption sliders", () => {
    const html = pricingModelHtml(OK_PAYLOAD);
    assert.match(html, /pricing-model-kpis/);
    assert.match(html, /sec\.companyfacts/);
    assert.match(html, /0000320193/);
    assert.match(html, /pricingGrowth/);
    assert.match(html, /Implied growth/);
    assert.match(html, /do not change scan scores/);
    assert.match(html, /SEC companyfacts/);
    assert.match(html, /Sharadar Bundle/);
  });

  it("shows Sharadar EV beside DCF without blending", () => {
    const html = pricingModelHtml({
      ...OK_PAYLOAD,
      sharadar_cross_check: {
        available: true,
        status: "ok",
        enabled: true,
        data_quality: "ok",
        as_of: "2026-09-18",
        ev: 3.5e12,
        evebitda: 22.1,
        fcf: 1.1e11,
        shares: 15e9,
      },
    });
    assert.match(html, /Enterprise value/);
    assert.match(html, /not mixed into the companyfacts DCF/);
    assert.doesNotMatch(html, /fall back to Yahoo/);
  });
});
