/**
 * Scan results table — qualified breakout rows, near-miss rows, sortable
 * headers, the funnel-filter banner, rank "why" cells, and the rank-explain
 * display mode control.
 *
 * Extracted from app.js per the module decomposition policy in
 * docs/FRONTEND_DESIGN_SYSTEM.md ("Next Planned Splits"). DOM ids consumed:
 * #scanTableBody, #nearMissTableBody, #scanShowMoreBtn, #scanQualifiedMeta,
 * #nearMissSummaryCount, #scanFunnelFilterBanner, #scanSection thead,
 * #rankExplainModeSelect, #rankExplainModeHelperText, #nearMissPanel.
 *
 * app.js injects the cross-panel callbacks once at boot via
 * `configureScanTable(deps)` (same DI pattern as other panels):
 *   renderScanDetail(sig), highlightSelectedScanRow(ticker),
 *   updateHeroInfographic(), renderDiagnostics(diag), runScan(),
 *   openQueueScanDialog(sig), openTradeDrawer(opts).
 */

import { state } from "../modules/state.js";
import {
  safeText,
  escapeHtml,
  formatMoney,
  formatDecimal,
  pct,
  formatStrategyLabel,
} from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";
import {
  optionalNum,
  formatConfidenceLabel,
  getCompositeScore,
  getConvictionScore,
  getCalibratedPUp,
  getRankScore,
  getEdgeScore,
  getReliabilityScore,
  getExecutionScore,
  getEv10d,
} from "../modules/signalScores.js";
import {
  formatScanStatusBadge,
  formatNearMissSummary,
  formatFilterReasons,
  scanStatusSeverityBucket,
} from "../modules/filterReasons.js";
import {
  isScanSignalStageable,
  // Kept imported for the scan transparency contract + future demoted-field reuse.
  renderSignalProvenanceChip,
  renderTradeableVerdict,
} from "../modules/signalProvenance.js";

// Retain symbol references so tree-shakers / contract greps keep these exports wired.
void renderSignalProvenanceChip;
void renderTradeableVerdict;
import {
  filterSignalsByFunnelStage,
  funnelStageFilterHint,
} from "../modules/funnelFilter.js";
import { wireScanRankWhyTooltips } from "../modules/floatTooltip.js";
import { normalizeScanSignal } from "../modules/scanSignals.js";

const QUALIFIED_ROWS_DEFAULT_LIMIT = 20;
const NEAR_MISS_DEFAULT_LIMIT = 10;
const RANK_EXPLAIN_MODE_KEY = "tradingbot.scan.rank_explain_mode";
const TRIAGE_COLSPAN = 6;
const SCAN_STATUS_FILTERS = Object.freeze(["pass", "review", "blocked", "info"]);

/** Cross-panel callbacks injected by app.js at boot (see module docstring). */
let deps = {};

export function configureScanTable(injected = {}) {
  deps = { ...deps, ...injected };
}

/* ── Rank explain mode (tooltip vs inline) ──────────────────────────── */

export function getRankExplainMode() {
  const fromState = safeText(state.scanRankExplainMode || "").toLowerCase();
  if (fromState === "tooltip" || fromState === "inline") return fromState;
  let stored = "";
  try {
    stored = safeText(localStorage.getItem(RANK_EXPLAIN_MODE_KEY) || "").toLowerCase();
  } catch {
    stored = "";
  }
  return stored === "inline" ? "inline" : "tooltip";
}

export function applyRankExplainModeSelection() {
  const mode = getRankExplainMode();
  state.scanRankExplainMode = mode;
  const el = document.getElementById("rankExplainModeSelect");
  if (el && el.value !== mode) el.value = mode;
  updateRankExplainModeHelperText();
}

export function setRankExplainMode(rawMode) {
  const mode = safeText(rawMode || "").toLowerCase() === "inline" ? "inline" : "tooltip";
  state.scanRankExplainMode = mode;
  try {
    localStorage.setItem(RANK_EXPLAIN_MODE_KEY, mode);
  } catch {
    // Ignore storage write failures.
  }
  updateRankExplainModeHelperText();
  const rows = state.latestShortlistSignals?.length ? state.latestShortlistSignals : state.latestSignals;
  renderScanRows(Array.isArray(rows) ? rows : []);
}

function updateRankExplainModeHelperText() {
  const helperEl = document.getElementById("rankExplainModeHelperText");
  if (!helperEl) return;
  const mode = getRankExplainMode();
  if (mode === "inline") {
    helperEl.textContent = "Inline shows rank rationale directly in each score cell (best for deep review).";
  } else {
    helperEl.textContent = "Tooltip keeps rows compact and shows rank rationale on hover.";
  }
}

/* ── Rank / confidence cell rendering ───────────────────────────────── */

