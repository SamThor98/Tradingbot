/**
 * Public percent-field contract: producer unit → helper → label.
 * A reviewer must see that 0.423 never becomes 4230% and 55.2 never becomes 0.6%.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  pct,
  formatPercentPoints,
  formatSignedPercentPoints,
  formatSignedRatioPct,
  formatBps,
} from "../../webapp/static/modules/format.js";
import { flattenDiagnosticValue } from "../../webapp/static/modules/typedView.js";

const HELPERS = {
  pct,
  formatPercentPoints,
  formatSignedPercentPoints,
  formatSignedRatioPct,
  formatBps,
};

/**
 * Every operator-visible percent field: sample API value and the only legal label.
 * Units are taken from the Python producer, not a JS abs() heuristic.
 */
const PUBLIC_PERCENT_FIELDS = [
  {
    field: "advisory.p_up_10d / p_up_calibrated",
    producer: "advisory_model.py / signal_scanner.py",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.423,
    expectedLabel: "42.3%",
  },
  {
    field: "win_rate_net",
    producer: "backtest.py 100*wins/total",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 48.0,
    expectedLabel: "48.00%",
  },
  {
    field: "total_return_net_pct",
    producer: "backtest.py 100*total_ret_net",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 18,
    expectedLabel: "18.00%",
  },
  {
    field: "cagr_net_pct",
    producer: "backtest.py 100*cagr_net",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 11.25,
    expectedLabel: "11.25%",
  },
  {
    field: "max_drawdown_net_pct",
    producer: "backtest.py 100*max_dd (signed ≤ 0)",
    unit: "signed percent points",
    helper: "formatSignedPercentPoints",
    sampleApiValue: -18.0,
    expectedLabel: "-18.00%",
  },
  {
    field: "avg_return_net_pct / performance.avg_return_pct",
    producer: "learning.py aliases backtest avg_return_net_pct",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 2.41,
    expectedLabel: "2.41%",
  },
  {
    field: "performance.win_rate",
    producer: "learning.py aliases win_rate_net",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 55.2,
    expectedLabel: "55.20%",
  },
  {
    field: "self_study.win_rate",
    producer: "self_study.py run_self_study",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 58.3,
    expectedLabel: "58.30%",
  },
  {
    field: "coverage_pct",
    producer: "packet_feature_analysis.py resolved/n*100",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 42.5,
    expectedLabel: "42.50%",
  },
  {
    field: "feature_lift.win_rate / packet win_rate",
    producer: "packet_feature_analysis.py wins/len",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.6,
    expectedLabel: "60.0%",
  },
  {
    field: "avg_return_pct (packet cohort)",
    producer: "packet_feature_analysis.py _avg(realized_return_pct)",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 1.25,
    expectedLabel: "1.25%",
  },
  {
    field: "realized_return_pct",
    producer: "outcome_backfill.py (exit-entry)/entry*100",
    unit: "percent points",
    helper: "formatSignedPercentPoints",
    sampleApiValue: 4.2,
    expectedLabel: "+4.20%",
  },
  {
    field: "fp_rate",
    producer: "trade_review.py losses/resolved",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.5,
    expectedLabel: "50.0%",
  },
  {
    field: "relative_lift_vs_baseline",
    producer: "score_ablation_report.py (v-b)/|b|",
    unit: "unitless ratio",
    helper: "formatSignedRatioPct",
    sampleApiValue: 0.123,
    expectedLabel: "+12.3%",
  },
  {
    field: "early_stopout_pct",
    producer: "analyze_early_stopout_cohorts.py 100*count/n",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 33.86,
    expectedLabel: "33.86%",
  },
  {
    field: "retention_pct",
    producer: "analyze_signal_stack_counterfactual.py",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 25.0,
    expectedLabel: "25.00%",
  },
  {
    field: "hypothesis hit_rate",
    producer: "hypothesis_ledger.py hits/n",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.62,
    expectedLabel: "62.0%",
  },
  {
    field: "hv20 / garch",
    producer: "position_intel.py annualized vol decimal",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.6,
    expectedLabel: "60.0%",
  },
  {
    field: "p_win / p_keep",
    producer: "position_intel.py select_long_call / covered",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.55,
    expectedLabel: "55.0%",
  },
  {
    field: "wacc / implied_growth / implied_wacc",
    producer: "companyfacts/model.py DEFAULT_WACC=0.10",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.1,
    expectedLabel: "10.0%",
  },
  {
    field: "dcf.margin_of_safety",
    producer: "companyfacts/model.py (iv-price)/price*100",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 16.9,
    expectedLabel: "16.90%",
  },
  {
    field: "valuation_adjustment_pct",
    producer: "event_probability/service.py (adj/price)*100",
    unit: "percent points",
    helper: "formatSignedPercentPoints",
    sampleApiValue: -2.19,
    expectedLabel: "-2.19%",
  },
  {
    field: "event_mispricing_score / ems_gap_pct",
    producer: "event_probability/signal.py percent points",
    unit: "percent points",
    helper: "formatSignedPercentPoints",
    sampleApiValue: 3.32,
    expectedLabel: "+3.32%",
  },
  {
    field: "adjusted_probability",
    producer: "event_probability event.adjusted_probability",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.432,
    expectedLabel: "43.2%",
  },
  {
    field: "sharadar.roe",
    producer: "sharadar_data.py map_fundamentals_row (ratio; 1.47=147%)",
    unit: "ratio 0–1 (may exceed 1)",
    helper: "pct",
    sampleApiValue: 1.47,
    expectedLabel: "147.0%",
  },
  {
    field: "institutional_ownership / holder.percent",
    producer: "sharadar_data.py pit_fields_from_parts sum of SF3 percent (tests: 4.0+2.5=6.5)",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 6.5,
    expectedLabel: "6.50%",
  },
  {
    field: "Finnhub revenue_growth_ttm_yoy / operating_margin_ttm / roe_ttm",
    producer: "research.py _pct_finnhub — already percent points",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 31.0,
    expectedLabel: "31.00%",
  },
  {
    field: "surprise_percent",
    producer: "finnhub_data.py passthrough; fixtures treat as points",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 8.2,
    expectedLabel: "8.20%",
  },
  {
    field: "report_trust.data_confidence",
    producer: "report_trust.py 0–1 ratio",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.84,
    expectedLabel: "84.0%",
  },
  {
    field: "portfolio max_drawdown_pct",
    producer: "portfolio_analytics.py drawdown_stats signed pp",
    unit: "signed percent points",
    helper: "formatSignedPercentPoints",
    sampleApiValue: -10.0,
    expectedLabel: "-10.00%",
  },
  {
    field: "daily_win_rate_pct",
    producer: "portfolio_risk_advanced.py daily_win_rate",
    unit: "percent points",
    helper: "formatPercentPoints",
    sampleApiValue: 54.2,
    expectedLabel: "54.20%",
  },
  {
    field: "closed-trade win_rate (risk KPI)",
    producer: "phase2_common.py win_rate wins/n",
    unit: "ratio 0–1",
    helper: "pct",
    sampleApiValue: 0.5,
    expectedLabel: "50.0%",
  },
  {
    field: "self_study.avg_return_pct",
    producer: "self_study.py percent points",
    unit: "signed percent points",
    helper: "formatSignedPercentPoints",
    sampleApiValue: 2.41,
    expectedLabel: "+2.41%",
  },
];

