/**
 * Unit tests for scanner filter / data-quality reason copy.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  formatFilterReasons,
  formatDataQualityReason,
  formatScanStatusBadge,
  scanStatusSeverityBucket,
} from "../../webapp/static/modules/filterReasons.js";

test("formatFilterReasons maps known codes to English", () => {
  assert.deepEqual(formatFilterReasons(["low_signal_score", "bars_stale"]), [
    "Signal score below minimum",
    "Data: price bars are stale",
  ]);
});

test("formatDataQualityReason humanizes quote/bar staleness prefixes", () => {
  assert.equal(
    formatDataQualityReason("quote_stale_age_sec_120_gt_60"),
    "Market quotes are stale",
  );
  assert.equal(
    formatDataQualityReason("bars_stale_last_bar_2024-01-01_age_days_4_gt_2"),
    "Price bars are stale",
  );
  assert.equal(
    formatDataQualityReason("sec_cache_stale_age_h_72.6_gt_72.0"),
    "SEC filing cache is stale",
  );
  assert.equal(formatDataQualityReason("sec_cache_empty_or_missing"), "SEC filing cache is empty");
  assert.equal(formatDataQualityReason("quote_unavailable"), "Quote unavailable");
  assert.equal(formatDataQualityReason("provider_fallback_polygon"), "Using fallback market data");
  assert.equal(formatDataQualityReason("totally_new_code"), "Totally New Code");
});

test("formatScanStatusBadge keeps Tradeable for kept rows", () => {
  const badge = formatScanStatusBadge("kept", []);
  assert.equal(badge.label, "Tradeable");
  assert.equal(scanStatusSeverityBucket("kept"), "pass");
  assert.equal(scanStatusSeverityBucket("trimmed_top_n"), "info");
  assert.equal(scanStatusSeverityBucket("filtered_quality_gates"), "blocked");
});
