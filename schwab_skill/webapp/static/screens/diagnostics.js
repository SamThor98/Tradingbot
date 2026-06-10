/* Diagnostics screen controller.
 *
 * Owns one-time wiring (init) and screen-prime data loading (prime) for the
 * Diagnostics screen: calibration, shadow scoreboard, review loop, and the
 * decision dashboard. Dependencies are injected via ctx from app.js so
 * behavior is identical to the previous inline code.
 * Rollout flag: screen_controllers (see wiki [[section-migration-map]]).
 */

export function createDiagnosticsController(ctx) {
  const {
    bindEvent,
    refreshCalibration,
    refreshShadowScoreboard,
    refreshReviewLoop,
    runReviewBackfill,
    loadDecisionCard,
    runLazyApi,
  } = ctx;

  function init() {
    document.getElementById("calibrationRefreshBtn")?.addEventListener("click", () => void refreshCalibration());
    document.getElementById("shadowScoreboardRefreshBtn")?.addEventListener("click", () => void refreshShadowScoreboard());
    document.getElementById("reviewLoopRefreshBtn")?.addEventListener("click", () => void refreshReviewLoop());
    document.getElementById("reviewBackfillBtn")?.addEventListener("click", () => void runReviewBackfill());
    bindEvent("decisionBtn", "click", loadDecisionCard);
  }

  function prime() {
    void runLazyApi("calibration");
    void runLazyApi("shadowScoreboard");
    void runLazyApi("reviewLoop");
  }

  return { id: "diagnostics", init, prime };
}
