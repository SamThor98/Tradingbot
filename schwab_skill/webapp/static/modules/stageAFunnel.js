/**
 * Stage A funnel counts shared by the diagnostics panel.
 * Timeouts are not Stage 2 passes. Mirrors core/stage_a_coverage.py.
 */

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

export function stageAFunnelCounts(diag = {}) {
  const watchlist = num(diag.watchlist_size);
  const unresolved = num(diag.stage_a_timeouts) + num(diag.stage_a_task_exceptions);
  const evaluated = Math.max(0, watchlist - unresolved);
  const preStage2 =
    num(diag.stage2_fail) + num(diag.df_empty) + num(diag.too_few_candles) + num(diag.bars_stale);
  // A research-only scan (e.g. weekly_swing) never runs the Stage 2 arm, so
  // stage2_fail stays 0. Reporting evaluated-0 as "Stage 2 passed" would
  // claim an uptrend check that never happened.
  const arms = diag.scan_arm_flags && typeof diag.scan_arm_flags === "object" ? diag.scan_arm_flags : null;
  const stage2Evaluated = !arms || arms.want_stage2 !== false;
  return {
    watchlist,
    unresolved,
    evaluated,
    stage2Pass: stage2Evaluated ? Math.max(0, evaluated - preStage2) : 0,
  };
}
