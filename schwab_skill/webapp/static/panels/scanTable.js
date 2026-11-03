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
  strategyCatalogEntry,
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
  getHistoricalRank,
  getHistoricalRankRaw,
  listPlaceBasis,
  isResearchRankRow,
  getSetupConfidence,
  getSetupConfidenceFeatures,
  getSetupConfidenceCoverage,
  getExtensionPts,
  getTrustScore,
  getTrustScoreRaw,
  getRankCellTrust,
  getTrustScoreEvidence,
  getTrustScoreCaps,
  getEquityConfidence,
  getEquityConfidenceSummary,
  getAnalogNeighborhoodPf,
  getAnalogN,
  getAnalogThin,
  getAnalogRegime,
  getAnalogReasons,
  getAnalogNeighbors,
  getAnalogCalibrationNote,
  getRankLiveCut,
  buildRankPercentileResolver,
} from "../modules/signalScores.js";
import {
  formatScanStatusBadge,
  formatNearMissSummary,
  formatFilterReasons,
} from "../modules/filterReasons.js";
import {
  isScanSignalStageable,
  renderSignalProvenanceChip,
  renderTradeableVerdict,
  scanRowTriageBucket,
} from "../modules/signalProvenance.js";
import { clearToQueue, stageHoldReasons } from "../modules/stageEvidence.js";
import {
  filterSignalsByFunnelStage,
  funnelStageFilterHint,
} from "../modules/funnelFilter.js";
import { wireScanRankWhyTooltips } from "../modules/floatTooltip.js";
import { normalizeScanSignal } from "../modules/scanSignals.js";
import { buildDiagnosticsSummary } from "./scanDiagnostics.js";

const QUALIFIED_ROWS_DEFAULT_LIMIT = 20;
const NEAR_MISS_DEFAULT_LIMIT = 10;
const RANK_EXPLAIN_MODE_KEY = "tradingbot.scan.rank_explain_mode";
const TRIAGE_COLSPAN = 9;
/** Matches Brief advisory stop band in webapp/main.py decision brief. */
const ADVISORY_STOP_PCT = 0.07;
const SCAN_STATUS_FILTERS = Object.freeze(["pass", "review", "blocked", "info"]);
const SCAN_STATUS_FILTER_LABELS = Object.freeze({
  pass: "Tradeable",
  review: "Research",
  blocked: "Blocked",
  info: "Other",
});

export { scanRowTriageBucket };

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

function cappedFromPhrase(shown, raw) {
  if (shown === null || raw === null || raw - shown < 0.05) return "";
  return ` (capped from ${raw.toFixed(1)})`;
}

function historicalRaw(row = {}, historical) {
  const stamped = getHistoricalRankRaw(row);
  if (stamped !== null) return stamped;
  const reasons = Array.isArray(row.historical_rank_reasons) ? row.historical_rank_reasons : [];
  for (const reason of reasons) {
    const match = String(reason || "").match(/risk cap ([0-9.]+) ->/);
    if (match) return optionalNum(match[1]);
  }
  return historical;
}