function buildRankWhyText(row = {}) {
  const rank = optionalNum(getRankScore(row));
  const basis = safeText(row.rank_basis || "composite_score");
  const comps = row.score_components || {};
  const ptsVol = optionalNum(row.pts_volume_rank ?? comps.pts_volume ?? row.pts_volume);
  const volumeRatio = optionalNum(row.volume_ratio);
  const ptsMiro = optionalNum(comps.pts_mirofish ?? row.pts_mirofish);
  const legacyRank = optionalNum(row.rank_score_v1 ?? row.rank_score);
  const rankV2 = optionalNum(row.rank_score_v2);
  const edge = optionalNum(getEdgeScore(row));
  const reliability = optionalNum(getReliabilityScore(row));
  const execution = optionalNum(getExecutionScore(row));
  const pUp = optionalNum(getCalibratedPUp(row));
  const ev10d = optionalNum(getEv10d(row));
  const composite = optionalNum(getCompositeScore(row));
  const reasons = Array.isArray(row.reliability_reasons) ? row.reliability_reasons : [];
  const capReasons = reasons
    .filter((r) => String(r || "").startsWith("composite_capped"))
    .map((r) => String(r || "").replace(/^composite_capped_/, "").replaceAll("_", " "));
  const segments = [];
  segments.push(`basis ${basis}`);
  if (composite !== null) segments.push(`composite ${composite.toFixed(1)}`);
  if (rank !== null && composite !== null && Math.abs(rank - composite) >= 0.05) {
    segments.push(`sort ${rank.toFixed(1)}`);
  } else if (rank !== null) {
    segments.push(`rank ${rank.toFixed(1)}`);
  }
  if (edge !== null) segments.push(`edge ${edge.toFixed(1)}`);
  if (reliability !== null) segments.push(`reliability ${reliability.toFixed(1)}`);
  if (execution !== null) segments.push(`execution ${execution.toFixed(1)}`);
  if (ptsVol !== null) {
    const ratioText = volumeRatio !== null ? ` (${volumeRatio.toFixed(2)}x)` : "";
    segments.push(`vol pts ${ptsVol.toFixed(1)}${ratioText}`);
  }
  if (ptsMiro !== null && ptsMiro > 0) segments.push(`miro pts ${ptsMiro.toFixed(1)}`);
  if (legacyRank !== null && legacyRank !== rank) segments.push(`v1 ${legacyRank.toFixed(1)}`);
  if (rankV2 !== null) segments.push(`v2 diag ${rankV2.toFixed(1)}`);
  if (pUp !== null) segments.push(`p(up) ${pct(pUp, 1)}`);
  if (ev10d !== null) segments.push(`EV10d ${(ev10d * 100).toFixed(2)}%`);
  if (capReasons.length) segments.push(`caps ${capReasons.join(", ")}`);
  return segments.join(" · ");
}

function buildRankWhyInlineText(row = {}) {
  const rank = optionalNum(getRankScore(row));
  const reliability = optionalNum(getReliabilityScore(row));
  const execution = optionalNum(getExecutionScore(row));
  const pUp = optionalNum(getCalibratedPUp(row));
  const reasons = Array.isArray(row.reliability_reasons) ? row.reliability_reasons : [];
  const hasCap = reasons.some((r) => String(r || "").includes("capped"));
  const segments = [];
  if (rank !== null) segments.push(`rank ${rank.toFixed(1)}`);
  if (reliability !== null) segments.push(`rel ${reliability.toFixed(0)}`);
  if (execution !== null) segments.push(`exec ${execution.toFixed(0)}`);
  if (pUp !== null) segments.push(`P(up) ${pct(pUp, 1)}`);
  if (hasCap) segments.push("cap applied");
  return segments.join(" · ");
}

export function renderRankScoreCell(row = {}, { triage = false } = {}) {
  const rank = getRankScore(row);
  const composite = getCompositeScore(row);
  const shown = rank !== null ? `${rank.toFixed(1)}` : "—";
  const mode = getRankExplainMode();
  const compositeHint =
    composite !== null && rank !== null && Math.abs(composite - rank) >= 0.05
      ? ` · comp ${composite.toFixed(1)}`
      : "";
  const why = buildRankWhyText(row);
  const title = why ? `${why}${compositeHint}` : `Rank ${shown}${compositeHint}`;
  // Triage rows stay single-line: number + tooltip only (no "?" affordance).
  if (triage) {
    return `<span class="scan-rank-score" title="${escapeHtml(title || "Composite quality rank (sort key)")}">${shown}</span>`;
  }
  if (mode === "inline") {
    const inlineWhy = buildRankWhyInlineText(row);
    const tail = inlineWhy ? `<span class="scan-rank-inline">${escapeHtml(inlineWhy)}</span>` : "";
    return `<span class="scan-rank-cell scan-rank-cell--inline"><span class="scan-rank-score" title="Composite quality rank (sort key)">${shown}</span>${tail}</span>`;
  }
  if (!why && !compositeHint) return shown;
  return `<span class="scan-rank-cell"><span class="scan-rank-score" title="Composite quality rank (sort key)">${shown}</span><span class="scan-rank-why" data-rank-tip="${escapeHtml(title)}" tabindex="0" role="button" aria-label="Why this rank">?</span></span>`;
}

