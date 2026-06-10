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

export function buildScanMeta(signals = [], count = null) {
  const total = count ?? signals.length;
  const high = signals.filter((s) => (s?.advisory?.confidence_bucket || "").toLowerCase() === "high").length;
  if (high > 0) return `Found ${total} signal(s). High-confidence: ${high}.`;
  return `Found ${total} signal(s).`;
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

export function buildDiagnosticsSummary(diag = {}) {
  const blockers = Object.entries(diag)
    .filter(([k, v]) => safeNum(v, 0) > 0 && !["watchlist_size"].includes(k))
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
        ? "SP1500 focused (SIGNAL_UNIVERSE_MODE=focused)"
        : watchlistSource === "sp1500_default"
          ? "SP1500 default (broad universe)"
          : "default universe";
  const watchlistTooltip =
    `Total tickers actually scanned: ${nWatchlist}. Source: ${watchlistSourceLabel}. ` +
    "Run Scan covers the full SP1500 (S&P 500 + 400 + 600). Set SIGNAL_UNIVERSE_MODE=focused in .env to narrow to a sample.";

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
      label: "Passed Stage 2",
      value: nStage2,
      filtered: stage2Fail,
      tooltip:
        "Tickers in a confirmed Stage 2 uptrend (above 30-week SMA, proper trend structure). Failures: stage2_fail.",
    },
    {
      key: "vcp",
      label: "Passed VCP",
      value: nVcp,
      filtered: vcpFail,
      shadow_filtered: vcpWouldFilter,
      mode: vcpGateMode,
      tooltip:
        "Tickers showing volatility-contraction-pattern volume. In shadow mode the VCP gate observes but does not filter; the would-filter count shows how many it would have removed.",
    },
    {
      key: "sector",
      label: "Sector OK",
      value: nSector,
      filtered: sectorFiltered,
      shadow_filtered: sectorWouldFilter,
      mode: sectorGateMode,
      tooltip:
        "Tickers in a winning sector ETF. Filtered by no_sector_etf + sector_not_winning when the sector gate is hard.",
    },
    {
      key: "stage_a",
      label: "Stage A Candidates",
      value: nStageA,
      filtered: Math.max(0, nSector - nStageA),
      tooltip:
        "Final Stage A pass count after breakout confirmation, exceptions, and timed gates. Sourced from stage_a_candidates.",
    },
    {
      key: "shortlist",
      label: "Shortlist (top-scored)",
      value: nShortlist,
      filtered: Math.max(0, nStageA - nShortlist),
      mode: primaryProviderMode,
      tooltip:
        "Top-scored Stage A candidates picked for Stage B enrichment (forensic, PEAD, advisory, MiroFish). Lower-scored picks are pruned by the shortlist cap.",
    },
    {
      key: "quality",
      label: "Quality Gates",
      value: nQuality,
      filtered: qualityFilteredTotal,
      mode: qualityGatesMode,
      tooltip:
        "Survivors of Stage B exceptions, timeouts, self-study min conviction, and quality gates (forensic, weak breakout volume, etc.).",
    },
    {
      key: "final",
      label: "Final Signals",
      value: finalCount,
      filtered: topNTrimmed,
      tooltip:
        "Tradeable signals returned after the top-N rank cap. If much smaller than Quality Gates, the cap (TOP_N) is trimming output.",
    },
  ];

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
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent =
      rs.length > 0
        ? `Data quality: ${dq} (${rs.slice(0, 2).map((x) => safeText(x)).join("; ")})`
        : `Data quality: ${dq}`;
    chipWrap.appendChild(chip);
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

  Object.entries(diag).slice(0, 8).forEach(([key, value]) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${DIAG_LABELS[key] || key}: ${value}`;
    chipWrap.appendChild(chip);
  });
  state.lastWatchlistSize = summary.funnel.watchlist;
  state.lastScanAt = new Date().toISOString();
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