function buildRankWhyText(row = {}, { percentile = null, cut = null, place = null } = {}) {
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
  const composite = optionalNum(getCompositeScore(row));
  const compositeRaw = optionalNum(row.composite_score_raw);
  const reasons = Array.isArray(row.reliability_reasons) ? row.reliability_reasons : [];
  const capReasons = reasons
    .filter((r) => String(r || "").startsWith("composite_capped"))
    .map((r) => String(r || "").replace(/^composite_capped_/, "").replaceAll("_", " "));
  const historical = optionalNum(getHistoricalRank(row));
  const sortScore = optionalNum(row.sort_score);
  const confidence = optionalNum(getSetupConfidence(row));
  const confidenceFeatures = getSetupConfidenceFeatures(row);
  const confidenceCoverage = optionalNum(getSetupConfidenceCoverage(row));
  const extension = getExtensionPts(row);
  const liveSort = optionalNum(row.signal_score);
  const trust = getRankCellTrust(row);
  const trustRaw = getTrustScoreRaw(row);
  const trustStamped = getTrustScore(row) !== null;
  const trustBound = trust !== null && trustRaw !== null && trustRaw - trust >= 0.05;
  const trustEvidence = getTrustScoreEvidence(row);
  const trustCaps = getTrustScoreCaps(row);
  const fileConfidence = getEquityConfidence(row);
  const fileSummary = getEquityConfidenceSummary(row);
  const placeBasis = listPlaceBasis(row);
  const segments = [];
  if (place !== null) {
    const by = placeBasis.text ? ` by ${placeBasis.text}` : "";
    segments.push(`list place ${Math.round(place)}${by}`);
  }
  if (trust !== null && trustBound) {
    const why = trustCaps.length ? ` (${trustCaps.join(", ")})` : "";
    segments.push(`trust ${trust.toFixed(0)}/100, capped from ${trustRaw.toFixed(0)}${why}`);
  } else if (trust !== null) {
    segments.push(`trust ${trust.toFixed(0)}/100 (higher is less extended; does not change live order)`);
  }
  if (fileConfidence !== null) {
    segments.push(
      `file confidence ${fileConfidence.toFixed(0)}/100 (coverage × agreement × freshness; does not change the rank)`,
    );
  }
  if (fileSummary) {
    segments.push(fileSummary.replace(/\bcaps (\d+\/\d+)\b/g, "cap inputs $1"));
  }
  if (trustEvidence) segments.push(trustEvidence);
  else if (trust !== null && extension !== null && extension > 0) {
    segments.push("Mapped from 52-week extension. The published book evidence is attached on the next scan.");
  }
  if (trustCaps.length && !trustBound) {
    segments.push(`safety flag: ${trustCaps.join(", ")} (trust already under the ceiling)`);
  }
  if (extension !== null && (extension > 0 || (liveSort !== null && liveSort > 0) || trustStamped)) {
    segments.push(
      `52w extension ${extension.toFixed(0)} (raw points; lower is less extended)`,
    );
  }
  if (placeBasis.kind !== "signal" && liveSort !== null && liveSort > 0) {
    segments.push(`live sort signal points ${liveSort.toFixed(1)} (composite, not a probability)`);
  }
  if (percentile !== null) {
    const cohort = historical !== null ? "historical cohort" : "cohort";
    segments.push(`${cohort} p${Math.round(percentile)}`);
  }
  if (historical !== null) {
    const raw = historicalRaw(row, historical);
    segments.push(`historical blend ${historical.toFixed(1)}${cappedFromPhrase(historical, raw)} (shadow)`);
  } else if (rank !== null) {
    segments.push(`rank ${rank.toFixed(1)}`);
  }
  segments.push(`basis ${basis}`);
  if (confidence !== null) {
    segments.push(`survival confidence ${confidence.toFixed(0)}/100 (shadow)`);
    if (confidenceFeatures.length) segments.push(`from ${confidenceFeatures.join(", ")}`);
    if (confidenceCoverage !== null && confidenceCoverage < 1) {
      segments.push(`confidence coverage ${pct(confidenceCoverage, 0)}`);
    }
  }
  if (cut && cut.threshold !== null) {
    const side = cut.wouldDrop === true ? "below" : cut.wouldDrop === false ? "above" : "unknown";
    segments.push(`live cut ${cut.threshold.toFixed(1)} (${side})`);
  }
  const compositeFromRaw = cappedFromPhrase(composite, compositeRaw);
  const compositeAtCeiling = composite !== null
    && !compositeFromRaw
    && capReasons.includes("risk")
    && Math.abs(composite - 45) < 0.05;
  if (composite !== null) {
    const ceiling = compositeAtCeiling ? " (forensic ceiling)" : compositeFromRaw;
    segments.push(`composite ${composite.toFixed(1)}${ceiling}`);
  }
  if (
    sortScore !== null
    && liveSort !== null
    && Math.abs(sortScore - liveSort) >= 0.05
  ) {
    segments.push(`book sort ${sortScore.toFixed(1)}`);
  }
  if (edge !== null) segments.push(`edge ${edge.toFixed(1)}`);
  if (reliability !== null) segments.push(`reliability ${reliability.toFixed(1)}`);
  if (execution !== null) segments.push(`execution ${execution.toFixed(1)}`);
  if (ptsVol !== null) segments.push(`vol dry-up ${ptsVol.toFixed(1)}`);
  if (volumeRatio !== null) segments.push(`session volume ${volumeRatio.toFixed(2)}×`);
  if (ptsMiro !== null && ptsMiro > 0) segments.push(`miro pts ${ptsMiro.toFixed(1)}`);
  if (legacyRank !== null && rank !== null && Math.abs(legacyRank - rank) >= 0.05) {
    segments.push(`v1 ${legacyRank.toFixed(1)}`);
  }
  if (rankV2 !== null && (rank === null || Math.abs(rankV2 - rank) >= 0.05)) {
    segments.push(`v2 ${rankV2.toFixed(1)}`);
  }
  if (pUp !== null) segments.push(`advisory p(up) ${pct(pUp, 1)} (weight 0 on the live score)`);
  const compositeCapped = Boolean(compositeFromRaw) || compositeAtCeiling;
  if (capReasons.length && !compositeCapped) {
    const leftover = capReasons.filter((reason) => !(reason === "risk" && trustBound && trustCaps.includes("forensic")));
    if (leftover.length) segments.push(`caps ${leftover.join(", ")}`);
  }
  return segments.join(" · ");
}

function buildRankWhyInlineText(row = {}, { cut = null } = {}) {
  const reliability = optionalNum(getReliabilityScore(row));
  const execution = optionalNum(getExecutionScore(row));
  const pUp = optionalNum(getCalibratedPUp(row));
  const confidence = optionalNum(getSetupConfidence(row));
  const reasons = Array.isArray(row.reliability_reasons) ? row.reliability_reasons : [];
  const hasCap = reasons.some((r) => String(r || "").includes("capped"));
  // The cell already prints percentile + score, so inline text adds context only.
  const segments = [];
  if (cut && cut.wouldDrop === true) segments.push("below live cut");
  if (confidence !== null) segments.push(`conf ${confidence.toFixed(0)}`);
  if (reliability !== null) segments.push(`rel ${reliability.toFixed(0)}`);
  if (execution !== null) segments.push(`exec ${execution.toFixed(0)}`);
  if (pUp !== null) segments.push(`P(up) ${pct(pUp, 1)}`); // ratio 0–1 from GET /api/scan p_up_calibrated
  if (hasCap) segments.push("cap applied");
  return segments.join(" · ");
}

/**
 * Rank cell: list place (1, 2, 3) by signal points on tradeable rows, and by
 * the historical blend on research rows. `trust_score` stays in the tooltip.
 * The line under the place is 52-week extension, which can match across rows.
 */
