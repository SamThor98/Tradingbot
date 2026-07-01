/**
 * Pending-trade board rendering — fetches /api/pending-trades, groups rows
 * by sector, renders the task cards with score/reliability/conviction
 * meters, and wires the quick-view/approve/reject/delete actions.
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
  safeNum,
  escapeHtml,
  clampPct,
  pct,
  formatCount,
  formatMoney,
} from "../modules/format.js";
import { setAsyncState, busyButton, ASYNC_ERROR } from "../modules/asyncState.js";
import { markUnavailable, clearUnavailable } from "../modules/freshness.js";
import { logEvent, updateActionCenter, statusClass } from "../modules/logger.js";
import {
  isPriorityFeedActive,
  pushPriorityItem,
  removePriorityItem,
} from "../modules/priorityFeed.js";
import { openTradeDrawerForTrade } from "./tradeDrawer.js";
import {
  getCompositeScore,
  getReliabilityScore,
  getConvictionScore,
  getEdgeScore,
  getExecutionScore,
  getCalibratedPUp,
  formatConfidenceLabel,
} from "../modules/signalScores.js";

function getSectorKeyFromTrade(row) {
  const sector = row?.signal?.sector_etf || "Unknown";
  return String(sector || "Unknown").toUpperCase();
}

function meterFromScore(score) {
  return clampPct(safeNum(score, 0));
}

function meterFromConviction(conviction) {
  return clampPct((safeNum(conviction, 0) + 100) / 2);
}

function meterFromReliability(reliability) {
  return clampPct(safeNum(reliability, 0));
}

function renderPendingContext(row) {
  const sig = row.signal || {};
  const score = getCompositeScore(sig);
  const reliability = getReliabilityScore(sig);
  const edge = getEdgeScore(sig);
  const execution = getExecutionScore(sig);
  const sector = sig.sector_etf;
  const conviction = getConvictionScore(sig);
  const advisory = sig.advisory || {};
  const pUp = getCalibratedPUp(sig);
  const confidence = formatConfidenceLabel(advisory.confidence_bucket ?? sig.confidence_bucket ?? sig.advisory_confidence);
  return `score: ${score !== null ? safeNum(score).toFixed(0) : "—"} (edge ${edge !== null ? safeNum(edge).toFixed(0) : "—"})<br/>
    reliability: ${reliability !== null ? safeNum(reliability).toFixed(0) : "—"} · execution: ${execution !== null ? safeNum(execution).toFixed(0) : "—"}<br/>
    sector: ${safeText(sector || "—")}<br/>
    confidence: ${safeText(confidence || "—")} · P(up 10d): ${pUp === null ? "—" : pct(pUp, 1)}<br/>
    conviction: ${conviction !== null ? safeText(conviction) : "—"}`;
}

function setPendingStatusStrip(stateName, title, detail) {
  const strip = document.getElementById("pendingStatusStrip");
  if (!strip) return;
  strip.dataset.state = stateName;
  const label = stateName.charAt(0).toUpperCase() + stateName.slice(1);
  strip.innerHTML = `
    <span class="operations-status-pill">${escapeHtml(label)}</span>
    <strong>${escapeHtml(title)}</strong>
    <span class="muted">${escapeHtml(detail)}</span>
  `;
}

function getPendingRiskProfile(row) {
  const sig = row?.signal || {};
  const score = safeNum(getCompositeScore(sig), 0);
  const reliability = safeNum(getReliabilityScore(sig), 0);
  const advisory = sig.advisory || {};
  const confidence = formatConfidenceLabel(advisory.confidence_bucket ?? sig.confidence_bucket ?? sig.advisory_confidence);
  const hasSector = Boolean(safeText(sig.sector_etf || "").trim());
  const lowConfidence = ["low", "unknown", "—"].includes(String(confidence || "—").toLowerCase());
  if (!hasSector || score < 60 || lowConfidence || reliability < 45) return { label: "Requires extra review", severity: "high" };
  if (score < 72) return { label: "Moderate confidence", severity: "medium" };
  return { label: "Ready to review", severity: "low" };
}

const PRESET_NOTIONAL_CAP = Object.freeze({
  conservative: 300,
  balanced: 500,
  aggressive: 900,
});

function renderPendingThesis(row) {
  const note = safeText(row?.note || row?.notes || "").trim();
  if (note) return escapeHtml(note.length > 140 ? `${note.slice(0, 137)}…` : note);
  const sig = row?.signal || {};
  const summary = safeText(sig.mirofish_summary || sig.summary || "").trim();
  if (summary) return escapeHtml(summary.length > 140 ? `${summary.slice(0, 137)}…` : summary);
  const strategy = safeText(sig?.strategy_attribution?.top_live || "").trim();
  const score = getCompositeScore(sig);
  const parts = [];
  if (strategy) parts.push(strategy.replace(/_/g, " "));
  if (score !== null) parts.push(`score ${safeNum(score, 0).toFixed(0)}`);
  return parts.length
    ? escapeHtml(parts.join(" · "))
    : `<span class="muted">Review chart context before approving.</span>`;
}

function renderPositionCapMeter(row, getDisplayMode) {
  const sig = row?.signal || {};
  const price = safeNum(sig.price ?? sig.current_price, NaN);
  const qty = safeNum(row?.qty, NaN);
  if (!Number.isFinite(price) || !Number.isFinite(qty) || qty <= 0) return "";
  const mode = typeof getDisplayMode === "function" ? getDisplayMode() : "balanced";
  const cap = PRESET_NOTIONAL_CAP[mode] || PRESET_NOTIONAL_CAP.balanced;
  const notional = price * qty;
  const pct = clampPct((notional / cap) * 100);
  const cls = pct >= 95 ? "bad" : pct >= 80 ? "warn" : "good";
  return `
    <div class="pending-cap-meter">
      <span class="meter-label">Notional vs preset cap ($${cap})</span>
      <div class="meter meter--${cls}"><span style="width:${pct}%"></span></div>
      <span class="muted mono-nums">${formatMoney(notional)} (${pct.toFixed(0)}%)</span>
    </div>
  `;
}

function renderTimeline(row) {
  const status = (row.status || "").toLowerCase();
  if (status === "pending") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> waiting action</span>`;
  if (status === "executed") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> approved -> executed</span>`;
  if (status === "rejected") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> rejected</span>`;
  if (status === "failed") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> approve attempted -> failed</span>`;
  return `<span class="timeline-badge"><span class="timeline-dot"></span>${safeText(status)}</span>`;
}

export async function refreshPendingBoard(deps = {}) {
  const {
    openApproveDialog,
    updateHeroInfographic,
    trackFunnelMilestoneOnce,
    FUNNEL_EVENTS,
    getDisplayMode,
  } = deps;
  const filter = document.getElementById("pendingFilter")?.value || state.pendingFilter;
  const sort = document.getElementById("pendingSort")?.value || state.pendingSort;
  state.pendingFilter = filter;
  state.pendingSort = sort;
  const board = document.getElementById("pendingBoard");
  if (board) {
    board.innerHTML = `<div class="task-empty muted">Loading pending trades...</div>`;
  }
  setPendingStatusStrip(
    "loading",
    "Pending queue loading.",
    "Counts remain unavailable until /api/pending-trades answers.",
  );
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
    setPendingStatusStrip(
      "error",
      "Pending queue unavailable.",
      safeText(msg || "Fetch failed. Retry keeps the same filter and sort."),
    );
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
  state.lastPendingAt = new Date().toISOString();
  if (pendingN > 0 && typeof trackFunnelMilestoneOnce === "function" && FUNNEL_EVENTS) {
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_PENDING_TRADE, {
      source: "pending_queue_refresh",
      pending_count: pendingN,
    });
  }
  const clearBtn = document.getElementById("clearPendingBtn");
  if (clearBtn) clearBtn.disabled = pendingN === 0;
  if (typeof updateHeroInfographic === "function") updateHeroInfographic();

  board.innerHTML = "";
  if (!rows.length) {
    board.innerHTML = `<div class="task-empty muted">No trades match current filter.</div>`;
    setPendingStatusStrip(
      "empty",
      pendingN === 0 ? "No staged trades." : "No trades match this filter.",
      pendingN === 0
        ? "Stage from Scan results or add a manual trade when needed."
        : `${pendingN} pending trade(s) exist outside the current filter.`,
    );
    return;
  }

  const riskCounts = rows.reduce(
    (acc, row) => {
      const risk = getPendingRiskProfile(row);
      acc[risk.severity] = (acc[risk.severity] || 0) + 1;
      return acc;
    },
    { high: 0, medium: 0, low: 0 },
  );
  const highRisk = safeNum(riskCounts.high, 0);
  const mediumRisk = safeNum(riskCounts.medium, 0);
  const statusState = highRisk > 0 ? "partial" : "success";
  setPendingStatusStrip(
    statusState,
    `${pendingN} pending approval${pendingN === 1 ? "" : "s"}.`,
    highRisk > 0
      ? `${highRisk} require extra review; ${mediumRisk} moderate-confidence setup(s).`
      : "Approve/reject actions are visible on each staged trade.",
  );

  const groups = rows.reduce((acc, row) => {
    const key = getSectorKeyFromTrade(row);
    if (!acc[key]) acc[key] = [];
    acc[key].push(row);
    return acc;
  }, {});

  Object.keys(groups).sort().forEach((sector) => {
    const section = document.createElement("section");
    section.className = "task-group";
    section.innerHTML = `<h3>${sector}</h3>`;
    groups[sector].forEach((row) => {
      const composite = getCompositeScore(row?.signal || {});
      const reliabilityValue = getReliabilityScore(row?.signal || {});
      const convictionValue = getConvictionScore(row?.signal || {});
      const score = meterFromScore(composite);
      const reliabilityMeter = meterFromReliability(reliabilityValue);
      const conviction = meterFromConviction(convictionValue);
      const liveBlocked =
        state.publicConfig.saas_mode &&
        (!state.accountMe || !state.accountMe.live_execution_enabled);
      const approveTitle = liveBlocked
        ? "Live trading is off — enable in Strategy Presets after reviewing risk."
        : "";
      const card = document.createElement("article");
      const risk = getPendingRiskProfile(row);
      const sector = safeText(row?.signal?.sector_etf || "").trim();
      const sectorChip = sector
        ? `<span class="sector-chip pill info">${escapeHtml(sector)}</span>`
        : `<span class="sector-chip pill neutral">No sector</span>`;
      card.className = `task-card task-card--risk-${risk.severity}`;
      card.innerHTML = `
        <div class="task-card-head">
          <div>
            <strong>${safeText(row.ticker)}</strong>
            <span class="muted">#${safeText(row.id)} • Qty ${safeText(row.qty)}</span>
            ${sectorChip}
          </div>
          <div class="task-card-badges">
            <span class="risk-chip ${risk.severity}">${safeText(risk.label)}</span>
            <span class="${statusClass(row.status)}">${safeText(row.status)}</span>
          </div>
        </div>
        <p class="pending-thesis-line">${renderPendingThesis(row)}</p>
        <div class="task-meters">
          <div>
            <span class="meter-label">Score ${safeNum(composite, 0).toFixed(0)}</span>
            <div class="meter"><span style="width:${score}%"></span></div>
          </div>
          <div>
            <span class="meter-label">Reliability ${safeNum(reliabilityValue, 0).toFixed(0)}</span>
            <div class="meter info"><span style="width:${reliabilityMeter}%"></span></div>
          </div>
          <div>
            <span class="meter-label">Conviction ${safeNum(convictionValue, 0).toFixed(0)}</span>
            <div class="meter conviction"><span style="width:${conviction}%"></span></div>
          </div>
        </div>
        ${renderPositionCapMeter(row, getDisplayMode)}
        <div class="context-mini">${renderTimeline(row)}<br/>${renderPendingContext(row)}</div>
        <div class="task-actions">
          <button class="btn small secondary" data-quick="${row.id}">Quick View</button>
          <button class="btn small approve-btn" data-approve="${row.id}" title="${escapeHtml(approveTitle)}" ${row.status !== "pending" || liveBlocked ? "disabled" : ""}>Approve</button>
          <button class="btn small reject-btn" data-reject="${row.id}" ${row.status !== "pending" ? "disabled" : ""}>Reject</button>
          <button class="btn small bad" data-delete="${row.id}" title="Permanently delete this trade">Delete</button>
        </div>
      `;
      section.appendChild(card);
    });
    board.appendChild(section);
  });

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
}