function renderConfidenceCell(row, conf) {
  if (conf === "—") return "—";
  const bucket = safeText((row.advisory || {}).confidence_bucket || "").toLowerCase();
  const link =
    bucket && bucket !== "unknown"
      ? ` <a href="/?screen=diagnostics#calibrationSection" class="calibration-link muted" title="View bucket calibration on System tab">↗</a>`
      : "";
  return `${escapeHtml(conf)}${link}`;
}

/* ── Sortable scan table ────────────────────────────────────────────── */
//
// Each header in the scan candidates table carries a `data-sort-key` attribute
// (see `index.html`). Clicking a header toggles the sort direction; clicking a
// different header switches to that field with a sensible default direction
// (descending for numeric/score-like columns, ascending for text/labels).
// The active sort lives on `state.scanSort` and is applied during
// `renderScanRows`, so any subsequent re-render (filter changes, new scan
// payload, etc.) keeps the operator's chosen order until they pick a
// different one.

const SCAN_SORT_DEFAULT_DIRECTION = {
  ticker: "desc",
  status: "desc",
  source: "desc",
  flagged_days: "desc",
  strategy: "desc",
  price: "desc",
  score: "desc",
  p_up_10d: "desc",
  expected_return_40d: "desc",
  confidence: "desc",
  conviction: "desc",
  sector: "desc",
  reason: "desc",
  actions: "desc",
};

// Confidence is a label, not a number — give each bucket a numeric rank so
// "HIGH" sorts above "MEDIUM" above "LOW" regardless of the input casing.
// Unknown buckets sort to the bottom.
const CONFIDENCE_RANK = {
  HIGH: 3,
  MEDIUM: 2,
  MED: 2,
  LOW: 1,
};

// Status pill order: keep the actionable "kept" rows on top by default, with
// trimmed/filtered rows beneath in a stable order.
const SCAN_STATUS_RANK = {
  kept: 7,
  trimmed_top_n: 6,
  filtered_meta_policy: 5,
  filtered_ensemble: 4,
  filtered_self_study: 3,
  filtered_event_risk: 2,
  filtered_quality_gates: 1,
};

function getScanSourceRank(row = {}) {
  if (row.data_provider_primary === true) return 4;
  const provider = safeText(row.data_provider || row.provider || row.source || "").toLowerCase();
  if (provider === "schwab") return 3;
  if (row.used_fallback_data === true || provider) return 2;
  return 0;
}

function getScanReasonText(rawSig = {}) {
  const reasons = Array.isArray(rawSig?._filter_reasons) ? rawSig._filter_reasons : [];
  if (reasons.length) return formatFilterReasons(reasons).join("; ");
  const status = safeText(rawSig?._filter_status || "kept");
  return status === "kept" ? "" : formatNearMissSummary(status, reasons);
}

function getBreakoutAbovePct(row = {}) {
  const shadow = row.entry_timing_shadow || row.entry_timing_at_stage2 || {};
  return optionalNum(shadow.breakout_buffer_pct);
}

function formatBreakoutAboveLabel(row = {}) {
  const buf = getBreakoutAbovePct(row);
  if (buf === null) return "";
  return `${(buf * 100).toFixed(1)}% above pivot`;
}

function renderStagePill(row = {}) {
  if (row.breakout_confirmed) {
    return `<span class="scan-stage-pill" title="Stage 2 breakout confirmed">S2</span>`;
  }
  return `<span class="scan-stage-pill muted" title="Stage 2 setup (breakout pending)">S2</span>`;
}

function buildQualifiedReasonText(row = {}, rawSig = {}) {
  const parts = [];
  const bufLabel = formatBreakoutAboveLabel(row);
  if (bufLabel) parts.push(bufLabel);
  if (row.breakout_confirmed) parts.push("Breakout confirmed");
  const vol = optionalNum(row.volume_ratio);
  if (vol !== null && vol >= 1.05) parts.push(`${vol.toFixed(1)}x volume`);
  const flagged = optionalNum(row.flagged_days ?? row.days_flagged);
  if (flagged !== null && flagged > 0) parts.push(`${flagged}d flagged`);
  const topLive = formatStrategyLabel(row?.strategy_attribution?.top_live || "");
  if (topLive && topLive !== "—") parts.push(topLive);
  const rank = optionalNum(getRankScore(row));
  if (rank !== null) parts.push(`rank ${rank.toFixed(1)}`);
  const filterReasons = getScanReasonText(rawSig);
  if (filterReasons) parts.push(filterReasons);
  return parts.length ? parts.join(" · ") : "Qualified breakout";
}

function renderPriceCell(row = {}) {
  const price = row.price || row.current_price;
  return price ? formatMoney(price) : "—";
}

function renderGateChip(sig = {}) {
  const filterStatus = safeText(sig?._filter_status || "kept");
  const filterReasons = Array.isArray(sig?._filter_reasons) ? sig._filter_reasons : null;
  const badge = formatScanStatusBadge(filterStatus, filterReasons);
  const bucket = scanStatusSeverityBucket(filterStatus);
  // Keep Pass bucket short so the triage row doesn't re-introduce a loud CTA chip.
  const label = bucket === "pass" ? "Pass" : badge.label;
  return `<span class="scan-gate-chip scan-gate-chip--${escapeHtml(bucket)}" title="${escapeHtml(badge.title)}">${escapeHtml(label)}</span>`;
}

