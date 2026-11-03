import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { sharadarBundleHtml } from "../../webapp/static/panels/sharadarFundamentals.js";

const BASE = {
  ok: true,
  enabled: true,
  status: "ok",
  ticker: "AAPL",
  as_of: "2026-09-18",
  from_cache: true,
  errors: [],
  warnings: [],
  data_quality: "ok",
  profile: { name: "Apple Inc", exchange: "NASDAQ" },
  daily: { date: "2026-09-18", marketcap: 3.2e12, pe: 32.1 },
  fundamentals: { revenue: 4e11, fcf: 1e11, roe: 1.47, datekey: "2026-08-01" },
  pit_fields: {},
  insiders: [],
  holders: [],
  actions: [],
  events: [],
  sp500: { is_member: true },
};

describe("sharadarBundleHtml ART sparkline and actions", () => {
  it("plots history against datekey and hides empty action strip", () => {
    const html = sharadarBundleHtml({
      ...BASE,
      fundamentals_history: [
        { datekey: "2025-08-01", calendardate: "2025-06-30", revenue: 3e11, fcf: 8e10, roe: 1.4 },
        { datekey: "2026-08-01", calendardate: "2026-06-30", revenue: 4e11, fcf: 1e11, roe: 1.47 },
      ],
    });
    assert.match(html, /2026-08-01/);
    assert.match(html, /sharadar-sparkline/);
    assert.doesNotMatch(html, /2025-06-30/);
    assert.doesNotMatch(html, /sharadar-action-strip/);
    assert.doesNotMatch(html, />Actions \/ events</);
  });

  it("does not plot calendardate or invent $0 for missing ART cells", () => {
    const html = sharadarBundleHtml({
      ...BASE,
      fundamentals_history: [
        { datekey: "2025-08-01", calendardate: "2025-06-30", revenue: null, fcf: 8e10, roe: 1.4 },
        { datekey: "2026-08-01", calendardate: "2026-06-30", revenue: 4e11, fcf: 1e11, roe: 1.47 },
      ],
    });
    assert.match(html, /2026-08-01/);
    assert.doesNotMatch(html, /\$0\.00/);
    assert.doesNotMatch(html, /2025-06-30/);
  });

  it("shows corporate-action strip for splits and dividends", () => {
    const html = sharadarBundleHtml({
      ...BASE,
      fundamentals_history: [],
      actions: [{ date: "2026-05-01", event: "dividend" }],
      events: [{ date: "2026-06-01", event: "earnings call" }],
    });
    assert.match(html, /sharadar-action-strip/);
    assert.match(html, /dividend/);
    assert.doesNotMatch(html, /earnings call/);
  });

  it("asks for SHARADAR_API_KEY when disabled", () => {
    const html = sharadarBundleHtml({
      status: "disabled",
      enabled: false,
      errors: ["missing_sharadar_api_key"],
    });
    assert.match(html, /System → Connect/);
    assert.doesNotMatch(html, /\$0/);
  });
});
