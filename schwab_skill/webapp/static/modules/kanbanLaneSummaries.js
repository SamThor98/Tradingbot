import { safeText, safeNum, formatCount } from "./format.js";
import { state } from "./state.js";
import { isScanSignalStageable } from "./signalProvenance.js";

function setLaneSummary(id, text, laneState = "neutral") {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = safeText(text);
  el.dataset.state = laneState;
}

/** One-line lane headlines under kanban lane headers (Operations workflow). */
export function updateKanbanLaneSummaries(options = {}) {
  const selectedTicker = safeText(options.selectedTicker ?? state.selectedScanTicker ?? "").toUpperCase();
  const diag = state.lastScanDiagnostics || {};
  const dq = safeText(diag.data_quality || "ok").toLowerCase();
  const scanCount = Array.isArray(state.latestSignals) ? state.latestSignals.length : 0;
  const screened =
    safeNum(diag.watchlist_size, 0) ||
    (Array.isArray(state.latestShortlistSignals) ? state.latestShortlistSignals.length : scanCount);
  const hasScan = Boolean(state.lastScanAt);
  const pendingCount = state.lastPendingCount;
  const pendingKnown = Number.isFinite(pendingCount);
  const scanBlocked = safeNum(diag.scan_blocked, 0) > 0;

  if (!hasScan) {
    setLaneSummary("scanLaneSummary", "Step 1 — run scan to load the shortlist.", "empty");
  } else if (scanBlocked || ["failed", "stale", "conflict", "blocked"].includes(dq)) {
    setLaneSummary(
      "scanLaneSummary",
      `Step 1 — scan blocked or data ${dq}; fix before staging.`,
      "error",
    );
  } else {
    setLaneSummary(
      "scanLaneSummary",
      `Step 1 — ${scanCount} kept / ${screened} screened · data ${dq}.`,
      ["degraded", "partial", "unknown", "warning", "warn"].includes(dq) ? "partial" : "success",
    );
  }

  if (!selectedTicker) {
    setLaneSummary("scanDetailLaneSummary", "Step 2 — select a row for chart + decision brief.", "empty");
  } else {
    const sig =
      (state.latestShortlistSignals || []).find(
        (row) => safeText(row?.ticker || row?.symbol || "").toUpperCase() === selectedTicker,
      ) ||
      (state.latestSignals || []).find(
        (row) => safeText(row?.ticker || row?.symbol || "").toUpperCase() === selectedTicker,
      ) ||
      {};
    const stageable = isScanSignalStageable(sig);
    setLaneSummary(
      "scanDetailLaneSummary",
      stageable
        ? `Step 2 — reviewing ${selectedTicker}; trust chips look OK to queue.`
        : `Step 2 — ${selectedTicker} is filtered; read blockers before queuing.`,
      stageable ? "success" : "partial",
    );
  }

  if (options.pendingState === "loading") {
    setLaneSummary("pendingLaneSummary", "Step 3 — loading approval queue…", "loading");
  } else if (options.pendingState === "error") {
    setLaneSummary(
      "pendingLaneSummary",
      safeText(options.pendingError || "Step 3 — queue unavailable; retry refresh."),
      "error",
    );
  } else if (!pendingKnown) {
    setLaneSummary("pendingLaneSummary", "Step 3 — queue count not loaded yet.", "loading");
  } else if (pendingCount === 0) {
    setLaneSummary("pendingLaneSummary", "Step 3 — nothing staged; queue a tradeable setup.", "empty");
  } else {
    setLaneSummary(
      "pendingLaneSummary",
      `Step 3 — ${formatCount(pendingCount)} staged · approve or reject each.`,
      "partial",
    );
  }
}