export function renderRankScoreCell(row = {}, { triage = false, percentile = null, place = null } = {}) {
  const rank = getRankScore(row);
  const shown = rank !== null ? `${rank.toFixed(1)}` : "—";
  const extension = getExtensionPts(row);
  const trust = getRankCellTrust(row);
  const liveSort = optionalNum(row.signal_score);
  const placeNum = optionalNum(place);
  // A literal 0/0 on research rows is an empty score stack, not "least extended".
  const showExtension = trust !== null;
  const cut = getRankLiveCut(row);
  const mode = getRankExplainMode();
  const why = buildRankWhyText(row, { percentile, cut, place: placeNum });
  const title = why || (placeNum !== null ? `Place ${Math.round(placeNum)}` : trust !== null ? `Trust ${trust.toFixed(0)}` : showExtension ? `52w extension ${extension.toFixed(0)}` : `Rank ${shown}`);
  const pctLabel = percentile === null ? "" : `p${Math.round(percentile)}`;
  const scoreClasses = ["scan-rank-score"];
  if (cut.wouldDrop === true) scoreClasses.push("scan-rank-score--below-cut");
  let primary;
  let secondary;
  if (placeNum !== null) {
    primary = `<span class="${scoreClasses.join(" ")}">${Math.round(placeNum)}</span>`;
    if (showExtension && extension !== null) {
      const subClasses = ["scan-rank-sub"];
      if (extension > 37) subClasses.push("scan-rank-score--extended");
      secondary = `<span class="${subClasses.join(" ")}">52w ${extension.toFixed(0)}</span>`;
    } else {
      secondary = "";
    }
  } else if (trust !== null && showExtension) {
    if (extension > 37) scoreClasses.push("scan-rank-score--extended");
    primary = `<span class="${scoreClasses.join(" ")}">${trust.toFixed(0)}</span>`;
    secondary = extension !== null ? `<span class="scan-rank-sub">52w ${extension.toFixed(0)}</span>` : "";
  } else if (showExtension) {
    if (extension > 37) scoreClasses.push("scan-rank-score--extended");
    primary = `<span class="${scoreClasses.join(" ")}">52w ${extension.toFixed(0)}</span>`;
    secondary = liveSort !== null ? `<span class="scan-rank-sub">sig ${liveSort.toFixed(0)}</span>` : "";
  } else {
    primary = `<span class="${scoreClasses.join(" ")}">${pctLabel || shown}</span>`;
    secondary = pctLabel && rank !== null ? `<span class="scan-rank-sub">${shown}</span>` : "";
  }
  const cellClasses = ["scan-rank-cell"];
  if (triage) cellClasses.push("scan-rank-cell--triage");
  if (mode === "inline") {
    cellClasses.push("scan-rank-cell--inline");
    const inlineWhy = buildRankWhyInlineText(row, { cut });
    const tail = inlineWhy ? `<span class="scan-rank-inline">${escapeHtml(inlineWhy)}</span>` : "";
    return `<span class="${cellClasses.join(" ")}" title="${escapeHtml(title)}">${primary}${secondary}${tail}</span>`;
  }
  return `<span class="${cellClasses.join(" ")}"><span class="scan-rank-stack">${primary}${secondary}</span><span class="scan-rank-why" data-rank-tip="${escapeHtml(title)}" tabindex="0" role="button" aria-label="Why this rank">?</span></span>`;
}

function formatAnalogRegimeTag(regime) {
  const raw = safeText(regime || "").toLowerCase();
  if (!raw || raw === "unknown" || raw === "all_eras") return raw === "all_eras" ? "all-eras" : "";
  if (raw.includes("spy_below") || raw.includes("bear")) return "bear-like";
  if (raw.includes("spy_above") || raw.includes("bull")) return "bull-like";
  const era = raw.split("|")[0];
  return era.replaceAll("_", "-");
}

function buildAnalogWhyText(row = {}) {
  const pf = getAnalogNeighborhoodPf(row);
  const n = getAnalogN(row);
  const thin = getAnalogThin(row);
  const regime = getAnalogRegime(row);
  const reasons = getAnalogReasons(row);
  const neighbors = getAnalogNeighbors(row);
  const cal = getAnalogCalibrationNote(row);
  const segments = [];
  if (pf !== null) segments.push(`neighborhood PF ${pf.toFixed(2)}`);
  if (n !== null) segments.push(`n=${Math.round(n)}`);
  if (thin) segments.push("shrunk toward era prior");
  if (regime) segments.push(`slice ${regime}`);
  reasons.slice(0, 2).forEach((r) => {
    if (r && !segments.some((s) => String(s).includes(r))) segments.push(r);
  });
  neighbors.slice(0, 3).forEach((nb) => {
    const ticker = safeText(nb?.ticker || "").toUpperCase();
    const date = safeText(nb?.entry_date || "").slice(0, 10);
    const ret = optionalNum(nb?.net_return);
    const hold = optionalNum(nb?.hold_days);
    const exit = safeText(nb?.exit_reason || "");
    const bits = [ticker, date];
    if (ret !== null) bits.push(`${(ret * 100).toFixed(1)}%`);
    if (hold !== null) bits.push(`${Math.round(hold)}d`);
    if (exit) bits.push(exit.replaceAll("_", " "));
    if (bits.filter(Boolean).length) segments.push(bits.filter(Boolean).join(" "));
  });
  if (cal) segments.push(cal);
  return segments.join(" · ");
}

