/**
 * Scan diagnostics rendering — the headline meta line, blocker list, and
 * pipeline funnel shown under the scan results table.
 *
 * Extracted from app.js per the module decomposition policy in
 * docs/FRONTEND_DESIGN_SYSTEM.md ("Next Planned Splits"). DOM ids consumed:
 * #scanDiagnostics, #scanBlockers, #scanFunnel, #scanBlockersChip,
 * #scanBlockersChipCount, #scanDiagnosticsPanel.
 */

import { state } from "../modules/state.js";
import { safeText, safeNum, escapeHtml } from "../modules/format.js";
import { statusClass, DIAG_LABELS } from "../modules/logger.js";
import { formatGateModeLabel } from "../modules/filterReasons.js";

export function buildScanMeta(signals = [], count = null) {
  const total = count ?? signals.length;
  const high = signals.filter((s) => (s?.advisory?.confidence_bucket || "").toLowerCase() === "high").length;
  if (high > 0) return `Found ${total} signal(s). High-confidence: ${high}.`;
  return `Found ${total} signal(s).`;
}

/**
 * One-line scan integrity summary for the Operations banner.
 * @param {object} diag
 * @param {object[]} signals Kept signals (state.latestSignals)
 * @param {object[]} shortlist Full shortlist when available
 */
export function buildScanIntegrityLine(diag = {}, signals = [], shortlist = []) {
  const kept = Array.isArray(signals) ? signals.length : 0;
  const screened =
    safeNum(diag.watchlist_size, 0) ||
    (Array.isArray(shortlist) && shortlist.length ? shortlist.length : kept);
  const scanId = safeText(diag.scan_id || "").trim();
  const idPart = scanId ? `Scan ${scanId.slice(0, 8)} · ` : "";
  const dq = safeText(diag.data_quality || "ok").toLowerCase();
  const regimeBlocked = safeNum(diag.scan_blocked, 0) > 0;
  const regimePart = regimeBlocked ? "Regime: blocked · " : "Regime: open · ";
  const vcp = formatGateModeLabel(diag.scan_vcp_gate_mode);
  const sector = formatGateModeLabel(diag.scan_sector_gate_mode);
  const quality = formatGateModeLabel(diag.quality_gates_mode);
  const gatesPart = `Filters: quality ${quality}, pattern ${vcp}, sector ${sector}`;
  const fallback = safeNum(diag.provider_fallback_count, 0) + safeNum(diag.used_fallback_data_count, 0);
  const fallbackPart = fallback > 0 ? ` · ${fallback} fallback ticker(s)` : "";
  return `${idPart}${kept} kept / ${screened} screened · Data: ${dq} · ${regimePart}${gatesPart}${fallbackPart}`;
}

export function renderScanIntegrityBanner(diag = {}, signals = [], shortlist = []) {
  const el = document.getElementById("scanIntegrityBanner");
  if (!el) return;
  const line = buildScanIntegrityLine(diag, signals, shortlist);
  el.textContent = line;
  el.classList.remove("hidden");
  const dq = safeText(diag.data_quality || "").toLowerCase();
  el.dataset.integrity = dq === "ok" && !safeNum(diag.scan_blocked, 0) ? "good" : "warn";
}

export function renderScanGateModesToolbar(diag = {}) {
  const el = document.getElementById("scanGateModesChip");
  if (!el) return;
  const quality = formatGateModeLabel(diag.quality_gates_mode);
  const vcp = formatGateModeLabel(diag.scan_vcp_gate_mode);
  el.textContent = `Active gates: quality ${quality}, VCP ${vcp}`;
  el.classList.remove("hidden");
}

/**
 * Render scan delta strip from `/api/cockpit/deltas` payload.
 * @param {object|null} delta
 */
