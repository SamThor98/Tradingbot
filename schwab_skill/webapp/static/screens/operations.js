/* Operations screen controller.
 *
 * Owns one-time wiring (init) and screen-prime data loading (prime) for the
 * Operations screen: scan workflow, pending queue, and the approve dialog.
 * All dependencies are injected via ctx from app.js so behavior is identical
 * to the previous inline wireEvents/maybePrimeScreenData code.
 */

import { isScanSignalStageable } from "../modules/signalProvenance.js";

export function createOperationsController(ctx) {
  const {
    bindEvent,
    state,
    api,
    safeText,
    logEvent,
    updateActionCenter,
    runScan,
    refreshPending,
    updateScanModeHelperText,
    renderScanRows,
    bindScanSortHandlers,
    fillScanOptionsFromLatestBacktest,
    closeQueueScanDialog,
    confirmQueueScanDialog,
    submitManualPendingTrade,
    normalizeScanSignal,
    openQueueScanDialog,
    getScanDetailSignal,
    approveTradeById,
    syncApproveDialogGuardrails,
    openResearchForTicker,
  } = ctx;

  function init() {
    document.getElementById("queueScanCancelBtn")?.addEventListener("click", closeQueueScanDialog);
    document.getElementById("queueScanConfirmBtn")?.addEventListener("click", () => void confirmQueueScanDialog());
    document.getElementById("manualPendingBtn")?.addEventListener("click", () => void submitManualPendingTrade());
    document.getElementById("scanDetailStageBtn")?.addEventListener("click", () => {
      const sig = getScanDetailSignal();
      if (!sig) return;
      const normalized = normalizeScanSignal(sig);
      if (!isScanSignalStageable(normalized)) return;
      openQueueScanDialog(normalized);
    });
    document.getElementById("scanDetailResearchBtn")?.addEventListener("click", () => {
      const sig = getScanDetailSignal();
      const ticker = safeText(sig?.ticker || sig?.symbol || "");
      if (!ticker) return;
      openResearchForTicker(ticker);
    });
    document.getElementById("queueScanDialog")?.addEventListener("click", (e) => {
      if (e.target?.id === "queueScanDialog") closeQueueScanDialog();
    });
    bindEvent("scanBtn", "click", runScan);
    document.getElementById("todaySummaryScanBtn")?.addEventListener("click", runScan);
    document.getElementById("scanModeSelect")?.addEventListener("change", () => {
      state.scanRowsExpanded = false;
      updateScanModeHelperText();
      const rows = state.latestShortlistSignals?.length ? state.latestShortlistSignals : state.latestSignals;
      renderScanRows(Array.isArray(rows) ? rows : []);
    });
    bindScanSortHandlers();
    document.getElementById("scanApplyBacktestSpecBtn")?.addEventListener("click", () => void fillScanOptionsFromLatestBacktest());
    document.getElementById("scanClearOptionsBtn")?.addEventListener("click", () => {
      const ta = document.getElementById("scanOptionsJson");
      if (ta) ta.value = "";
      state.scanRunOptions = null;
    });
    bindEvent("pendingFilter", "change", refreshPending);
    bindEvent("pendingSort", "change", refreshPending);
    document.getElementById("clearPendingBtn")?.addEventListener("click", async () => {
      const btn = document.getElementById("clearPendingBtn");
      if (!btn || btn.disabled) return;
      if (
        !confirm(
          "Reject all pending trades? They will move to rejected status and disappear from the pending queue.",
        )
      ) {
        return;
      }
      btn.disabled = true;
      const out = await api.post("/api/pending-trades/clear-pending", {});
      if (!out.ok) {
        logEvent({ kind: "trade", severity: "error", message: `Clear pending failed: ${out.error}` });
        updateActionCenter({ title: "Clear pending failed", message: out.error, severity: "error" });
        await refreshPending();
        return;
      }
      const n = typeof out.data?.cleared === "number" ? out.data.cleared : 0;
      logEvent({ kind: "trade", severity: "info", message: `Cleared ${n} pending trade(s).` });
      updateActionCenter({
        title: n ? "Pending queue cleared" : "Nothing to clear",
        message: n ? `Rejected ${n} pending trade(s).` : "There were no pending trades.",
        severity: n ? "warn" : "info",
      });
      await refreshPending();
    });

    document.getElementById("deleteAllTradesBtn")?.addEventListener("click", async () => {
      if (!confirm("Permanently delete ALL trades from history? This cannot be undone.")) return;
      const btn = document.getElementById("deleteAllTradesBtn");
      if (btn) btn.disabled = true;
      const out = await api.post("/api/pending-trades/delete-all", {});
      if (!out.ok) {
        logEvent({ kind: "trade", severity: "error", message: `Delete all failed: ${out.error}` });
        updateActionCenter({ title: "Delete failed", message: out.error, severity: "error" });
      } else {
        const n = typeof out.data?.deleted === "number" ? out.data.deleted : 0;
        logEvent({ kind: "trade", severity: "info", message: `Deleted ${n} trade(s) from history.` });
        updateActionCenter({ title: "History cleared", message: `Permanently deleted ${n} trade(s).`, severity: "success" });
      }
      if (btn) btn.disabled = false;
      await refreshPending();
    });

    const dialog = document.getElementById("approveDialog");
    bindEvent("confirmApproveBtn", "click", async (e) => {
      e.preventDefault();
      const id = state.approvingTradeId;
      if (!id) {
        dialog?.close();
        return;
      }
      const confirmBtn = document.getElementById("confirmApproveBtn");
      if (confirmBtn) confirmBtn.disabled = true;
      const approved = await approveTradeById(id);
      syncApproveDialogGuardrails();
      if (!approved && confirmBtn) confirmBtn.disabled = false;
      if (approved) {
        state.approvingTradeId = null;
        state.approvingExpectedTicker = "";
        dialog?.close();
      }
    });
    bindEvent("cancelApproveBtn", "click", () => {
      state.approvingTradeId = null;
      state.approvingExpectedTicker = "";
      dialog?.close();
    });
    dialog?.addEventListener("close", () => {
      state.approvingTradeId = null;
      state.approvingExpectedTicker = "";
      const riskAck = document.getElementById("approveRiskAck");
      if (riskAck) riskAck.checked = false;
      syncApproveDialogGuardrails();
    });
    document.getElementById("approveTickerInput")?.addEventListener("input", syncApproveDialogGuardrails);
    document.getElementById("approveRiskAck")?.addEventListener("change", syncApproveDialogGuardrails);
  }

  function prime() {
    void ctx.refreshScanDeltas?.();
  }

  return { id: "operations", init, prime };
}
