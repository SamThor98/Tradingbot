/**
 * Unit tests for scan provenance + tradeable verdict chips.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  isScanSignalStageable,
  scanRowTriageBucket,
  renderTradeableVerdict,
  provenanceFromSignal,
} from "../../webapp/static/modules/signalProvenance.js";

test("isScanSignalStageable rejects weekly, PEAD paper, and filtered rows", () => {
  assert.equal(isScanSignalStageable({ _filter_status: "kept" }), true);
  assert.equal(isScanSignalStageable({ _filter_status: "kept", timeframe: "weekly" }), false);
  assert.equal(isScanSignalStageable({ _filter_status: "kept", entry_family: "pead_primary" }), false);
  assert.equal(isScanSignalStageable({ _filter_status: "filtered_quality_gates" }), false);
});

test("renderTradeableVerdict matches triage buckets in English", () => {
  assert.match(renderTradeableVerdict({ _filter_status: "kept" }), />Tradeable</);
  assert.match(
    renderTradeableVerdict({ _filter_status: "kept", timeframe: "weekly" }),
    />Research</,
  );
  assert.match(
    renderTradeableVerdict({ _filter_status: "filtered_quality_gates", _filter_reasons: ["low_signal_score"] }),
    />Blocked</,
  );
  assert.match(renderTradeableVerdict({ _filter_status: "trimmed_top_n" }), />Other</);
});

test("scanRowTriageBucket keeps horizon names in Research", () => {
  assert.equal(
    scanRowTriageBucket({ _filter_status: "kept", entry_family: "horizon" }),
    "review",
  );
});

test("provenanceFromSignal marks fallback as medium confidence", () => {
  const p = provenanceFromSignal({ data_provider: "yfinance", used_fallback_data: true });
  assert.equal(p.source, "yfinance");
  assert.equal(p.confidence, "medium");
});