function renderTickerCell(row = {}) {
  const ticker = safeText(row.ticker || row.symbol || "?");
  return `<span class="scan-ticker-wrap"><strong class="scan-ticker">${ticker}</strong>${renderStagePill(row)}</span>`;
}

function renderScanRowActions({ idx, ticker, isKept, viewKey = "data-scan-view" }) {
  const chartBtn = `<button type="button" class="scan-row-action" ${viewKey}="${idx}" title="Open chart for ${safeText(ticker)}">Chart</button>`;
  const briefBtn = `<button type="button" class="scan-row-action" data-scan-brief="${idx}" title="Open decision brief for ${safeText(ticker)}">Brief</button>`;
  const stageBtn = isKept
    ? `<button type="button" class="scan-row-action scan-row-action--primary" data-idx="${idx}" title="Stage ${safeText(ticker)} as pending">Stage</button>`
    : `<button type="button" class="scan-row-action" disabled title="Filtered — cannot stage">Stage</button>`;
  return `<div class="scan-row-actions">${stageBtn}${briefBtn}${chartBtn}</div>`;
}

function getActiveScanStatusFilter() {
  const raw = safeText(state.scanStatusFilter || "pass").toLowerCase();
  return SCAN_STATUS_FILTERS.includes(raw) ? raw : "pass";
}

function countScanStatusBuckets(signals = []) {
  const counts = { pass: 0, review: 0, blocked: 0, info: 0 };
  for (const sig of signals) {
    const bucket = scanStatusSeverityBucket(sig?._filter_status || "kept");
    counts[bucket] = (counts[bucket] || 0) + 1;
  }
  return counts;
}