export function renderScanDeltaStrip(delta) {
  const el = document.getElementById("scanDeltaStrip");
  if (!el) return;
  if (!delta || typeof delta !== "object") {
    el.innerHTML = "";
    el.classList.add("hidden");
    return;
  }
  const newTickers = Array.isArray(delta.new_tickers) ? delta.new_tickers : [];
  const dropped = Array.isArray(delta.dropped_tickers) ? delta.dropped_tickers : [];
  const moves = Array.isArray(delta.rank_moves) ? delta.rank_moves : [];
  const bigMoves = moves.filter((m) => Math.abs(safeNum(m?.delta, 0)) >= 10).slice(0, 5);
  if (!newTickers.length && !dropped.length && !bigMoves.length) {
    el.innerHTML = `<span class="muted">No material changes vs previous scan.</span>`;
    el.classList.remove("hidden");
    return;
  }
  const chips = [];
  if (newTickers.length) {
    chips.push(`<span class="delta-chip delta-new">+${newTickers.length} new (${escapeHtml(newTickers.slice(0, 4).join(", "))})</span>`);
  }
  if (dropped.length) {
    chips.push(`<span class="delta-chip delta-dropped">−${dropped.length} dropped (${escapeHtml(dropped.slice(0, 4).join(", "))})</span>`);
  }
  bigMoves.forEach((m) => {
    const t = escapeHtml(safeText(m.ticker || "?"));
    const d = safeNum(m.delta, 0);
    const sign = d >= 0 ? "+" : "";
    chips.push(`<span class="delta-chip delta-move">${t} rank ${sign}${d.toFixed(1)}</span>`);
  });
  el.innerHTML = chips.join("");
  el.classList.remove("hidden");
}

export function diagnosticsHeadline(diagOrSummary = null) {
  if (!diagOrSummary || typeof diagOrSummary !== "object") return "";
  const headline = safeText(diagOrSummary.headline || "").trim();
  if (headline && headline !== "—") return headline;
  const dq = safeText(diagOrSummary.data_quality || "").trim().toLowerCase();
  if (dq && dq !== "ok") {
    const rs = Array.isArray(diagOrSummary.data_quality_reasons)
      ? diagOrSummary.data_quality_reasons
      : [];
    const rtxt = rs.slice(0, 2).map((x) => safeText(x)).filter(Boolean).join("; ");
    return rtxt ? `Data quality: ${dq} — ${rtxt}.` : `Data quality: ${dq}.`;
  }
  if (safeNum(diagOrSummary.scan_blocked, 0) > 0) {
    const reason = safeText(diagOrSummary.scan_blocked_reason || "").trim();
    if (reason === "bear_regime_spy_below_200sma") {
      return "Scan blocked by regime gate: SPY is below 200 SMA.";
    }
    return "Scan blocked by active risk gates.";
  }
  return "";
}

const SIGNAL_EDGE_SHADOW_BLOCKER_KEYS = new Set([
  "rank_filter_would_drop_composite",
  "rank_filter_would_drop_rank_v2",
  "rank_filter_would_drop_signal",
  "rank_filter_would_drop_any",
  "stage2_shadow_would_filter",
  "entry_shadow_would_filter_sma50_low",
  "entry_shadow_would_filter_sma50_high",
  "entry_shadow_would_filter_breakout_buffer",
  "entry_shadow_would_filter_any",
]);

