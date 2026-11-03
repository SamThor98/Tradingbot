/* Operations screen controller.
 *
 * Owns one-time wiring (init) and screen-prime data loading (prime) for the
 * Operations screen: scan workflow, pending queue, and the approve dialog.
 * All dependencies are injected via ctx from app.js so behavior is identical
 * to the previous inline wireEvents/maybePrimeScreenData code.
 */

import { isScanSignalStageable } from "../modules/signalProvenance.js";
import { bindSettledLessons, refreshSettledLessons } from "../panels/settledLessons.js";

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
    bindScanStudio,
    loadScanCatalog,
    closeQueueScanDialog,
    confirmQueueScanDialog,
    submitManualPendingTrade,
    normalizeScanSignal,
    openQueueScanDialog,
    getScanDetailSignal,
    approveTradeById,
    syncApproveDialogGuardrails,
    openResearchForTicker,
    openIntelForTicker,
  } = ctx;

  function init() {
    bindScanStudio?.();
    void loadScanCatalog?.();
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
    document.getElementById("scanDetailIntelBtn")?.addEventListener("click", () => {
      const sig = getScanDetailSignal();
      const ticker = safeText(sig?.ticker || sig?.symbol || "");
      if (!ticker) return;
      openIntelForTicker?.(ticker);
    });
    document.getElementById("queueScanDialog")?.addEventListener("click", (e) => {
      if (e.target?.id === "queueScanDialog") closeQueueScanDialog();
    });
    bindEvent("scanBtn", "click", runScan);
    document.getElementById("todaySummaryScanBtn")?.addEventListener("click", runScan);
    bindTodayDecisionDesk();
    bindSettledLessons();
    document.getElementById("scanModeSelect")?.addEventListener("change", () => {
      state.scanRowsExpanded = false;
      updateScanModeHelperText();
      const rows = state.latestShortlistSignals?.length ? state.latestShortlistSignals : state.latestSignals;
      renderScanRows(Array.isArray(rows) ? rows : []);
    });
    bindScanSortHandlers();
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
      // Re-enable immediately: a failed clear (or a failed follow-up refresh)
      // must never leave the control permanently locked. A successful board
      // refresh re-syncs disabled state from the pending count anyway.
      btn.disabled = false;
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

  function pendingSection() {
    return document.getElementById("pendingSection");
  }

  function pendingQueueIsOpen() {
    const pending = pendingSection();
    if (!pending) return false;
    if (pending.classList.contains("is-open")) return true;
    if (pending.classList.contains("is-collapsed")) return false;
    if (window.location.hash === "#pendingSection") return true;
    return pending.dataset.workflowFocus === "true";
  }

  function setPendingQueueOpen(open) {
    const pending = pendingSection();
    const toggle = document.getElementById("pendingLaneToggle");
    if (!pending) return;
    pending.classList.toggle("is-open", open);
    pending.classList.toggle("is-collapsed", !open);
    toggle?.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      pending.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function syncPendingQueueAria() {
    document.getElementById("pendingLaneToggle")?.setAttribute(
      "aria-expanded",
      pendingQueueIsOpen() ? "true" : "false",
    );
  }

  function togglePendingQueue() {
    const open = !pendingQueueIsOpen();
    if (!open && window.location.hash === "#pendingSection") {
      const next = `${window.location.pathname}${window.location.search}`;
      window.history.replaceState(null, "", next);
    }
    setPendingQueueOpen(open);
  }

  function bindActivate(el, fn) {
    if (!el) return;
    el.addEventListener("click", fn);
    el.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      fn();
    });
  }

  function bindTodayDecisionDesk() {
    if (document.body.dataset.todayDeskBound === "true") return;
    document.body.dataset.todayDeskBound = "true";
    ["workflowStepScan", "workflowStepEvaluate", "workflowStepApprove"].forEach((id) => {
      const step = document.getElementById(id);
      if (!step) return;
      step.tabIndex = 0;
      step.setAttribute("role", "button");
    });
    bindActivate(document.getElementById("pendingLaneToggle"), togglePendingQueue);
    bindActivate(document.getElementById("todayPendingKpi"), () => setPendingQueueOpen(true));
    bindActivate(document.getElementById("workflowStepApprove"), () => setPendingQueueOpen(true));
    bindActivate(document.getElementById("workflowStepScan"), () => {
      document.getElementById("scanSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    bindActivate(document.getElementById("workflowStepEvaluate"), () => {
      const detail = document.getElementById("scanDetailPanel");
      const target = detail && detail.dataset.state !== "empty"
        ? detail
        : document.getElementById("scanSection");
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    document.addEventListener("click", (event) => {
      const link = event.target?.closest?.('a[href="#pendingSection"]');
      if (!link) return;
      setPendingQueueOpen(true);
    });
    window.addEventListener("hashchange", () => {
      if (window.location.hash === "#pendingSection") setPendingQueueOpen(true);
      else syncPendingQueueAria();
    });
    const pending = pendingSection();
    if (pending) {
      new MutationObserver(syncPendingQueueAria).observe(pending, {
        attributes: true,
        attributeFilter: ["class", "data-workflow-focus"],
      });
    }
    if (window.location.hash === "#pendingSection") setPendingQueueOpen(true);
    else syncPendingQueueAria();
  }

  function prime() {
    void ctx.refreshScanDeltas?.();
    void ctx.refreshShadowWorkbenchTeaser?.();
    void refreshSettledLessons();
  }

  return { id: "operations", init, prime };
}