test("every public percent field maps producer unit to the canonical helper", () => {
  for (const row of PUBLIC_PERCENT_FIELDS) {
    const fn = HELPERS[row.helper];
    assert.equal(typeof fn, "function", `${row.field}: unknown helper ${row.helper}`);
    const args = row.digits != null ? [row.sampleApiValue, row.digits] : [row.sampleApiValue];
    const label = fn(...args);
    assert.equal(
      label,
      row.expectedLabel,
      `${row.field} (${row.producer}): ${row.sampleApiValue} via ${row.helper} → ${label}, expected ${row.expectedLabel}`,
    );
  }
});

test("wrong helper would explode or shrink — contract forbids that swap", () => {
  // ratio through points helper: 0.423 → "0.42%" (looks like 42bp, not 42%)
  assert.equal(formatPercentPoints(0.423), "0.42%");
  // points through ratio helper: 55.2 → "5520.0%"
  assert.equal(pct(55.2), "5520.0%");
  // Neither of those labels appears in PUBLIC_PERCENT_FIELDS expectedLabel.
  const labels = PUBLIC_PERCENT_FIELDS.map((r) => r.expectedLabel);
  assert.ok(!labels.includes("0.42%"));
  assert.ok(!labels.includes("5520.0%"));
  assert.ok(!labels.includes("4230%"));
  assert.ok(!labels.includes("0.6%"));
});

test("flattenDiagnosticValue never JSON.stringifies objects", () => {
  assert.equal(flattenDiagnosticValue(null), "—");
  assert.equal(flattenDiagnosticValue({ stage_a_candidates: 12, df_empty: 0 }), "Passed quick filter 12 · No price data 0");
  assert.doesNotMatch(flattenDiagnosticValue({ a: 1, b: { c: 2 } }), /\{/);
  assert.doesNotMatch(flattenDiagnosticValue({ a: 1 }), /"/);
});

test("research surfaces do not invent a Confidence percent", () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "webapp", "static");
  const files = [
    "modules/researchPanelSnapshot.js",
    "panels/portfolioRisk.js",
    "panels/quickCheck.js",
    "panels/sec.js",
  ];
  for (const rel of files) {
    const src = readFileSync(join(root, rel), "utf8");
    assert.doesNotMatch(src, /Confidence \$\{/);
    assert.doesNotMatch(src, /confidence:\s*(10|28|34|50|58|62|86|88)/);
  }
  const snapshot = readFileSync(join(root, "modules/researchPanelSnapshot.js"), "utf8");
  assert.match(snapshot, /meter\.label/);
  assert.match(snapshot, /meter\.unit/);
});