export function buildDiagnosticsSummary(diag = {}) {
  const blockers = Object.entries(diag)
    .filter(
      ([k, v]) =>
        safeNum(v, 0) > 0 &&
        !["watchlist_size"].includes(k) &&
        !SIGNAL_EDGE_SHADOW_BLOCKER_KEYS.has(k),
    )
    .map(([k, v]) => ({
      key: k,
      label: DIAG_LABELS[k] || k.replaceAll("_", " "),
      value: safeNum(v, 0),
      severity: ["exceptions", "df_empty"].includes(k) ? "error" : "warn",
    }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 5);

  // Watchlist sourcing: trust the actual watchlist_size from diagnostics.
  // Honoured sources from the backend include:
  //   - explicit_tickers_override : custom ticker list (e.g. /api/scan body)
  //   - sp1500_focused            : SIGNAL_UNIVERSE_MODE=focused (used by
  //                                 backtests / API callers; no UI trigger)
  //   - sp1500_default            : full SP1500 broad universe (Run Scan)
  // Fall back to the SP1500 default size only when diagnostics carry no
  // watchlist_size at all (e.g. before the first scan completes).
  const watchRaw = safeNum(diag.watchlist_size, 0);
  const watch = watchRaw > 0 ? watchRaw : 1500;
  const finalSignals = state.latestSignals.length;
  const funnel = buildFunnelStages(diag, watch, finalSignals);

  return { blockers, funnel };
}

/**
 * Dev-mode integrity check for the scan funnel. The hero "Open signals" KPI
 * and the candidate table both render off `state.latestSignals`; the funnel
 * counts are computed from `diagnostics`. They must reconcile at the bottom.
 *
 * If they don't, surface a single grouped console.warn instead of failing
 * silently — this is exactly the kind of contradiction the cleanup pass is
 * trying to eliminate.
 */
export function assertScanDeltasReconcile(diag, funnel, signals) {
  if (!funnel || !Array.isArray(funnel.stages)) return;
  const last = funnel.stages[funnel.stages.length - 1];
  if (!last) return;
  const rendered = Array.isArray(signals) ? signals.length : 0;
  // Only assert the *final* stage matches the rendered candidate count;
  // intermediate stages can legitimately drift due to multi-source counting.
  if (Number.isFinite(last.value) && last.value !== rendered) {
    if (typeof console !== "undefined" && console.groupCollapsed) {
      console.groupCollapsed(
        "[scan reconcile] funnel terminal stage does not match rendered signals",
      );
      console.warn(
        `funnel "${last.key || last.label}" reports ${last.value} but ${rendered} rows were rendered.`,
      );
      console.warn("diagnostics:", diag);
      console.warn("funnel:", funnel);
      console.groupEnd();
    }
  }
}

const DIAGNOSTIC_DETAIL_SKIP_KEYS = new Set([
  "data_quality",
  "data_quality_reasons",
  "rank_filter_shadow",
  "signal_edge_shadow_mode",
  "entry_timing_shadow_mode",
  ...SIGNAL_EDGE_SHADOW_BLOCKER_KEYS,
]);

function dataQualityChipClass(value) {
  const dq = safeText(value || "").trim().toLowerCase();
  if (["ok", "fresh", "healthy", "good"].includes(dq)) return "good";
  if (["stale", "conflict", "blocked", "failed", "fail"].includes(dq)) return "bad";
  if (["degraded", "partial", "unknown", "warning", "warn"].includes(dq)) return "warn";
  return "neutral";
}

function formatDiagnosticValue(value) {
  if (value === null) return "null";
  if (value === undefined) return "—";
  if (Array.isArray(value)) return value.map((item) => safeText(item)).join("; ") || "—";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return safeText(value);
    }
  }
  return safeText(value);
}

function diagnosticChipClass(key, value) {
  const normalizedKey = safeText(key || "").toLowerCase();
  const normalizedValue = safeText(value || "").toLowerCase();
  const count = safeNum(value, 0);
  if (normalizedKey === "data_quality") return dataQualityChipClass(value);
  if (normalizedKey === "scan_blocked" && count > 0) return "bad";
  if (["exceptions", "df_empty", "stage_b_exceptions"].includes(normalizedKey) && count > 0) {
    return "bad";
  }
  if (
    count > 0 &&
    (normalizedKey.includes("stale") ||
      normalizedKey.includes("no_price") ||
      normalizedKey.includes("insufficient") ||
      normalizedKey.includes("timeout") ||
      normalizedKey.includes("fallback") ||
      normalizedKey.includes("filtered"))
  ) {
    return normalizedKey.includes("stale") ? "bad" : "warn";
  }
  if (["stale", "conflict", "blocked", "fail", "failed"].some((token) => normalizedValue.includes(token))) {
    return "bad";
  }
  if (["degraded", "warning", "warn", "partial"].some((token) => normalizedValue.includes(token))) {
    return "warn";
  }
  return count > 0 ? "neutral" : "neutral";
}

