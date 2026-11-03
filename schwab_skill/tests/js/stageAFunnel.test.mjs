import test from "node:test";
import assert from "node:assert/strict";

import { stageAFunnelCounts } from "../../webapp/static/modules/stageAFunnel.js";

test("timeouts are not stage 2 passes", () => {
  const counts = stageAFunnelCounts({
    watchlist_size: 10,
    stage2_fail: 3,
    df_empty: 1,
    stage_a_timeouts: 4,
    exceptions: 6,
    stage_b_exceptions: 2,
  });
  assert.equal(counts.evaluated, 6);
  assert.equal(counts.unresolved, 4);
  assert.equal(counts.stage2Pass, 2);
});

test("research-only scan does not claim stage 2 passes", () => {
  const counts = stageAFunnelCounts({
    watchlist_size: 100,
    stage2_fail: 0,
    scan_arm_flags: { want_stage2: false, want_pead: false, want_horizon: ["weekly_swing"] },
  });
  assert.equal(counts.evaluated, 100);
  assert.equal(counts.stage2Pass, 0);
});

test("stage B exceptions do not shrink the evaluated count", () => {
  const counts = stageAFunnelCounts({
    watchlist_size: 5,
    stage2_fail: 1,
    stage_a_task_exceptions: 1,
    exceptions: 3,
  });
  assert.equal(counts.evaluated, 4);
  assert.equal(counts.stage2Pass, 3);
});
