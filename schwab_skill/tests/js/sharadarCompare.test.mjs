import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { sharadarCompareHtml } from "../../webapp/static/panels/sharadarCompare.js";

describe("sharadarCompareHtml", () => {
  it("renders two tickers and keeps missing cells empty", () => {
    const html = sharadarCompareHtml({
      left: {
        ticker: "AAPL",
        name: "Apple",
        status: "ok",
        data_quality: "ok",
        as_of: "2026-09-18",
        roe: 1.47,
        revenue: 4e11,
        fcf: null,
        evebitda: 22,
        pe: 32.1,
      },
      right: {
        ticker: "MSFT",
        name: "Microsoft",
        status: "ok",
        data_quality: "ok",
        as_of: "2026-09-18",
        roe: 0.38,
        revenue: 2e11,
        fcf: 6e10,
        evebitda: 18,
        pe: 35,
      },
      fields: [
        { id: "roe", label: "ROE TTM" },
        { id: "fcf", label: "Free cash flow" },
        { id: "pe", label: "P/E" },
      ],
    });
    assert.match(html, /AAPL/);
    assert.match(html, /MSFT/);
    assert.match(html, /—/);
    assert.match(html, /does not change scan scores/);
  });
});