export function renderAnalogCell(row = {}, { triage = false } = {}) {
  const pf = getAnalogNeighborhoodPf(row);
  const n = getAnalogN(row);
  const thin = getAnalogThin(row);
  const regime = formatAnalogRegimeTag(getAnalogRegime(row));
  const mode = getRankExplainMode();
  const why = buildAnalogWhyText(row);
  const titleBits = [];
  if (pf !== null) titleBits.push(`${pf.toFixed(2)} PF`);
  if (thin) titleBits.push("thin sample");
  if (regime) titleBits.push(regime);
  if (why) titleBits.push(why);
  const title = titleBits.join(" · ") || "No analog neighborhood";
  if (triage) {
    const num = pf === null ? "—" : pf.toFixed(2);
    const thinClass = thin ? " scan-analog-num--thin" : "";
    return `<span class="scan-analog-cell scan-analog-cell--triage"><span class="scan-analog-num${thinClass}" data-analog-tip="${escapeHtml(title)}" tabindex="0">${escapeHtml(num)}</span></span>`;
  }
  let primary;
  if (pf === null && n === null) {
    primary = `<span class="scan-analog-score muted">—</span>`;
  } else if (thin) {
    const pfBit = pf === null ? "thin" : `${pf.toFixed(2)} PF`;
    primary = `<span class="scan-analog-score scan-analog-score--thin">${escapeHtml(pfBit)} · thin</span>`;
  } else {
    const nBit = n === null ? "" : ` · n=${Math.round(n)}`;
    primary = `<span class="scan-analog-score">${pf === null ? "—" : `${pf.toFixed(2)} PF`}${nBit}</span>`;
  }
  const regimeBit = regime ? `<span class="scan-analog-regime muted">${escapeHtml(regime)}</span>` : "";
  const cellClasses = ["scan-analog-cell"];
  if (triage) cellClasses.push("scan-analog-cell--triage");
  if (mode === "inline") {
    cellClasses.push("scan-analog-cell--inline");
    const inline = why ? `<span class="scan-analog-inline">${escapeHtml(why)}</span>` : "";
    return `<span class="${cellClasses.join(" ")}" title="${escapeHtml(title)}">${primary}${regimeBit}${inline}</span>`;
  }
  return `<span class="${cellClasses.join(" ")}"><span class="scan-analog-stack">${primary}${regimeBit}</span><span class="scan-analog-why" data-analog-tip="${escapeHtml(title)}" tabindex="0" role="button" aria-label="Why this analog score">?</span></span>`;
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
  volume_ratio: "desc",
  price: "desc",
  score: "desc",
  analog_neighborhood_pf: "desc",
  stop: "desc",
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

/** Compact meta for rich ticker line: "XLY · +1.8% pivot" (vol is its own column). */
function formatTickerMetaLine(row = {}) {
  const bits = [];
  const sector = safeText(row.sector_etf || "").toUpperCase();
  if (sector) bits.push(sector);
  const buf = getBreakoutAbovePct(row);
  if (buf !== null) {
    const sign = buf >= 0 ? "+" : "";
    bits.push(`${sign}${(buf * 100).toFixed(1)}% pivot`);
  }
  return bits.join(" · ");
}

function getAdvisoryStopPrice(row = {}) {
  const price = optionalNum(row.price || row.current_price);
  if (price === null || price <= 0) return null;
  return price * (1 - ADVISORY_STOP_PCT);
}

function renderStagePill(row = {}) {
  if (row.breakout_confirmed) {
    return `<span class="scan-stage-pill" title="Stage 2 breakout confirmed">S2</span>`;
  }
  return `<span class="scan-stage-pill muted" title="Stage 2 setup (breakout pending)">S2</span>`;
}

function buildQualifiedReasonText(row = {}, rawSig = {}) {
  // Only carry what no other cell on this row already shows. Stage2/VCP pass,
  // pivot distance, breakout-confirmed, volume, strategy names and rank each
  // have their own column or pill; repeating them here made every row read
  // identically and buried the two or three fields that actually differ.
  const parts = [];
  const family = safeText(row.entry_family || rawSig?.entry_family || "").toLowerCase();
  if (family === "pead_primary") parts.push("PEAD primary");
  if (row.pead_extension_drop_would_drop === true || rawSig?.pead_extension_drop_would_drop === true) {
    parts.push("extended, shadow drop");
  }
  else if (family === "both") parts.push("Stage2 + PEAD");
  // Confirmed is the norm here, so flag only the exception.
  if (!row.breakout_confirmed) parts.push("breakout pending");
  const flagged = optionalNum(row.flagged_days ?? row.days_flagged);
  if (flagged !== null && flagged > 0) parts.push(`${flagged}d flagged`);
  const filterReasons = getScanReasonText(rawSig);
  if (filterReasons) parts.push(filterReasons);
  return parts.join(" · ");
}

/** Plain-English why this row appears (pass thesis or near-miss failure). */
function buildRowWhyText(row = {}, rawSig = {}) {
  const status = safeText(rawSig?._filter_status || "kept").toLowerCase() || "kept";
  if (status !== "kept") {
    const reasons = Array.isArray(rawSig?._filter_reasons) ? rawSig._filter_reasons : [];
    const human = formatFilterReasons(reasons);
    const text = human.length ? human.join(" · ") : formatNearMissSummary(status, reasons);
    // On most near-miss rows the Gate cell already carries this verbatim.
    const gate = gateChipLabel(rawSig);
    return text.trim().toLowerCase() === gate.trim().toLowerCase() ? "" : text;
  }
  return buildQualifiedReasonText(row, rawSig);
}

function renderPriceCell(row = {}) {
  const price = row.price || row.current_price;
  return price ? formatMoney(price) : "—";
}

/** Text shown in the Gate cell; the row note suppresses anything identical. */
function gateChipLabel(sig = {}) {
  if (isScanSignalStageable(sig) && stageHoldReasons(sig).length) return "Hold";
  const html = renderTradeableVerdict(sig);
  const match = String(html || "").match(/>(Tradeable|Research|Blocked|Other)</);
  return match ? match[1] : formatScanStatusBadge(
    safeText(sig?._filter_status || "kept"),
    Array.isArray(sig?._filter_reasons) ? sig._filter_reasons : null,
  ).label;
}

function renderGateChip(sig = {}) {
  const hold = isScanSignalStageable(sig) ? stageHoldReasons(sig)[0] : "";
  if (hold) {
    return `<span class="scan-gate-chip scan-gate-chip--blocked scan-tradeable-verdict" title="${escapeHtml(hold)}">Hold</span>`;
  }
  return renderTradeableVerdict(sig);
}

function renderGradeChip(row = {}) {
  const grade = row.debate_grade || {};
  const letter = safeText(grade.qual_letter || "");
  if (!letter || grade.qual_abstain) return "";
  // C/D/F are an uncalibrated shadow and do not change rank. Painting them
  // on every row made the table look like a field of failures.
  if (letter !== "A" && letter !== "B") return "";
  return `<span class="scan-grade-chip" data-letter="${escapeHtml(letter)}" title="Uncalibrated shadow. Does not change rank.">${escapeHtml(letter)}</span>`;
}

function renderTickerCell(row = {}, rawSig = {}) {
  const ticker = safeText(row.ticker || row.symbol || "?");
  const company = safeText(row.company_name || "").trim();
  const meta = formatTickerMetaLine(row);
  const why = buildRowWhyText(row, rawSig);
  const companyHtml = company
    ? `<span class="scan-company-name" title="${escapeHtml(company)}">${escapeHtml(company)}</span>`
    : "";
  const metaHtml = meta ? `<span class="scan-ticker-meta">${escapeHtml(meta)}</span>` : "";
  const whyHtml = why
    ? `<span class="scan-row-why" title="${escapeHtml(why)}">${escapeHtml(why)}</span>`
    : "";
  const provHtml = `<span class="scan-ticker-prov">${renderSignalProvenanceChip({ ...rawSig, ...row })}</span>`;
  // Ticker is the width:auto column, so these three short facts share one
  // wrapping line rather than stacking the row three extra lines deep.
  const sublineHtml = `<span class="scan-ticker-subline">${metaHtml}${provHtml}${whyHtml}</span>`;
  return `<span class="scan-ticker-stack"><span class="scan-ticker-wrap"><strong class="scan-ticker">${escapeHtml(ticker)}</strong>${renderStagePill(row)}${renderGradeChip({ ...rawSig, ...row })}</span>${companyHtml}${sublineHtml}</span>`;
}

function renderStrategyCell(row = {}) {
  const { labels, timeframes } = strategyLabelsForRow(row, state.scanCatalog);
  if (!labels.length) return `<span class="muted">—</span>`;
  const title = [...labels, ...timeframes].filter(Boolean).join(" · ");
  const extra = labels.length - 1;
  const more = extra > 0
    ? `<span class="scan-strategy-more" title="${escapeHtml(title)}">+${extra}</span>`
    : "";
  return `<span class="scan-strategy-label" title="${escapeHtml(title)}"><span class="scan-strategy-pill">${escapeHtml(labels[0])}</span>${more}</span>`;
}

export function strategyLabelsForRow(row = {}, catalog = null) {
  const attr = row?.strategy_attribution || {};
  const cat = catalog || state.scanCatalog;
  const passedNames = Array.isArray(attr.passed_names)
    ? attr.passed_names.map((n) => safeText(n)).filter((n) => n && n !== "—")
    : [];
  const passedIds = Array.isArray(attr.passed_ids)
    ? attr.passed_ids.map((id) => safeText(id)).filter(Boolean)
    : [];
  const labels = passedNames.length
    ? passedNames
    : passedIds.map((id) => formatStrategyLabel(id, cat)).filter((l) => l && l !== "—");
  if (!labels.length) {
    const rawId = attr.top_live || attr.catalog_id || "";
    const one = formatStrategyLabel(rawId, cat);
    if (one && one !== "—") labels.push(one);
  }
  const timeframes = [];
  const seenTf = new Set();
  const tfSources = [
    ...passedIds,
    attr.catalog_id,
    attr.top_live,
    attr.timeframe,
  ];
  for (const raw of tfSources) {
    const entry = strategyCatalogEntry(raw, cat);
    const tf = safeText(entry?.timeframe || (raw === attr.timeframe ? attr.timeframe : "")).toLowerCase();
    if (!tf || seenTf.has(tf)) continue;
    seenTf.add(tf);
    timeframes.push(tf);
  }
  return { labels, timeframes, passedIds };
}

function renderVolCell(row = {}) {
  const vol = optionalNum(row.volume_ratio);
  if (vol === null) return `<span class="muted">—</span>`;
  // Breakouts want volume expansion, so weight the number by that reading:
  // a 0.7x and a 5.1x row should not scan the same.
  let tone = "";
  let title = `${vol.toFixed(1)}x average volume`;
  if (vol >= 1.5) {
    tone = " scan-vol--strong";
    title += " — expansion";
  } else if (vol < 1.0) {
    tone = " scan-vol--weak";
    title += " — below average";
  }
  return `<span class="mono-nums${tone}" title="${escapeHtml(title)}">${vol.toFixed(1)}×</span>`;
}

function renderStopCell(row = {}) {
  const stop = getAdvisoryStopPrice(row);
  if (stop === null) return `<span class="muted">—</span>`;
  const riskPct = ADVISORY_STOP_PCT * 100;
  const pctLabel = `−${riskPct.toFixed(1)}%`;
  const width = Math.min(100, Math.max(8, (riskPct / 15) * 100));
  return `<span class="scan-stop-cell" title="Brief advisory invalidation (${pctLabel} from price)"><span class="scan-stop-track" aria-hidden="true"><span class="scan-stop-risk" style="width:${width.toFixed(0)}%"></span></span><span class="scan-stop-price mono-nums">${formatMoney(stop)}</span><span class="scan-stop-pct muted">${pctLabel}</span></span>`;
}

function renderScanRowActions({ idx, ticker, isKept, stageable, hold = "", viewKey = "data-scan-view" }) {
  const chartBtn = `<button type="button" class="scan-row-action" ${viewKey}="${idx}" title="Open chart for ${safeText(ticker)}">Chart</button>`;
  const briefBtn = `<button type="button" class="scan-row-action" data-scan-brief="${idx}" title="Open decision brief for ${safeText(ticker)}">Brief</button>`;
  const canStage = Boolean(stageable) && !hold;
  const disabledTitle = hold || (isKept ? "Research sleeve — not executable" : "Filtered — cannot stage");
  const stageBtn = canStage
    ? `<button type="button" class="scan-row-action scan-row-action--primary" data-idx="${idx}" title="Stage ${safeText(ticker)} as pending">Stage</button>`
    : `<button type="button" class="scan-row-action" disabled title="${escapeHtml(disabledTitle)}">${hold ? "Hold" : "Stage"}</button>`;
  return `<div class="scan-row-actions">${stageBtn}${briefBtn}${chartBtn}</div>`;
}

function getActiveScanStatusFilter() {
  const raw = safeText(state.scanStatusFilter || "pass").toLowerCase();
  return SCAN_STATUS_FILTERS.includes(raw) ? raw : "pass";
}

/**
 * Keep the operator on a bucket that actually has rows.
 * Weekly / monthly kept names land in Research, so a default Tradeable
 * filter would otherwise empty the table and clear Evaluate setup.
 */
export function pickNonEmptyScanStatusFilter(counts = {}, current = "pass") {
  const cur = SCAN_STATUS_FILTERS.includes(String(current || "").toLowerCase())
    ? String(current).toLowerCase()
    : "pass";
  if (Number(counts[cur] || 0) > 0) return cur;
  if (cur !== "pass") return cur;
  for (const key of SCAN_STATUS_FILTERS) {
    if (Number(counts[key] || 0) > 0) return key;
  }
  return cur;
}

function countScanStatusBuckets(signals = []) {
  const counts = { pass: 0, review: 0, blocked: 0, info: 0 };
  for (const sig of signals) {
    const bucket = scanRowTriageBucket(sig);
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
    const label = SCAN_STATUS_FILTER_LABELS[key] || key.charAt(0).toUpperCase() + key.slice(1);
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

const SCAN_FAMILY_FILTERS = ["all", "stage2", "pead", "pullback"];
const SCAN_FAMILY_FILTER_LABELS = {
  all: "All",
  stage2: "Stage 2",
  pead: "PEAD",
  pullback: "Pullback",
};

function primaryStrategyId(row = {}) {
  const attr = row.strategy_attribution || {};
  return safeText(attr.top_live || attr.catalog_id || "").toLowerCase();
}

function isPullbackRow(row = {}) {
  const family = safeText(row.entry_family || "").toLowerCase();
  return family === "pullback" || primaryStrategyId(row).includes("pullback");
}

function isPeadRow(row = {}) {
  const family = safeText(row.entry_family || "").toLowerCase();
  const primary = primaryStrategyId(row);
  return family === "pead_primary" || family === "both" || primary.includes("pead");
}

/** True when a row belongs on the selected entry-family book. */
export function rowMatchesScanFamily(row = {}, filter = "all") {
  const key = SCAN_FAMILY_FILTERS.includes(filter) ? filter : "all";
  if (key === "all") return true;
  const family = safeText(row.entry_family || "").toLowerCase();
  const primary = primaryStrategyId(row);
  if (key === "pullback") return isPullbackRow(row);
  if (key === "pead") return isPeadRow(row);
  if (isPullbackRow(row)) return false;
  if (family === "pead_primary") return false;
  return (
    family === "stage2"
    || family === "both"
    || primary.includes("trend_breakout")
    || primary.includes("stage2")
  );
}

function getActiveScanFamilyFilter() {
  const raw = safeText(state.scanFamilyFilter || "all").toLowerCase();
  return SCAN_FAMILY_FILTERS.includes(raw) ? raw : "all";
}

function updateScanFamilyFilterUi(counts = {}) {
  const host = document.getElementById("scanFamilyFilter");
  if (!host) return;
  const active = getActiveScanFamilyFilter();
  host.querySelectorAll("[data-scan-family-filter]").forEach((btn) => {
    const key = safeText(btn.getAttribute("data-scan-family-filter")).toLowerCase();
    const n = Number(counts[key] || 0);
    const label = SCAN_FAMILY_FILTER_LABELS[key] || key;
    btn.textContent = key === "all" ? label : `${label} ${n}`;
    const isActive = key === active;
    btn.classList.toggle("is-active", isActive);
    btn.setAttribute("aria-pressed", isActive ? "true" : "false");
  });
}

function wireScanFamilyFilterOnce() {
  const host = document.getElementById("scanFamilyFilter");
  if (!host || host.dataset.filterBound === "1") return;
  host.dataset.filterBound = "1";
  host.addEventListener("click", (e) => {
    const btn = e.target?.closest?.("[data-scan-family-filter]");
    if (!btn) return;
    const key = safeText(btn.getAttribute("data-scan-family-filter")).toLowerCase();
    if (!SCAN_FAMILY_FILTERS.includes(key)) return;
    state.scanFamilyFilter = key;
    const rows = state.latestShortlistSignals?.length
      ? state.latestShortlistSignals
      : state.latestSignals;
    renderScanRows(Array.isArray(rows) ? rows : []);
  });
}

function buildTriageRowHtml({ sig, row, idx, isKept, viewKey = "data-scan-view", percentile = null, place = null }) {
  const ticker = row.ticker || row.symbol || "?";
  return `
    <td class="scan-col-ticker">${renderTickerCell(row, sig)}</td>
    <td class="scan-col-gate">${renderGateChip(sig)}</td>
    <td class="scan-col-strategy">${renderStrategyCell(row)}</td>
    <td class="scan-col-vol">${renderVolCell(row)}</td>
    <td class="scan-col-price mono-nums">${renderPriceCell(row)}</td>
    <td class="scan-col-rank">${renderRankScoreCell(row, { triage: true, percentile, place })}</td>
    <td class="scan-col-analog">${renderAnalogCell(row, { triage: true })}</td>
    <td class="scan-col-stop">${renderStopCell(row)}</td>
    <td class="scan-actions-cell">${renderScanRowActions({ idx, ticker, isKept, stageable: isScanSignalStageable(sig), hold: stageHoldReasons(sig)[0] || "", viewKey })}</td>
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
    case "strategy": {
      const attr = row?.strategy_attribution || {};
      const tf = safeText(attr.timeframe || "").toLowerCase();
      const tfRank = { intraday: "0", daily: "1", weekly: "2", monthly: "3" }[tf] || "9";
      const names = Array.isArray(attr.passed_names) ? attr.passed_names.filter(Boolean) : [];
      const label = safeText(
        names.length ? names.join(" · ") : formatStrategyLabel(attr.top_live || attr.catalog_id || "", state.scanCatalog),
      ).toLowerCase();
      return label ? `${tfRank}:${label}` : null;
    }
    case "volume_ratio":
      return optionalNum(row.volume_ratio);
    case "price":
      return optionalNum(row.price ?? row.current_price);
    case "score": {
      // Rank click follows the same score that assigns places 1, 2, 3.
      // Rows with no score stay at the bottom instead of looking like a zero.
      const researchBook = isResearchRankRow(row);
      if (!researchBook && optionalNum(row.signal_score) === null && getRankScore(row) === null) return null;
      return getDefaultBreakoutRankValue(rawSig);
    }
    case "analog_neighborhood_pf":
      return getAnalogNeighborhoodPf(row);
    case "stop":
      return getAdvisoryStopPrice(row);
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
  const researchBook = isResearchRankRow(row);
  if (researchBook) {
    // research_score / sort_score often pins at 100 for the whole book.
    // The historical blend still separates names, and staying in 0–1 keeps
    // this book under executable rows (those return 2 + score).
    const distinct = optionalNum(getRankScore(row));
    if (distinct !== null) return Math.min(Math.max(distinct / 100, 0), 1);
    const research = optionalNum(row.research_score ?? row.sort_score) ?? 0;
    return Math.min(Math.max(research / 100, 0), 1);
  }
  // Tradeable place numbers follow signal points. Older rows with no
  // signal_score fall back to the rank blend.
  const placeBasis = listPlaceBasis(row);
  const live =
    placeBasis.value !== null
      ? Math.min(Math.max(placeBasis.value / 100, 0), 1)
      : (() => {
          const score = optionalNum(getCompositeScore(row)) ?? 0;
          const pUp = optionalNum(getCalibratedPUp(row)) ?? 0;
          const conviction = optionalNum(getConvictionScore(row)) ?? 0;
          const flagged = optionalNum(row.flagged_days ?? row.days_flagged) ?? 0;
          const latestVol = optionalNum(row.latest_volume);
          const avgVol = optionalNum(row.avg_vol_50);
          const volumeRatio =
            latestVol !== null && avgVol !== null && avgVol > 0 ? latestVol / avgVol : 0;
          return (
            (Math.min(flagged, 7) / 7) * 0.32 +
            Math.min(volumeRatio / 2.0, 1.0) * 0.33 +
            Math.min(score / 100, 1.0) * 0.2 +
            Math.min(pUp, 1.0) * 0.1 +
            Math.min((conviction + 100) / 200, 1.0) * 0.05
          );
        })();
  return 2 + live;
}

/** Higher live score first. Ticker breaks ties so a price sort does not renumber the column. */
function compareLiveRankOrder(aSig, aIdx, bSig, bIdx) {
  const ra = getDefaultBreakoutRankValue(aSig);
  const rb = getDefaultBreakoutRankValue(bSig);
  if (rb !== ra) return rb - ra;
  const ta = safeText(aSig?.ticker || aSig?.symbol || "").toUpperCase();
  const tb = safeText(bSig?.ticker || bSig?.symbol || "").toUpperCase();
  if (ta !== tb) return ta < tb ? -1 : 1;
  return aIdx - bIdx;
}

/** 1-based places for one list. Higher live score is 1. Equal scores split by ticker so the column does not repeat. */
export function assignDisplayRanks(signals) {
  const rows = Array.isArray(signals) ? signals : [];
  const decorated = rows.map((sig, idx) => ({ sig, idx }));
  decorated.sort((a, b) => compareLiveRankOrder(a.sig, a.idx, b.sig, b.idx));
  const places = new Map();
  decorated.forEach((item, i) => {
    places.set(item.sig, i + 1);
  });
  return places;
}

function rememberDisplayRanks(groups) {
  const byTicker = {};
  for (const { signals, places } of groups) {
    for (const sig of signals) {
      const ticker = safeText(sig?.ticker || sig?.symbol || "").toUpperCase();
      const place = places.get(sig);
      if (!ticker || place == null || byTicker[ticker] != null) continue;
      byTicker[ticker] = place;
    }
  }
  state.scanDisplayRankByTicker = byTicker;
}

export function sortScanSignalsForRender(signals) {
  const sort = state.scanSort || { field: null, dir: "desc" };
  if (!Array.isArray(signals) || signals.length < 2) return signals;
  if (!sort.field) {
    const decorated = signals.map((sig, idx) => ({ sig, idx }));
    decorated.sort((x, y) => compareLiveRankOrder(x.sig, x.idx, y.sig, y.idx));
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

function bindScanRowKeys(rowEl, rowsRoot, open) {
  rowEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = [...(rowsRoot?.querySelectorAll("tr[data-scan-row-index]") || [])];
    const next = rows[rows.indexOf(rowEl) + (e.key === "ArrowDown" ? 1 : -1)];
    if (!next) return;
    e.preventDefault();
    next.focus();
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
  wireScanFamilyFilterOnce();
  // Always honour the active sort before rendering so re-renders triggered by
  // SSE / poll updates don't snap the operator back to backend order.
  const allSignals = sortScanSignalsForRender(Array.isArray(signalsInput) ? signalsInput : []);
  // One percentile scale for the whole scan cohort, so a row's percentile does
  // not shift when the operator filters or collapses the table.
  const rankPercentileOf = buildRankPercentileResolver(
    allSignals.map((sig) => getRankScore(normalizeScanSignal(sig))),
  );
  // Inline rationale needs a wider Rank column than the compact tooltip mode.
  const explainMode = getRankExplainMode();
  body.closest("table")?.setAttribute("data-rank-explain", explainMode);
  nearMissBody?.closest("table")?.setAttribute("data-rank-explain", explainMode);
  const statusCounts = countScanStatusBuckets(allSignals);
  const resolvedFilter = pickNonEmptyScanStatusFilter(statusCounts, getActiveScanStatusFilter());
  if (resolvedFilter !== state.scanStatusFilter) {
    state.scanStatusFilter = resolvedFilter;
  }
  updateScanStatusFilterUi(statusCounts);
  const statusFilter = getActiveScanStatusFilter();
  const familyFilter = getActiveScanFamilyFilter();
  const passSignals = allSignals.filter((sig) => scanRowTriageBucket(sig) === "pass");
  const filteredByStatus = allSignals.filter((sig) => scanRowTriageBucket(sig) === statusFilter);
  const familyCounts = { all: filteredByStatus.length, stage2: 0, pead: 0, pullback: 0 };
  for (const sig of filteredByStatus) {
    const row = normalizeScanSignal(sig);
    if (rowMatchesScanFamily(row, "stage2")) familyCounts.stage2 += 1;
    if (rowMatchesScanFamily(row, "pead")) familyCounts.pead += 1;
    if (rowMatchesScanFamily(row, "pullback")) familyCounts.pullback += 1;
  }
  updateScanFamilyFilterUi(familyCounts);
  const filteredByFamily = filteredByStatus.filter((sig) =>
    rowMatchesScanFamily(normalizeScanSignal(sig), familyFilter),
  );
  const nearMissAll = allSignals.filter((sig) => {
    if (safeText(sig?._filter_status || "kept").toLowerCase() === "kept") return false;
    return rowMatchesScanFamily(normalizeScanSignal(sig), familyFilter);
  });
  const nearMissSignals = filterSignalsByFunnelStage(nearMissAll, state.scanFunnelFilter);
  const qualifiedPlaces = assignDisplayRanks(filteredByFamily);
  const nearMissPlaces = assignDisplayRanks(nearMissSignals);
  rememberDisplayRanks([
    { signals: filteredByFamily, places: qualifiedPlaces },
    { signals: nearMissSignals, places: nearMissPlaces },
  ]);
  renderScanFunnelFilterBanner(state.lastScanDiagnostics || {});
  const expanded = Boolean(state.scanRowsExpanded);
  const signals = expanded
    ? filteredByFamily
    : filteredByFamily.slice(0, QUALIFIED_ROWS_DEFAULT_LIMIT);
  body.innerHTML = "";
  applyScanSortIndicators();
  if (qualifiedMetaEl) {
    const shown = signals.length;
    const total = filteredByFamily.length;
    const passTotal = passSignals.length;
    const suffix = total > shown ? ` (showing ${shown})` : "";
    const familyLabel = familyFilter === "all" ? "" : ` · ${SCAN_FAMILY_FILTER_LABELS[familyFilter] || familyFilter}`;
    if (statusFilter === "pass") {
      qualifiedMetaEl.textContent = `${total} tradeable name${total === 1 ? "" : "s"}${familyLabel}${suffix}`;
    } else {
      const label = SCAN_STATUS_FILTER_LABELS[statusFilter] || statusFilter;
      qualifiedMetaEl.textContent = `${total} ${label.toLowerCase()} name${total === 1 ? "" : "s"}${familyLabel}${suffix} · ${passTotal} tradeable`;
    }
  }
  if (nearMissCountEl) {
    const suffix = state.scanFunnelFilter && nearMissSignals.length !== nearMissAll.length
      ? ` (${nearMissSignals.length} match filter)`
      : "";
    nearMissCountEl.textContent = `(${nearMissAll.length}${suffix})`;
  }
  if (showMoreBtn) {
    if (filteredByFamily.length > QUALIFIED_ROWS_DEFAULT_LIMIT) {
      showMoreBtn.classList.remove("hidden");
      showMoreBtn.textContent = expanded
        ? `Show top ${QUALIFIED_ROWS_DEFAULT_LIMIT}`
        : `Show all ${filteredByFamily.length}`;
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
          percentile: rankPercentileOf(row),
          place: nearMissPlaces.get(sig) ?? null,
        });
        nearMissBody.appendChild(tr);
      });
    }
  }
  if (!signals.length) {
    const scanned = Boolean(state.lastScanAt);
    const filterLabel = SCAN_STATUS_FILTER_LABELS[statusFilter] || statusFilter;
    const topDrop = scanned ? buildDiagnosticsSummary(state.lastScanDiagnostics || {}).blockers[0] : null;
    const dropCopy = topDrop
      ? `Biggest drop-off: ${topDrop.label} (${topDrop.value}).`
      : "";
    const emptyTitle = scanned ? "Zero candidates" : "No scan yet";
    let emptySub;
    if (!scanned) {
      emptySub = "Run Scan in the top bar to load candidates.";
    } else if (statusFilter === "blocked" || statusFilter === "info") {
      const drops = scanned ? buildDiagnosticsSummary(state.lastScanDiagnostics || {}).blockers || [] : [];
      const dropLine = drops.length
        ? `Funnel drop-offs: ${drops.map((d) => `${d.label} (${d.value})`).join("; ")}.`
        : dropCopy;
      emptySub = `No ${filterLabel.toLowerCase()} names in this shortlist. ${dropLine} Open Scan funnel for stage counts.`
        .replace(/\s+/g, " ")
        .trim();
    } else if (statusFilter !== "pass") {
      emptySub = `No ${filterLabel.toLowerCase()} names in this shortlist. ${dropCopy}`.trim();
    } else if (nearMissSignals.length) {
      emptySub = `No tradeable names. ${nearMissSignals.length} near-miss row${nearMissSignals.length === 1 ? "" : "s"} below. ${dropCopy}`.trim();
    } else {
      emptySub = `This scan kept 0 tradeable names. ${dropCopy} Open the funnel to see where symbols dropped out.`.replace(/\s+/g, " ").trim();
    }
    const emptyCta = scanned
      ? `<button id="scanEmptyFunnelBtn" class="btn small secondary" type="button">See drop-offs</button>`
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
    document.getElementById("scanEmptyFunnelBtn")?.addEventListener("click", () => {
      const panel = document.getElementById("scanDiagnosticsPanel");
      if (panel) panel.open = true;
      panel?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    const keepTicker = safeText(state.selectedScanTicker || "").toUpperCase();
    const keepSig = keepTicker
      ? allSignals.find(
          (sig) => safeText(sig?.ticker || sig?.symbol || "").toUpperCase() === keepTicker,
        )
      : null;
    if (keepSig) {
      deps.highlightSelectedScanRow?.(keepTicker);
    } else {
      void deps.renderScanDetail?.(null);
    }
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
    tr.innerHTML = buildTriageRowHtml({ sig, row, idx, isKept, percentile: rankPercentileOf(row), place: qualifiedPlaces.get(sig) ?? null });
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
      if (!raw || !clearToQueue(raw)) return;
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
    // Row click (and Enter / Space) opens Evaluate setup: chart + decision brief.
    // Scan completion still does not auto-select the first row.
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
    rowEl.addEventListener("click", (e) => {
      if (e.target instanceof Element && e.target.closest("button")) return;
      openChart();
    });
    bindScanRowKeys(rowEl, body, openChart);
  });
  nearMissBody?.querySelectorAll("tr[data-scan-row-index]").forEach((rowEl) => {
    const idx = Number(rowEl.getAttribute("data-scan-row-index"));
    const openNearMiss = () => {
      const raw = nearMissLookup(idx);
      if (raw) void deps.renderScanDetail?.(normalizeScanSignal(raw));
    };
    rowEl.addEventListener("click", (e) => {
      if (e.target instanceof Element && e.target.closest("button")) return;
      openNearMiss();
    });
    rowEl.tabIndex = 0;
    bindScanRowKeys(rowEl, nearMissBody, openNearMiss);
  });
  wireScanRankWhyTooltips(body);
  if (nearMissBody) wireScanRankWhyTooltips(nearMissBody);
  deps.updateHeroInfographic?.();
}