function updateScanStatusFilterUi(counts = {}) {
  const host = document.getElementById("scanStatusFilter");
  if (!host) return;
  const active = getActiveScanStatusFilter();
  host.querySelectorAll("[data-scan-status-filter]").forEach((btn) => {
    const key = safeText(btn.getAttribute("data-scan-status-filter")).toLowerCase();
    const n = Number(counts[key] || 0);
    const label = key.charAt(0).toUpperCase() + key.slice(1);
    btn.textContent = Number.isFinite(n) ? `${label} ${n}` : label;
    const isActive = key === active;
    btn.classList.toggle("is-active", isActive);
    btn.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

function wireScanStatusFilterOnce() {
  const host = document.getElementById("scanStatusFilter");
  if (!host || host.dataset.filterBound === "1") return;
  host.dataset.filterBound = "1";
  host.addEventListener("click", (e) => {
    const btn = e.target?.closest?.("[data-scan-status-filter]");
    if (!btn) return;
    const key = safeText(btn.getAttribute("data-scan-status-filter")).toLowerCase();
    if (!SCAN_STATUS_FILTERS.includes(key)) return;
    state.scanStatusFilter = key;
    const rows = state.latestShortlistSignals?.length
      ? state.latestShortlistSignals
      : state.latestSignals;
    renderScanRows(Array.isArray(rows) ? rows : []);
  });
}

function buildTriageRowHtml({ sig, row, idx, isKept, viewKey = "data-scan-view" }) {
  const ticker = row.ticker || row.symbol || "?";
  return `
    <td class="scan-col-ticker">${renderTickerCell(row)}</td>
    <td class="scan-col-gate">${renderGateChip(sig)}</td>
    <td class="scan-col-price mono-nums">${renderPriceCell(row)}</td>
    <td class="scan-col-rank">${renderRankScoreCell(row, { triage: true })}</td>
    <td class="scan-actions-cell">${renderScanRowActions({ idx, ticker, isKept, viewKey })}</td>
    <td class="scan-col-spacer" aria-hidden="true"></td>
  `;
}

function getScanSortValue(rawSig, field) {
  // Returns either a finite Number (for numeric sort) or a lowercase string
  // (for text/label sort). Returning `null` means "missing"; missing values
  // are pushed to the bottom regardless of direction so empty cells never
  // crowd the top of the table.
  if (!rawSig || typeof rawSig !== "object") return null;
  const row = normalizeScanSignal(rawSig);
  const advisory = row.advisory || {};
  switch (field) {
    case "ticker":
      return safeText(row.ticker || row.symbol || "").toUpperCase() || null;
    case "status": {
      const status = safeText(rawSig._filter_status || "kept").toLowerCase();
      const rank = SCAN_STATUS_RANK[status];
      return Number.isFinite(rank) ? rank : 0;
    }
    case "source":
      return getScanSourceRank(row);
    case "flagged_days":
      return optionalNum(row.flagged_days ?? row.days_flagged);
    case "strategy":
      return safeText(formatStrategyLabel(row?.strategy_attribution?.top_live || "")).toLowerCase() || null;
    case "price":
      return optionalNum(row.price ?? row.current_price);
    case "score":
      return getCompositeScore(row);
    case "p_up_10d": {
      const p = getCalibratedPUp(row);
      return p === null ? null : p;
    }
    case "expected_return_40d": {
      const er =
        optionalNum(row.expected_return_40d) ??
        optionalNum(row?.prob_rank?.expected_return_40d);
      return er;
    }
    case "confidence": {
      const label = formatConfidenceLabel(
        advisory.confidence_bucket ?? row.confidence_bucket ?? row.advisory_confidence,
      );
      if (!label || label === "—") return null;
      const rank = CONFIDENCE_RANK[label];
      return Number.isFinite(rank) ? rank : 0;
    }
    case "conviction":
      return getConvictionScore(row);
    case "sector":
      return safeText(row.sector_etf || "").toUpperCase() || null;
    case "reason":
      return safeText(getScanReasonText(rawSig)).toLowerCase() || null;
    case "actions":
      return isScanSignalStageable(row) ? 1 : 0;
    default:
      return null;
  }
}

export function compareScanSignals(a, b, field, dir) {
  const va = getScanSortValue(a, field);
  const vb = getScanSortValue(b, field);
  // Always push missing values to the bottom regardless of direction.
  if (va === null && vb === null) return 0;
  if (va === null) return 1;
  if (vb === null) return -1;
  let cmp;
  if (typeof va === "number" && typeof vb === "number") {
    cmp = va - vb;
  } else {
    // Coerce to string so mixed numeric/text edge cases (e.g. ticker "001")
    // still produce a deterministic order.
    cmp = String(va).localeCompare(String(vb), undefined, { numeric: true });
  }
  return dir === "asc" ? cmp : -cmp;
}

function getDefaultBreakoutRankValue(rawSig) {
  const row = normalizeScanSignal(rawSig);
  const backendRank = optionalNum(row.composite_score ?? row.rank_score_v2 ?? row.rank_score);
  if (backendRank !== null) {
    return Math.min(Math.max(backendRank / 100, 0), 1);
  }
  const score = optionalNum(getCompositeScore(row)) ?? 0;
  const pUp = optionalNum(getCalibratedPUp(row)) ?? 0;
  const conviction = optionalNum(getConvictionScore(row)) ?? 0;
  const flagged = optionalNum(row.flagged_days ?? row.days_flagged) ?? 0;
  const latestVol = optionalNum(row.latest_volume);
  const avgVol = optionalNum(row.avg_vol_50);
  const volumeRatio =
    latestVol !== null && avgVol !== null && avgVol > 0 ? latestVol / avgVol : 0;
  // Default blend prioritizes freshness + volume confirmation, then model strength.
  return (
    (Math.min(flagged, 7) / 7) * 0.32 +
    Math.min(volumeRatio / 2.0, 1.0) * 0.33 +
    Math.min(score / 100, 1.0) * 0.2 +
    Math.min(pUp, 1.0) * 0.1 +
    Math.min((conviction + 100) / 200, 1.0) * 0.05
  );
}

export function sortScanSignalsForRender(signals) {
  const sort = state.scanSort || { field: null, dir: "desc" };
  if (!Array.isArray(signals) || signals.length < 2) return signals;
  if (!sort.field) {
    const decorated = signals.map((sig, idx) => ({ sig, idx, rank: getDefaultBreakoutRankValue(sig) }));
    decorated.sort((x, y) => {
      if (y.rank !== x.rank) return y.rank - x.rank;
      return x.idx - y.idx;
    });
    return decorated.map((d) => d.sig);
  }
  // Decorate-sort-undecorate keeps the original index as a stable tiebreaker
  // so equal-keyed rows keep their backend ordering after sorting.
  const decorated = signals.map((sig, idx) => ({ sig, idx }));
  decorated.sort((x, y) => {
    const cmp = compareScanSignals(x.sig, y.sig, sort.field, sort.dir);
    return cmp !== 0 ? cmp : x.idx - y.idx;
  });
  return decorated.map((d) => d.sig);
}

function applyScanSortIndicators() {
  const sort = state.scanSort || { field: null, dir: "desc" };
  document.querySelectorAll("#scanSection thead th.sortable-th").forEach((th) => {
    const field = th.getAttribute("data-sort-key");
    const isActive = field && field === sort.field;
    th.classList.toggle("is-sorted", Boolean(isActive));
    th.classList.toggle("is-sorted-asc", Boolean(isActive) && sort.dir === "asc");
    th.classList.toggle("is-sorted-desc", Boolean(isActive) && sort.dir === "desc");
    if (isActive) {
      th.setAttribute("aria-sort", sort.dir === "asc" ? "ascending" : "descending");
    } else {
      th.setAttribute("aria-sort", "none");
    }
  });
}

function setScanSortField(field) {
  if (!field) return;
  const current = state.scanSort || { field: null, dir: "desc" };
  let nextDir;
  if (current.field === field) {
    nextDir = current.dir === "desc" ? "asc" : "desc";
  } else {
    nextDir = SCAN_SORT_DEFAULT_DIRECTION[field] || "desc";
  }
  state.scanSort = { field, dir: nextDir };
  const rows = state.latestShortlistSignals?.length ? state.latestShortlistSignals : state.latestSignals;
  renderScanRows(Array.isArray(rows) ? rows : []);
}

export function bindScanSortHandlers() {
  const headers = document.querySelectorAll("#scanSection thead th.sortable-th");
  if (!headers.length) return;
  headers.forEach((th) => {
    if (th.dataset.sortBound === "1") return;
    th.dataset.sortBound = "1";
    const field = th.getAttribute("data-sort-key");
    if (!field) return;
    th.addEventListener("click", () => setScanSortField(field));
    th.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        setScanSortField(field);
      }
    });
  });
  applyScanSortIndicators();
}

