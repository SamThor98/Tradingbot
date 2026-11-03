import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { sharadarBriefStripHtml } from "../../webapp/static/modules/sharadarBrief.js";

describe("sharadarBriefStripHtml", () => {
  it("omits null chips and hides when unavailable", () => {
    assert.equal(sharadarBriefStripHtml({ available: false, chips: [] }), "");
    const html = sharadarBriefStripHtml({
      available: true,
      provenance: "Sharadar daily · as-of 2026-09-18 · cached · data_quality=ok",
      chips: [
        { id: "marketcap", value: 3.2e12 },
        { id: "pe", value: 32.1 },
      ],
      holders: [{ name: "Vanguard", units: 1e9, percent: 8.1 }],
      institutional_ownership: 8.1,
      insiders: [{ filingdate: "2026-08-15", ownername: "Cook", transactioncode: "S", transactionshares: -1000 }],
      insider_net: { buys: 0, sells: 1 },
      actions: [],
    });
    assert.match(html, /Market cap/);
    assert.match(html, /P\/E/);
    assert.doesNotMatch(html, /P\/S/);
    assert.match(html, /Vanguard/);
    assert.match(html, /Cook/);
    assert.doesNotMatch(html, /Corporate actions/);
    assert.doesNotMatch(html, /data_quality=/);
    assert.match(html, /Data quality: ok/);
  });
});