function appendDiagnosticChip(chipWrap, label, value, className) {
  const chip = document.createElement("span");
  chip.className = `chip ${className || "neutral"}`;
  chip.textContent = `${label}: ${formatDiagnosticValue(value)}`;
  chipWrap.appendChild(chip);
}

export function buildFunnelStages(diag, watchlistOverride, finalCount) {
  const stage2Fail = safeNum(diag.stage2_fail, 0);
  const vcpFail = safeNum(diag.vcp_fail, 0);
  const noSectorEtf = safeNum(diag.no_sector_etf, 0);
  const sectorNotWinning = safeNum(diag.sector_not_winning, 0);
  const breakoutNotConfirmed = safeNum(diag.breakout_not_confirmed, 0);
  const exceptions = safeNum(diag.exceptions, 0);

  const stageACandidatesRaw = safeNum(diag.stage_a_candidates, 0);
  const stageAShortlistedRaw = safeNum(diag.stage_a_shortlisted, 0);
  const stageAPruned = safeNum(diag.stage_a_pruned, 0);

  const primaryProviderFiltered = safeNum(diag.primary_provider_filtered, 0);
  const stageBExceptions = safeNum(diag.stage_b_exceptions, 0);
  const stageBTimeouts = safeNum(diag.stage_b_timeouts, 0);
  const selfStudyFiltered = safeNum(diag.self_study_filtered, 0);
  const qualityGatesFiltered = safeNum(diag.quality_gates_filtered, 0);

  const vcpWouldFilter = safeNum(diag.stage_a_vcp_would_filter, 0);
  const sectorWouldFilter =
    safeNum(diag.stage_a_sector_would_filter, 0) +
    safeNum(diag.stage_a_no_sector_would_filter, 0);
  const signalEdgeShadowMode = safeText(diag.signal_edge_shadow_mode || "").toLowerCase() || null;
  const stage2ShadowWouldFilter = safeNum(diag.stage2_shadow_would_filter, 0);
  const rankFilterWouldDropAny = safeNum(diag.rank_filter_would_drop_any, 0);

  const vcpGateMode = safeText(diag.scan_vcp_gate_mode || "").toLowerCase() || null;
  const sectorGateMode = safeText(diag.scan_sector_gate_mode || "").toLowerCase() || null;
  const primaryProviderMode =
    safeText(diag.scan_primary_provider_mode || "").toLowerCase() || null;
  const qualityGatesMode = safeText(diag.quality_gates_mode || "").toLowerCase() || null;

  const nWatchlist = watchlistOverride;
  const nStage2 = Math.max(0, nWatchlist - stage2Fail);
  const nVcp = Math.max(0, nStage2 - vcpFail);
  const sectorFiltered = noSectorEtf + sectorNotWinning;
  const nSector = Math.max(0, nVcp - sectorFiltered);
  const nBreakout = Math.max(0, nSector - breakoutNotConfirmed - exceptions);
  // ``stage_a_candidates`` is the authoritative pass count when present.
  const nStageA = stageACandidatesRaw > 0 ? stageACandidatesRaw : nBreakout;
  const nAfterProvider = Math.max(0, nStageA - primaryProviderFiltered);
  const nShortlist =
    stageAShortlistedRaw > 0
      ? stageAShortlistedRaw
      : Math.max(0, nAfterProvider - stageAPruned);
  const qualityFilteredTotal =
    stageBExceptions + stageBTimeouts + selfStudyFiltered + qualityGatesFiltered;
  const nQuality = Math.max(0, nShortlist - qualityFilteredTotal);
  const topNTrimmed = Math.max(0, nQuality - finalCount);

  const watchlistSource = safeText(diag.watchlist_source || "").toLowerCase();
  const watchlistSourceLabel =
    watchlistSource === "explicit_tickers_override"
      ? "custom ticker override"
      : watchlistSource === "sp1500_focused"
        ? "SP1500 focused (smaller sample)"
        : watchlistSource === "sp1500_default"
          ? "S&P 1500 (full universe)"
          : "default universe";
  const watchlistTooltip =
    `Total tickers scanned: ${nWatchlist}. Source: ${watchlistSourceLabel}. ` +
    "Run Scan covers the S&P 1500. Use focused universe in settings to scan a smaller sample.";

  const stages = [
    {
      key: "watchlist",
      label: "Watchlist",
      value: nWatchlist,
      filtered: 0,
      tooltip: watchlistTooltip,
    },
    {
      key: "stage2",
      label: "Passed uptrend check",
      value: nStage2,
      filtered: stage2Fail,
      shadow_filtered: signalEdgeShadowMode === "shadow" ? stage2ShadowWouldFilter : 0,
      mode: stage2ShadowWouldFilter > 0 ? signalEdgeShadowMode : null,
      tooltip:
        "Symbols in a confirmed uptrend (above the long-term average with healthy trend structure). Failures did not pass the uptrend check. Shadow mode shows how many would fail tighter Stage 2 thresholds.",
    },
    {
      key: "vcp",
      label: "Passed volatility pattern",
      value: nVcp,
      filtered: vcpFail,
      shadow_filtered: vcpWouldFilter,
      mode: vcpGateMode,
      tooltip:
        "Symbols showing a volatility contraction pattern with supportive volume. In observe-only mode the filter watches but does not remove candidates; the “would filter” count shows how many it would have removed.",
    },
    {
      key: "sector",
      label: "Sector OK",
      value: nSector,
      filtered: sectorFiltered,
      shadow_filtered: sectorWouldFilter,
      mode: sectorGateMode,
      tooltip:
        "Symbols in a leading sector. Removed when sector data is missing or the sector is underperforming.",
    },
    {
      key: "stage_a",
      label: "Passed quick filter",
      value: nStageA,
      filtered: Math.max(0, nSector - nStageA),
      tooltip:
        "Final count after breakout confirmation and timing gates — candidates ready for deeper analysis.",
    },
    {
      key: "shortlist",
      label: "Shortlist (top scored)",
      value: nShortlist,
      filtered: Math.max(0, nStageA - nShortlist),
      mode: primaryProviderMode,
      tooltip:
        "Highest-scoring candidates selected for deep analysis (financial checks, earnings drift, probability scores, sentiment). Lower-ranked picks are trimmed by the shortlist cap.",
    },
    {
      key: "quality",
      label: "Quality filters",
      value: nQuality,
      filtered: qualityFilteredTotal,
      mode: qualityGatesMode,
      tooltip:
        "Survivors after deep-analysis exceptions, timeouts, minimum conviction, and quality filters (financial red flags, weak breakout volume, etc.).",
    },
    {
      key: "final",
      label: "Final signals",
      value: finalCount,
      filtered: topNTrimmed,
      tooltip:
        "Tradeable signals after the rank limit. If much smaller than after quality filters, the rank limit is trimming results.",
    },
  ];

  if (signalEdgeShadowMode === "shadow" && rankFilterWouldDropAny > 0) {
    stages.push({
      key: "rank_filter_shadow",
      label: "After rank-filter shadow",
      value: Math.max(0, finalCount - rankFilterWouldDropAny),
      filtered: rankFilterWouldDropAny,
      shadow_filtered: rankFilterWouldDropAny,
      mode: "shadow",
      tooltip:
        "Post-scan shadow rank filter (composite p50, rank v2 p70, signal p70). Would drop signals below batch quantile thresholds. Does not remove live signals.",
    });
  }

  return {
    watchlist: nWatchlist,
    stage2_pass: nStage2,
    vcp_pass: nVcp,
    final: finalCount,
    stages,
    vcp_gate_mode: vcpGateMode,
    sector_gate_mode: sectorGateMode,
    primary_provider_mode: primaryProviderMode,
    quality_gates_mode: qualityGatesMode,
  };
}