function renderScanFunnelFilterBanner(diag = {}) {
  const host = document.getElementById("scanFunnelFilterBanner");
  if (!host) return;
  const key = state.scanFunnelFilter;
  if (!key) {
    host.innerHTML = "";
    host.classList.add("hidden");
    return;
  }
  const hint = funnelStageFilterHint(key, diag);
  host.classList.remove("hidden");
  host.innerHTML = `
    <div class="scan-funnel-filter-banner">
      <span>Funnel filter: <strong>${escapeHtml(key.replace(/_/g, " "))}</strong></span>
      <span class="muted">${escapeHtml(hint)}</span>
      <button type="button" class="btn small secondary" id="scanFunnelFilterClear">Clear filter</button>
    </div>
  `;
  document.getElementById("scanFunnelFilterClear")?.addEventListener("click", () => {
    state.scanFunnelFilter = null;
    const rows = state.latestShortlistSignals?.length ? state.latestShortlistSignals : state.latestSignals;
    renderScanRows(Array.isArray(rows) ? rows : []);
    if (state.lastScanDiagnostics) deps.renderDiagnostics?.(state.lastScanDiagnostics);
  });
}

export function renderScanRows(signalsInput = []) {
  const body = document.getElementById("scanTableBody");
  const nearMissBody = document.getElementById("nearMissTableBody");
  const showMoreBtn = document.getElementById("scanShowMoreBtn");
  const qualifiedMetaEl = document.getElementById("scanQualifiedMeta");
  const nearMissCountEl = document.getElementById("nearMissSummaryCount");
  if (!body) return;
  wireScanStatusFilterOnce();
  // Always honour the active sort before rendering so re-renders triggered by
  // SSE / poll updates don't snap the operator back to backend order.
  const allSignals = sortScanSignalsForRender(Array.isArray(signalsInput) ? signalsInput : []);
  const statusCounts = countScanStatusBuckets(allSignals);
  updateScanStatusFilterUi(statusCounts);
  const statusFilter = getActiveScanStatusFilter();
  const passSignals = allSignals.filter(
    (sig) => scanStatusSeverityBucket(sig?._filter_status || "kept") === "pass",
  );
  const filteredByStatus = allSignals.filter(
    (sig) => scanStatusSeverityBucket(sig?._filter_status || "kept") === statusFilter,
  );
  const nearMissAll = allSignals.filter(
    (sig) => safeText(sig?._filter_status || "kept").toLowerCase() !== "kept",
  );
  const nearMissSignals = filterSignalsByFunnelStage(nearMissAll, state.scanFunnelFilter);
  renderScanFunnelFilterBanner(state.lastScanDiagnostics || {});
  const expanded = Boolean(state.scanRowsExpanded);
  const signals = expanded
    ? filteredByStatus
    : filteredByStatus.slice(0, QUALIFIED_ROWS_DEFAULT_LIMIT);
  body.innerHTML = "";
  applyScanSortIndicators();
  if (qualifiedMetaEl) {
    const shown = signals.length;
    const total = filteredByStatus.length;
    const passTotal = passSignals.length;
    const suffix = total > shown ? ` (showing ${shown})` : "";
    if (statusFilter === "pass") {
      qualifiedMetaEl.textContent = `${passTotal} qualified breakout${passTotal === 1 ? "" : "s"}${suffix}`;
    } else {
      const label = statusFilter.charAt(0).toUpperCase() + statusFilter.slice(1);
      qualifiedMetaEl.textContent = `${total} ${label.toLowerCase()} candidate${total === 1 ? "" : "s"}${suffix} · ${passTotal} pass`;
    }
  }
  if (nearMissCountEl) {
    const suffix = state.scanFunnelFilter && nearMissSignals.length !== nearMissAll.length
      ? ` (${nearMissSignals.length} match filter)`
      : "";
    nearMissCountEl.textContent = `(${nearMissAll.length}${suffix})`;
  }
  if (showMoreBtn) {
    if (filteredByStatus.length > QUALIFIED_ROWS_DEFAULT_LIMIT) {
      showMoreBtn.classList.remove("hidden");
      showMoreBtn.textContent = expanded
        ? `Show top ${QUALIFIED_ROWS_DEFAULT_LIMIT}`
        : `Show all ${filteredByStatus.length}`;
      showMoreBtn.onclick = () => {
        state.scanRowsExpanded = !Boolean(state.scanRowsExpanded);
        const rows = state.latestShortlistSignals?.length ? state.latestShortlistSignals : state.latestSignals;
        renderScanRows(Array.isArray(rows) ? rows : []);
      };
    } else {
      showMoreBtn.classList.add("hidden");
      showMoreBtn.onclick = null;
    }
  }
  if (nearMissBody) {
    nearMissBody.innerHTML = "";
    const nearMissRows = nearMissSignals.slice(0, NEAR_MISS_DEFAULT_LIMIT);
    if (!nearMissRows.length) {
      const msg = state.scanFunnelFilter
        ? "No near-miss rows match the selected funnel stage."
        : "No near-miss candidates for this scan mode.";
      nearMissBody.innerHTML = `<tr><td colspan="${TRIAGE_COLSPAN}" class="muted">${msg}</td></tr>`;
    } else {
      nearMissRows.forEach((sig, idx) => {
        const row = normalizeScanSignal(sig);
        const ticker = row.ticker || row.symbol || "?";
        const filterStatus = safeText(sig?._filter_status || "kept");
        const tr = document.createElement("tr");
        tr.setAttribute("data-scan-ticker", ticker);
        tr.setAttribute("data-scan-row-index", String(idx));
        tr.setAttribute("data-filter-status", filterStatus);
        tr.classList.add("scan-row--filtered");
        tr.innerHTML = buildTriageRowHtml({
          sig,
          row,
          idx,
          isKept: false,
          viewKey: "data-near-miss-view",
        });
        nearMissBody.appendChild(tr);
      });
    }
  }
  if (!signals.length) {
    const scanned = Boolean(state.lastScanAt);
    const emptyTitle = scanned ? "Zero candidates" : "No scan yet";
    const emptySub = scanned
      ? statusFilter !== "pass"
        ? `No ${statusFilter} candidates in this shortlist.`
        : nearMissSignals.length
          ? `${nearMissSignals.length} near-miss candidate(s) available below.`
          : "No qualified breakouts passed filters this scan."
      : "Run scan to load candidates.";
    const emptyCta = scanned
      ? ""
      : `<button id="scanEmptyCtaBtn" class="btn small secondary" type="button">Run Scan</button>`;
    body.innerHTML = `
      <tr class="scan-row--empty">
        <td colspan="${TRIAGE_COLSPAN}" class="muted">
          <div class="empty-state-cell">
            <svg class="empty-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 8h16M6 12h12M9 16h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
              <rect x="3" y="4" width="18" height="16" rx="2.5" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            <div>${emptyTitle}</div>
            <div class="muted small">${emptySub}</div>
            ${emptyCta}
          </div>
        </td>
      </tr>
    `;
    const cta = document.getElementById("scanEmptyCtaBtn");
    if (cta) cta.addEventListener("click", () => deps.runScan?.());
    void deps.renderScanDetail?.(null);
    deps.updateHeroInfographic?.();
    return;
  }

  let pupCount = 0;
  let confCount = 0;
  let convictionCount = 0;
  signals.forEach((sig, idx) => {
    const row = normalizeScanSignal(sig);
    const ticker = row.ticker || row.symbol || "?";
    const advisory = row.advisory;
    const conviction = getConvictionScore(row);
    const pUp = getCalibratedPUp(row);
    const conf = formatConfidenceLabel(advisory.confidence_bucket ?? row.confidence_bucket ?? row.advisory_confidence);
    const filterStatus = safeText(sig?._filter_status || "kept");
    const isKept = filterStatus === "kept";
    if (pUp !== null) pupCount += 1;
    if (conf !== "—") confCount += 1;
    if (conviction !== null) convictionCount += 1;
    const tr = document.createElement("tr");
    tr.setAttribute("data-scan-ticker", ticker);
    tr.setAttribute("data-scan-row-index", String(idx));
    tr.setAttribute("data-filter-status", filterStatus);
    if (!isKept) tr.classList.add("scan-row--filtered");
    tr.tabIndex = 0;
    tr.innerHTML = buildTriageRowHtml({ sig, row, idx, isKept });
    body.appendChild(tr);
  });
  if (signals.length && pupCount === 0 && confCount === 0 && convictionCount === 0 && !state.scanMissingEnrichmentWarned) {
    state.scanMissingEnrichmentWarned = true;
    logEvent({
      kind: "scan",
      severity: "warn",
      message:
        "Scan payload has no advisory/conviction fields. This usually means enrichment is disabled or failing upstream.",
    });
    updateActionCenter({
      title: "Scan Enrichment Missing",
      message: "No P(up), confidence, or conviction values were returned for this scan run.",
      severity: "warn",
    });
  } else if (pupCount > 0 || confCount > 0 || convictionCount > 0) {
    state.scanMissingEnrichmentWarned = false;
  }

  // Chart panel intentionally does NOT auto-render. Operators repeatedly hit
  // the "Test scan" / focused-mode confusion partly because the first row's
  // chart auto-loaded and dominated the surface. Now the panel stays idle
  // until the user clicks a row's "Chart" button or presses Enter on a row.
  // Re-highlight the previously selected ticker if it's still in the table,
  // so a refresh doesn't lose row selection — but don't trigger network fetch.
  if (state.selectedScanTicker) {
    const stillPresent = signals.some(
      (sig) => safeText(sig?.ticker || sig?.symbol || "") === state.selectedScanTicker,
    );
    if (stillPresent) {
      deps.highlightSelectedScanRow?.(state.selectedScanTicker);
    } else {
      state.selectedScanTicker = "";
      void deps.renderScanDetail?.(null);
    }
  } else {
    void deps.renderScanDetail?.(null);
  }

  // After moving to shortlist-driven rendering, the row indexes refer to
  // entries in the (possibly larger) shortlist, NOT to `state.latestSignals`
  // (which holds only kept candidates). Don't fall back to latestSignals[idx]
  // — that would silently surface the wrong ticker. Instead, prefer the
  // freshly rendered row, and as a last resort look it up by ticker.
  const lookupRowSignal = (idx, btnEl) => {
    const fromSignals = signals[idx];
    if (fromSignals) return fromSignals;
    const ticker = safeText(btnEl?.closest("tr")?.getAttribute("data-scan-ticker") || "").toUpperCase();
    if (!ticker) return null;
    const fromShortlist = (state.latestShortlistSignals || []).find(
      (s) => safeText(s?.ticker || s?.symbol || "").toUpperCase() === ticker,
    );
    if (fromShortlist) return fromShortlist;
    return (state.latestSignals || []).find(
      (s) => safeText(s?.ticker || s?.symbol || "").toUpperCase() === ticker,
    );
  };
  body.querySelectorAll("button[data-idx]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const idx = Number(e.currentTarget.getAttribute("data-idx"));
      const raw = lookupRowSignal(idx, e.currentTarget);
      if (!raw || !isScanSignalStageable(raw)) return;
      deps.openQueueScanDialog?.(normalizeScanSignal(raw));
    });
  });
  body.querySelectorAll("button[data-scan-view]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const idx = Number(e.currentTarget.getAttribute("data-scan-view"));
      const raw = lookupRowSignal(idx, e.currentTarget);
      if (!raw) return;
      void deps.renderScanDetail?.(normalizeScanSignal(raw));
    });
  });
  const nearMissLookup = (idx) => nearMissSignals[idx] || null;
  nearMissBody?.querySelectorAll("button[data-near-miss-view]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const idx = Number(e.currentTarget.getAttribute("data-near-miss-view"));
      const raw = nearMissLookup(idx);
      if (!raw) return;
      void deps.renderScanDetail?.(normalizeScanSignal(raw));
    });
  });
  const openBriefForSignal = (raw) => {
    if (!raw) return;
    void deps.renderScanDetail?.(normalizeScanSignal(raw));
    document.getElementById("scanDetailBriefCard")?.scrollIntoView?.({
      behavior: "smooth",
      block: "nearest",
    });
  };
  body.querySelectorAll("button[data-scan-brief]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const idx = Number(e.currentTarget.getAttribute("data-scan-brief"));
      openBriefForSignal(lookupRowSignal(idx, e.currentTarget));
    });
  });
  nearMissBody?.querySelectorAll("button[data-scan-brief]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const idx = Number(e.currentTarget.getAttribute("data-scan-brief"));
      openBriefForSignal(nearMissLookup(idx));
    });
  });
  body.querySelectorAll("tr[data-scan-row-index]").forEach((rowEl) => {
    const idx = Number(rowEl.getAttribute("data-scan-row-index"));
    // Pressing Enter / Space on a focused row is treated as the explicit
    // "Chart" action — same as clicking the Chart button. Plain row clicks
    // (anywhere other than a button) only update selection highlight without
    // fetching chart data, so the panel doesn't auto-populate during scrolling.
    const resolveSignal = () => {
      const raw = signals[idx];
      if (raw) return normalizeScanSignal(raw);
      const ticker = safeText(rowEl.getAttribute("data-scan-ticker") || "").toUpperCase();
      if (!ticker) return null;
      const fallback =
        (state.latestShortlistSignals || []).find(
          (s) => safeText(s?.ticker || s?.symbol || "").toUpperCase() === ticker,
        ) ||
        (state.latestSignals || []).find(
          (s) => safeText(s?.ticker || s?.symbol || "").toUpperCase() === ticker,
        );
      return fallback ? normalizeScanSignal(fallback) : null;
    };
    const openChart = () => {
      const sig = resolveSignal();
      if (sig) void deps.renderScanDetail?.(sig);
    };
    const justSelect = () => {
      const sig = resolveSignal();
      if (!sig) return;
      const ticker = safeText(sig?.ticker || sig?.symbol || "");
      state.selectedScanTicker = ticker;
      deps.highlightSelectedScanRow?.(ticker);
    };
    rowEl.addEventListener("click", (e) => {
      if (e.target instanceof Element && e.target.closest("button")) return;
      justSelect();
    });
    rowEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openChart();
      }
    });
  });
  wireScanRankWhyTooltips(body);
  deps.updateHeroInfographic?.();
}
