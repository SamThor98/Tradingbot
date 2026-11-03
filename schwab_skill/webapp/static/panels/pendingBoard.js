/**
 * Pending-trade blotter — fetches /api/pending-trades and renders one
 * dense row per trade: side, quantity, limit, stop, R, book heat, sector,
 * whether the book holds that sector, and status. The note sits under the row.
 * Approve stays disabled unless status is "clear to stage".
 *
 * Extracted from app.js per the module decomposition policy in
 * docs/FRONTEND_DESIGN_SYSTEM.md ("Next Planned Splits"). DOM ids consumed:
 * #pendingFilter, #pendingSort, #pendingBoard, #pendingCount,
 * #clearPendingBtn, #pendingSummaryStrip, #pendingSummaryText.
 *
 * `deps` (injected by app.js, same DI pattern as other panels):
 *   openApproveDialog(row), updateHeroInfographic(),
 *   trackFunnelMilestoneOnce(eventName, props), FUNNEL_EVENTS.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import {
  safeText,
  escapeHtml,
  formatCount,
  formatMoney,
} from "../modules/format.js";
import { setAsyncState, busyButton, ASYNC_ERROR } from "../modules/asyncState.js";
import { applyFreshness, markUnavailable, clearUnavailable, pendingAsOfFrom } from "../modules/freshness.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";
import {
  isPriorityFeedActive,
  pushPriorityItem,
  removePriorityItem,
} from "../modules/priorityFeed.js";
import { openTradeDrawerForTrade } from "./tradeDrawer.js";
import { setOperationsStatusStrip } from "../modules/operationsStatus.js";
import { updateKanbanLaneSummaries } from "../modules/kanbanLaneSummaries.js";
import { renderSignalTrustRow } from "../modules/signalTrustRow.js";
import {
  averageSignalMetric,
  renderOperationsPanelSnapshot,
} from "../modules/operationsPanelSnapshot.js";
import { syncPendingSectionState } from "../modules/operationsPanelState.js";
import {
  isScanSignalStageable,
} from "../modules/signalProvenance.js";
import {
  getReliabilityScore,
  getExecutionScore,
} from "../modules/signalScores.js";
import {
  buildTicketModel,
  rowStageBlockers,
  systemStageBlockers,
} from "../modules/ticketFacts.js";

function pendingNote(row) {
  const raw = row?.note ?? row?.notes;
  const note = raw === null || raw === undefined ? "" : String(raw).trim();
  return note;
}

function bookEquity() {
  const portfolio = state.lastPortfolioData;
  const risk = state.lastPortfolioRiskData;
  const candidates = [portfolio?.total_market_value, portfolio?.total_value, risk?.total_value];
  for (const value of candidates) {
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return null;
}

function bookPositions() {
  const risk = state.lastPortfolioRiskData;
  if (Array.isArray(risk?.positions_weighted) && risk.positions_weighted.length) {
    return risk.positions_weighted;
  }
  const dash = state.lastPortfolioRiskDashboard;
  if (Array.isArray(dash?.positions_weighted) && dash.positions_weighted.length) {
    return dash.positions_weighted;
  }
  const portfolio = state.lastPortfolioData;
  if (Array.isArray(portfolio?.positions)) return portfolio.positions;
  return [];
}

function stageSnapshot() {
  return {
    killSwitch: Boolean(state.publicConfig?.platform_live_trading_kill_switch),
    tradingHalted: Boolean(state.accountMe?.trading_halted),
    liveOff: Boolean(
      state.publicConfig?.saas_mode &&
        (!state.accountMe || !state.accountMe.live_execution_enabled),
    ),
  };
}

function moneyOrDash(value) {
  return value === null || value === undefined ? "—" : formatMoney(value);
}

function summarizePendingQueueHealth(rows = []) {
  const pendingRows = rows.filter((row) => (row.status || "").toLowerCase() === "pending");
  let filteredStaged = 0;
  let degradedStaged = 0;
  pendingRows.forEach((row) => {
    const sig = row?.signal || {};
    if (Object.keys(sig).length && !isScanSignalStageable(sig)) filteredStaged += 1;
    const dq = safeText(sig._data_quality || sig.data_quality || "").toLowerCase();
    if (dq && dq !== "ok") degradedStaged += 1;
  });
  return { filteredStaged, degradedStaged };
}

function pendingDataTone(degradedStaged, statusState) {
  if (statusState === "error") return "bad";
  if (degradedStaged > 0) return "warn";
  return "success";
}

function paintPendingSnapshot(statusState, opts = {}) {
  const {
    pendingN = 0,
    blockers = 0,
    degradedStaged = 0,
    dataLabel = "ok",
    rows = [],
    title = "",
    detail = "",
    hint = "Queue path: scan stage → approval → execution",
  } = opts;
  syncPendingSectionState(statusState);
  const avgReliability = averageSignalMetric(rows, (sig) => getReliabilityScore(sig));
  const avgExecution = averageSignalMetric(rows, (sig) => getExecutionScore(sig));
  renderOperationsPanelSnapshot("pendingSnapshot", "pendingSection", statusState, {
    hint,
    kpis: [
      {
        label: "QUEUE",
        sub: "pending approvals",
        value: pendingN,
        tone: statusState === "empty" ? "neutral" : statusState === "partial" ? "warn" : "success",
      },
      {
        label: "BLOCKERS",
        sub: "filtered or high-risk",
        value: blockers,
        tone: blockers > 0 ? "warn" : "success",
      },
      {
        label: "DATA",
        sub: "quality state",
        value: dataLabel,
        tone: pendingDataTone(degradedStaged, statusState),
      },
    ],
    meters: {
      reliability: avgReliability,
      execution: avgExecution,
    },
    lines: [title, detail].filter(Boolean),
  });
}

export async function refreshPendingBoard(deps = {}) {
  const {
    openApproveDialog,
    updateHeroInfographic,
    trackFunnelMilestoneOnce,
    FUNNEL_EVENTS,
  } = deps;
  const filter = document.getElementById("pendingFilter")?.value || state.pendingFilter;
  const sort = document.getElementById("pendingSort")?.value || state.pendingSort;
  state.pendingFilter = filter;
  state.pendingSort = sort;
  const board = document.getElementById("pendingBoard");
  if (board) {
    board.dataset.state = "loading";
    // Skeleton cards approximate the task-card silhouette so the board
    // doesn't jump when real rows land (perceived-perf pass).
    board.innerHTML = `
      <div role="status" aria-label="Loading pending trades">
        <span class="ol-skeleton" style="min-height:3.2rem"></span>
        <span class="ol-skeleton" style="min-height:3.2rem; width:92%"></span>
        <span class="ol-skeleton" style="min-height:3.2rem; width:85%"></span>
      </div>`;
  }
  updateKanbanLaneSummaries({ pendingState: "loading" });
  setOperationsStatusStrip(
    "pendingStatusStrip",
    "loading",
    "Pending queue loading.",
    "Counts remain unavailable until /api/pending-trades answers.",
  );
  paintPendingSnapshot("loading", {
    title: "Pending queue loading.",
    detail: "Counts remain unavailable until /api/pending-trades answers.",
  });
  const query = new URLSearchParams({ status: filter, sort });
  const pendingOnlyQuery = new URLSearchParams({ status: "pending", sort });
  const [out, pendingOnlyOut] = await Promise.all([
    api.get(`/api/pending-trades?${query.toString()}`),
    api.get(`/api/pending-trades?${pendingOnlyQuery.toString()}`),
  ]);
  if (!out.ok) {
    const msg = out.user_message || out.error;
    logEvent({ kind: "trade", severity: "error", message: `Pending trades load failed: ${out.error}` });
    if (board) {
      setAsyncState(board, ASYNC_ERROR, {
        message: `Pending trades unavailable: ${safeText(msg)}`,
        onRetry: () => void refreshPendingBoard(deps),
      });
    }
    // Honest "unavailable" for the count badge — never silently render 0.
    const pcEl = document.getElementById("pendingCount");
    if (pcEl) markUnavailable(pcEl, msg || "fetch failed");
    state.lastPendingCount = null;
    state.lastPendingError = safeText(msg || "Pending trades did not load.");
    setOperationsStatusStrip(
      "pendingStatusStrip",
      "error",
      "Pending queue unavailable.",
      safeText(msg || "Fetch failed. Retry keeps the same filter and sort."),
    );
    paintPendingSnapshot("error", {
      dataLabel: "Unavailable",
      title: "Pending queue unavailable.",
      detail: safeText(msg || "Fetch failed. Retry keeps the same filter and sort."),
    });
    updateKanbanLaneSummaries({ pendingState: "error", pendingError: safeText(msg || "Queue unavailable.") });
    if (typeof updateHeroInfographic === "function") updateHeroInfographic();
    updateActionCenter({ title: "Pending queue unavailable", message: msg, severity: "error" });
    return;
  }
  const rows = out.data || [];
  let pendingN =
    pendingOnlyOut.ok && Array.isArray(pendingOnlyOut.data)
      ? pendingOnlyOut.data.length
      : rows.filter((r) => r.status === "pending").length;
  const pcEl = document.getElementById("pendingCount");
  if (pcEl) {
    clearUnavailable(pcEl);
    pcEl.textContent = formatCount(pendingN);
  }
  state.lastPendingCount = pendingN;
  state.lastPendingError = "";
  state.lastPendingAt = pendingAsOfFrom(rows);
  applyFreshness(document.getElementById("pendingFresh"), {
    asOf: state.lastPendingAt,
    source: "/api/pending-trades",
    surface: "pending_queue",
    unavailable: pendingN > 0 ? "pending time missing" : "no pending trades",
  });
  if (pendingN > 0 && typeof trackFunnelMilestoneOnce === "function" && FUNNEL_EVENTS) {
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_PENDING_TRADE, {
      source: "pending_queue_refresh",
      pending_count: pendingN,
    });
  }
  const clearBtn = document.getElementById("clearPendingBtn");
  if (clearBtn) clearBtn.disabled = pendingN === 0;
  const deleteBtn = document.getElementById("deleteAllTradesBtn");
  if (deleteBtn) {
    const nothingHere = pendingN === 0 && rows.length === 0;
    deleteBtn.disabled = nothingHere;
    deleteBtn.title = nothingHere
      ? "No trades in this view to delete."
      : "Permanently delete every trade row. This cannot be undone.";
  }
  if (typeof updateHeroInfographic === "function") updateHeroInfographic();

  board.innerHTML = "";
  if (!rows.length) {
    board.dataset.state = "empty";
    board.innerHTML = pendingN === 0
      ? `<div class="task-empty muted">Nothing staged. Queue a clear tradeable name from the scan table.</div>`
      : `<div class="task-empty muted">No trades match this filter.</div>`;
    setOperationsStatusStrip(
      "pendingStatusStrip",
      "empty",
      pendingN === 0 ? "No staged trades." : "No trades match this filter.",
      pendingN === 0
        ? "Stage from Scan results or add a manual trade when needed."
        : `${pendingN} pending trade(s) exist outside the current filter.`,
    );
    paintPendingSnapshot("empty", {
      pendingN,
      blockers: 0,
      degradedStaged: 0,
      dataLabel: pendingN === 0 ? "Clear" : "Filtered",
      title: pendingN === 0 ? "No staged trades." : "No trades match this filter.",
      detail:
        pendingN === 0
          ? "Stage from Scan results or add a manual trade when needed."
          : `${pendingN} pending trade(s) exist outside the current filter.`,
    });
    updateKanbanLaneSummaries({ pendingState: "empty" });
    return;
  }

  const equity = bookEquity();
  const positions = bookPositions();
  const snapshot = stageSnapshot();
  const rendered = rows.map((row) => {
    const model = buildTicketModel({
      row,
      equity,
      positions,
      blockers: rowStageBlockers(row),
      systemBlockers: systemStageBlockers(snapshot),
    });
    const pending = String(row.status || "").toLowerCase() === "pending";
    return {
      row,
      model,
      clear: pending && model.clear,
      status: pending ? model.status : String(row.status || ""),
    };
  });
  const blockedN = rendered.filter((item) => {
    return String(item.row.status || "").toLowerCase() === "pending" && !item.clear;
  }).length;
  const { filteredStaged, degradedStaged } = summarizePendingQueueHealth(rows);
  const needsReview = blockedN > 0 || filteredStaged > 0 || degradedStaged > 0;
  const statusState = needsReview ? "partial" : "success";
  const detailParts = [];
  if (blockedN > 0) detailParts.push(`${blockedN} blocked`);
  if (filteredStaged > 0) {
    detailParts.push(
      `${filteredStaged} filtered at scan time (approve blocked until re-staged)`,
    );
  }
  if (degradedStaged > 0) detailParts.push(`${degradedStaged} with degraded data quality`);
  setOperationsStatusStrip(
    "pendingStatusStrip",
    statusState,
    `${pendingN} pending approval${pendingN === 1 ? "" : "s"}.`,
    detailParts.length ? detailParts.join("; ") + "." : "R, book heat, and data quality are on each row.",
  );
  const dataLabel =
    degradedStaged > 0 ? `${degradedStaged} degraded` : rows.length ? "ok" : "Clear";
  paintPendingSnapshot(statusState, {
    pendingN,
    blockers: blockedN,
    degradedStaged,
    dataLabel,
    rows: rows.map((row) => row.signal || {}),
    title: `${pendingN} pending approval${pendingN === 1 ? "" : "s"}.`,
    detail: detailParts.length
      ? detailParts.join("; ") + "."
      : "R, book heat, and data quality are on each row.",
  });
  board.dataset.state = statusState;

  const body = rendered.map(({ row, model, clear, status }) => {
    const sig = row.signal || {};
    const note = pendingNote(row);
    const id = escapeHtml(row.id);
    const pending = String(row.status || "").toLowerCase() === "pending";
    return `
      <tr class="pending-blotter-row" data-tone="${clear ? "ok" : "bad"}" data-stage="${clear ? "clear" : "blocked"}" data-quality="${escapeHtml(model.dataQuality || "")}" data-trade="${id}">
        <td class="pending-blotter-ticker">${escapeHtml(safeText(row.ticker))}</td>
        <td>${escapeHtml(model.side)}</td>
        <td class="mono-nums">${model.qty === null ? "—" : escapeHtml(String(model.qty))}</td>
        <td class="mono-nums">${escapeHtml(moneyOrDash(model.limit))}</td>
        <td class="mono-nums">${escapeHtml(moneyOrDash(model.stop))}</td>
        <td class="mono-nums">${escapeHtml(moneyOrDash(model.r))}</td>
        <td class="mono-nums pending-blotter-heat">${escapeHtml(model.heat)}</td>
        <td>${escapeHtml(model.sector || "—")}</td>
        <td>${escapeHtml(model.book || "—")}</td>
        <td class="pending-blotter-status">${escapeHtml(status)}</td>
        <td class="pending-blotter-actions">
          <button class="btn small secondary" type="button" data-quick="${id}">Quick View</button>
          <button class="btn small approve-btn" type="button" data-approve="${id}" data-stage="${clear ? "clear" : "blocked"}" title="${escapeHtml(status)}" ${clear ? "" : "disabled"}>Approve</button>
          <button class="btn small reject-btn" type="button" data-reject="${id}" ${pending ? "" : "disabled"}>Reject</button>
          <button class="btn small bad" type="button" data-delete="${id}" title="Permanently delete this trade">Delete</button>
        </td>
      </tr>
      <tr class="pending-blotter-note">
        <td colspan="11">
          <div class="pending-blotter-note-line">${note ? escapeHtml(note) : '<span class="muted">—</span>'}</div>
          ${renderSignalTrustRow(sig)}
        </td>
      </tr>`;
  }).join("");
  board.innerHTML = `
    <table class="pending-blotter">
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Limit</th>
          <th>Stop</th>
          <th>R</th>
          <th>Heat</th>
          <th>Sector</th>
          <th>Book</th>
          <th>Status</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>${body}</tbody>
    </table>`;

  board.querySelectorAll("button[data-quick]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const id = e.currentTarget.getAttribute("data-quick");
      const row = rows.find((r) => r.id === id);
      if (row) await openTradeDrawerForTrade(row);
    });
  });

  board.querySelectorAll("button[data-approve]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const id = e.currentTarget.getAttribute("data-approve");
      const row = rows.find((r) => r.id === id);
      if (typeof openApproveDialog === "function") openApproveDialog(row);
    });
  });

  board.querySelectorAll("button[data-reject]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const clicked = e.currentTarget;
      const release = busyButton(clicked, "Rejecting…");
      const id = clicked.getAttribute("data-reject");
      try {
        const out = await api.post(`/api/trades/${id}/reject`, {});
        if (!out.ok) {
          logEvent({ kind: "trade", severity: "error", message: `Reject ${id} failed: ${out.error}` });
          updateActionCenter({ title: "Trade Reject Failed", message: out.user_message || out.error, severity: "error" });
        } else {
          logEvent({ kind: "trade", severity: "info", message: `Rejected ${id}.` });
          updateActionCenter({ title: "Trade Rejected", message: `Trade ${id} was rejected.`, severity: "warn" });
        }
        await refreshPendingBoard(deps);
      } finally {
        release();
      }
    });
  });

  board.querySelectorAll("button[data-delete]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const clicked = e.currentTarget;
      const release = busyButton(clicked, "Deleting…");
      const id = clicked.getAttribute("data-delete");
      try {
        const out = await api.post(`/api/trades/${id}/delete`, {});
        if (!out.ok) {
          logEvent({ kind: "trade", severity: "error", message: `Delete ${id} failed: ${out.error}` });
          updateActionCenter({ title: "Trade Delete Failed", message: out.user_message || out.error, severity: "error" });
        } else {
          logEvent({ kind: "trade", severity: "info", message: `Deleted ${id}.` });
        }
        await refreshPendingBoard(deps);
      } finally {
        release();
      }
    });
  });

  const strip = document.getElementById("pendingSummaryStrip");
  const stripText = document.getElementById("pendingSummaryText");
  // Pending count lives on the Today summary landing; keep the legacy strip hidden.
  if (strip) strip.classList.add("hidden");
  if (isPriorityFeedActive()) {
    if (pendingN > 0) {
      pushPriorityItem({
        key: "pending_decision",
        title: "Pending trades need a decision",
        message: `${pendingN} staged trade(s) are waiting for approval or rejection.`,
        severity: "warn",
        href: "#pendingSection",
        hrefLabel: "Review pending",
      });
    } else {
      removePriorityItem("pending_decision");
    }
  } else if (strip && stripText) {
    strip.classList.add("hidden");
  }
  if (typeof deps.updateTodaySummaryLanding === "function") {
    deps.updateTodaySummaryLanding();
  }
  updateKanbanLaneSummaries({ pendingState: "success" });
}