/**
 * Render the diagnostics chips, blocker list, and funnel.
 * `deps`: { updateHeroInfographic, getDisplayMode } injected by app.js
 * (same DI pattern as the other panels/*.js modules).
 */
export function renderDiagnostics(diag = {}, deps = {}) {
  const { updateHeroInfographic, getDisplayMode } = deps;
  const chipWrap = document.getElementById("scanDiagnostics");
  const blockersEl = document.getElementById("scanBlockers");
  const funnelEl = document.getElementById("scanFunnel");
  chipWrap.innerHTML = "";
  blockersEl.innerHTML = "";
  funnelEl.innerHTML = "";

  const dq = safeText(diag.data_quality || "").trim();
  if (dq) {
    const rs = Array.isArray(diag.data_quality_reasons) ? diag.data_quality_reasons : [];
    appendDiagnosticChip(
      chipWrap,
      "Data quality",
      rs.length > 0
        ? `${dq} (${rs.slice(0, 2).map((x) => safeText(x)).join("; ")})`
        : dq,
      dataQualityChipClass(dq),
    );
  }

  const shadowMode = safeText(diag.signal_edge_shadow_mode || "").toLowerCase();
  if (shadowMode === "shadow") {
    appendDiagnosticChip(
      chipWrap,
      "Rank-filter shadow",
      safeNum(diag.rank_filter_would_drop_any, 0),
      "warn",
    );
    appendDiagnosticChip(
      chipWrap,
      "Stage 2 shadow",
      safeNum(diag.stage2_shadow_would_filter, 0),
      "warn",
    );
  }
  const entryShadowMode = safeText(diag.entry_timing_shadow_mode || "").toLowerCase();
  const preflight = state.entryTimingScanPreflight;
  if (preflight?.needs_dashboard_restart) {
    appendDiagnosticChip(
      chipWrap,
      "Entry experiment env",
      "restart dashboard to load .env",
      "error",
    );
  } else if (preflight?.stale_last_scan) {
    appendDiagnosticChip(
      chipWrap,
      "Entry experiment env",
      "loaded — Run Scan to refresh counters",
      "info",
    );
  } else if (preflight?.experiment_recommended && !preflight?.experiment_env_ready) {
    const missing = (preflight.missing_env || []).slice(0, 2).join("; ");
    appendDiagnosticChip(
      chipWrap,
      "Entry experiment env",
      missing || "not configured — restart server after .env update",
      "error",
    );
  } else if (entryShadowMode === "shadow") {
    const profile = safeText(diag.entry_timing_shadow_profile || "default");
    const stage2Eval = safeNum(diag.entry_shadow_stage2_evaluated, 0);
    const stage2Drop = safeNum(diag.entry_shadow_stage2_would_filter_any, 0);
    const stage2Part =
      stage2Eval > 0 ? ` | stage2 ${stage2Drop}/${stage2Eval}` : "";
    appendDiagnosticChip(
      chipWrap,
      "Entry-timing shadow",
      `${safeNum(diag.entry_shadow_would_filter_any, 0)} stageA (${profile})${stage2Part}`,
      "warn",
    );
  } else if (preflight?.experiment_recommended && preflight?.experiment_env_ready) {
    appendDiagnosticChip(
      chipWrap,
      "Entry-timing shadow",
      "env ready — run scan to populate counters",
      "info",
    );
  }

  const summary = buildDiagnosticsSummary(diag);
  const headerChip = document.getElementById("scanBlockersChip");
  const headerChipCount = document.getElementById("scanBlockersChipCount");
  if (headerChip && !headerChip.dataset.wired) {
    headerChip.dataset.wired = "1";
    headerChip.addEventListener("click", () => {
      const panel = document.getElementById("scanDiagnosticsPanel");
      if (!panel) return;
      panel.open = true;
      headerChip.setAttribute("aria-expanded", "true");
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }
  if (!summary.blockers.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No major blockers detected.";
    blockersEl.appendChild(empty);
    if (headerChip) headerChip.classList.add("hidden");
    if (headerChipCount) headerChipCount.textContent = "0";
  } else {
    summary.blockers.forEach((b) => {
      const li = document.createElement("li");
      li.innerHTML = `${b.label}: <strong>${b.value}</strong> <span class="${statusClass(b.severity)}">${b.severity}</span>`;
      blockersEl.appendChild(li);
    });
    if (headerChip) headerChip.classList.remove("hidden");
    if (headerChipCount) headerChipCount.textContent = String(summary.blockers.length);
  }

  const stages = Array.isArray(summary.funnel.stages) ? summary.funnel.stages : [];
  const funnelVals = stages.map((s) => safeNum(s.value, 0));
  const funnelMax = Math.max(1, ...funnelVals);
  const hueStep = stages.length > 1 ? 132 / (stages.length - 1) : 0;

  stages.forEach((stage, i) => {
    const n = safeNum(stage.value, 0);
    const pct = Math.round((n / funnelMax) * 100);
    const hue = Math.round(200 - i * hueStep);
    const filtered = safeNum(stage.filtered, 0);
    const shadowFiltered = safeNum(stage.shadow_filtered, 0);
    const mode = safeText(stage.mode || "").toLowerCase();
    const tooltip = safeText(stage.tooltip || "");
    const showShadowBadge = shadowFiltered > 0 && (mode === "shadow" || mode === "soft" || mode === "off" || !mode);
    const node = document.createElement("div");
    node.className = "funnel-node";
    if (mode) node.dataset.gateMode = mode;
    if (tooltip) node.title = tooltip;
    const filteredLine =
      i === 0 || filtered <= 0
        ? ""
        : `<div class="funnel-node-filtered" title="Removed at this step">&minus;${filtered}</div>`;
    const shadowBadge = showShadowBadge
      ? `<span class="funnel-shadow-badge" title="${escapeHtml(
          `Gate is in ${mode || "shadow"} mode. Would have filtered ${shadowFiltered} more in hard mode.`,
        )}">${escapeHtml(mode || "shadow")} &middot; would-filter ${shadowFiltered}</span>`
      : "";
    node.innerHTML = `
      <div class="funnel-node-head">
        <span class="label">${escapeHtml(stage.label || stage.key || "")}</span>
        <span class="funnel-node-pct mono-nums">${pct}%</span>
      </div>
      <div class="funnel-bar-track" aria-hidden="true">
        <div class="funnel-bar-fill" style="width:${pct}%;--funnel-hue:${hue}"></div>
      </div>
      <div class="funnel-node-foot">
        <span class="value mono-nums" aria-label="pass count">${n}</span>
        ${filteredLine}
      </div>
      ${shadowBadge}
    `;
    funnelEl.appendChild(node);
  });

  Object.entries(diag)
    .filter(([key]) => !DIAGNOSTIC_DETAIL_SKIP_KEYS.has(key))
    .slice(0, 8)
    .forEach(([key, value]) => {
      appendDiagnosticChip(
        chipWrap,
        DIAG_LABELS[key] || key.replaceAll("_", " "),
        value,
        diagnosticChipClass(key, value),
      );
    });
  state.lastWatchlistSize = summary.funnel.watchlist;
  state.lastScanAt = new Date().toISOString();
  renderScanIntegrityBanner(diag, state.latestSignals, state.latestShortlistSignals);
  renderScanGateModesToolbar(diag);
  assertScanDeltasReconcile(diag, summary.funnel, state.latestSignals);
  if (typeof updateHeroInfographic === "function") updateHeroInfographic();
  const diagPanel = document.getElementById("scanDiagnosticsPanel");
  const displayMode = typeof getDisplayMode === "function" ? getDisplayMode() : "";
  if (diagPanel && displayMode === "pro") diagPanel.open = true;
  if (headerChip && diagPanel && !headerChip.dataset.boundExpand) {
    headerChip.addEventListener("click", () => {
      diagPanel.open = true;
      headerChip.setAttribute("aria-expanded", "true");
      const target = document.getElementById("scanBlockers");
      if (target && typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
    headerChip.dataset.boundExpand = "1";
  }
}
