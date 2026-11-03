/**
 * Backtest panel — owns the queue form, the result summary card, the
 * recent-runs list, the form-persistence cache (localStorage), and the
 * background-task poller for queued runs.
 *
 * Public surface:
 *   - setDefaultBacktestDates / restoreBacktestFormFromStorage /
 *     wireBacktestFormPersistence / resetBacktestFormToDefaults:
 *     form lifecycle helpers used by `wireEvents` at boot time.
 *   - syncBtUniverseRow / applyBacktestPresetYears: form interactions.
 *   - collectBacktestSpecFromForm / collectBacktestOverrides: build the
 *     payload sent to `/api/backtest-runs`. `collectBacktestSpecFromForm`
 *     was previously reused by the scan panel via `scanBodyFromBacktestSpec`
 *     (advanced scan JSON UI has been removed from the dashboard).
 *   - renderBacktestResultSummary / renderBacktestResultRaw /
 *     backtestSpecSummaryLine: shared by the panel itself and the
 *     scan-from-latest-backtest helper.
 *   - switchBacktestHubTab / refreshBacktestRuns: tab-switching + recent
 *     runs list, also called by the strategy-chat panel.
 *   - queueUserBacktest: the "Queue backtest" button in Edit settings.
 *   - queueBacktestWindow: landing 5y/8y/2y one-click (sets dates and queues).
 *
 * Injected dependencies:
 *   - `setJobProgress`: shared scan/backtest progress-bar helper.
 *   - `getDisplayMode`: controls auto-opening the raw-JSON panel for pro.
 */

import { state, BACKTEST_PREFS_KEY } from "../modules/state.js";
import { api } from "../modules/api.js";
import {
  safeText,
  escapeHtml,
  formatPercentPoints,
  formatMoney,
} from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import { humanizeKey } from "../modules/humanize.js";
import { setResearchStatusStrip } from "../modules/researchStatus.js";
import { scrollStrategyChatToEnd, renderStrategyChatMessages } from "./strategyChat.js";
import { renderBacktestEquityCharts, clearBacktestEquityCharts } from "./backtestCharts.js";
import { setAsyncState, ASYNC_ERROR } from "../modules/asyncState.js";
import { runDisplayTitle } from "./backtestLabels.js";
import { loadScanCatalog, primaryStrategyIdForTimeframe } from "./scanStudio.js";

/**
 * The backtest queue (`/api/backtest-runs*`) is served by both backends:
 * SaaS runs it on Celery workers, the local server runs it in a background
 * thread. This notice only appears on servers that predate the local queue
 * (no `backtest_transport` in public-config).
 */
export const LOCAL_BACKTEST_NOTICE =
  "This server does not expose the backtest queue. Restart the dashboard on a current " +
  "build, or run `python backtest.py` from schwab_skill/ (results are written to " +
  ".backtest_results.json and validation_artifacts/).";

export function isBacktestQueueAvailable() {
  const cfg = state.publicConfig || {};
  return Boolean(cfg.saas_mode || cfg.backtest_transport === "local_thread");
}

/** @deprecated Use isBacktestQueueAvailable — kept for any stale call sites. */
export const isHostedBacktestAvailable = isBacktestQueueAvailable;

export function normalizeBtUniverseValue(raw) {
  const v = String(raw || "").trim().toLowerCase();
  if (!v || v === "watchlist") return "sp1500";
  if (v === "tickers") return "custom";
  return v;
}

export function universeModeForPreset(preset) {
  return normalizeBtUniverseValue(preset) === "custom" ? "tickers" : "watchlist";
}

function btCatalog() {
  const live = state.scanCatalog;
  if (live && typeof live === "object" && Array.isArray(live.strategies) && live.strategies.length) {
    return live;
  }
  const el = document.getElementById("scanCatalogBootstrap");
  if (el?.textContent) {
    try {
      const parsed = JSON.parse(el.textContent);
      if (parsed && Array.isArray(parsed.strategies) && parsed.strategies.length) return parsed;
    } catch {
      /* ignore */
    }
  }
  return {
    timeframes: [{ id: "daily", display_name: "Daily", description: "Primary live engine." }],
    strategies: [
      {
        id: "trend_breakout",
        display_name: "Stage 2 / VCP breakout",
        timeframe: "daily",
        status: "live",
        runnable: true,
        origin: "iterated",
        description: "Weinstein Stage 2 plus volume contraction. This is the live book.",
      },
    ],
    universes: [
      { id: "sp1500", display_name: "S&P 1500", available: true, description: "Default live universe." },
      { id: "custom", display_name: "Custom tickers", available: true, description: "Paste your own list." },
    ],
    defaults: { timeframe: "daily", universe_preset: "sp1500", strategy_ids: ["trend_breakout"] },
  };
}

function btStatusHow(row) {
  const status = String(row?.status || "research").toLowerCase();
  if (status === "live") return "Live order-routing sleeve.";
  if (status === "live_overlay") return "Confirms the live book. Overlay, not a separate book.";
  if (status === "shadow") return "Replay uses the Scan studio evaluator. Does not go LIVE.";
  return "Paper / research screen. Replay uses the Scan studio evaluator — not live.";
}

function readBtStrategyIdsFromDom() {
  const boxes = document.querySelectorAll("#btStrategyBoard [data-bt-strategy]");
  if (!boxes.length) return [];
  return [...boxes]
    .filter((el) => el.checked && !el.disabled)
    .map((el) => String(el.getAttribute("data-bt-strategy") || "").trim().toLowerCase())
    .filter(Boolean);
}

function btPrimaryStrategyId(timeframe) {
  try {
    const id = primaryStrategyIdForTimeframe?.(timeframe);
    if (id) return String(id);
  } catch {
    /* catalog not ready */
  }
  const cat = btCatalog();
  const tf = (cat.timeframes || []).find((row) => String(row.id) === String(timeframe));
  return String(tf?.default_strategy_id || cat.defaults?.strategy_ids?.[0] || "trend_breakout");
}

export function mountBacktestComposerCatalog() {
  const cat = btCatalog();
  const hiddenUni = document.getElementById("btUniverse");
  const preset = normalizeBtUniverseValue(hiddenUni?.value || cat.defaults?.universe_preset || "sp1500");
  if (hiddenUni) hiddenUni.value = preset;
  const uniMount = document.getElementById("btUniverseMount");
  if (uniMount) {
    const rows = Array.isArray(cat.universes) ? cat.universes : [];
    uniMount.innerHTML = rows
      .filter((u) => u.available !== false)
      .map((u) => {
        const selected = u.id === preset;
        return `<label class="scan-studio-uni${selected ? " is-selected" : ""}">
          <input type="radio" name="btUniverseRadio" value="${escapeHtml(u.id)}" ${selected ? "checked" : ""} />
          <span class="scan-studio-uni-name">${escapeHtml(u.display_name || u.id)}</span>
          <span class="scan-studio-uni-desc">${escapeHtml(u.description || "")}</span>
        </label>`;
      })
      .join("");
  }
  const tfHidden = document.getElementById("btScanTimeframe");
  let tf = String(tfHidden?.value || cat.defaults?.timeframe || "daily").toLowerCase();
  const frames = Array.isArray(cat.timeframes) ? cat.timeframes : [];
  if (!frames.some((row) => String(row.id) === tf) && frames.length) tf = String(frames[0].id);
  if (tfHidden) tfHidden.value = tf;
  const tfMount = document.getElementById("btTimeframeMount");
  if (tfMount) {
    tfMount.innerHTML = frames
      .map((row) => {
        const id = String(row.id || "");
        const active = id === tf;
        return `<button type="button" class="scan-studio-tf${active ? " is-active" : ""}" data-bt-timeframe="${escapeHtml(id)}" role="tab" aria-selected="${active ? "true" : "false"}">${escapeHtml(row.display_name || id)}</button>`;
      })
      .join("");
  }
  mountBtStrategyBoard(tf);
  syncBtUniverseRow();
  renderBacktestQueueNowSummary();
}

function mountBtStrategyBoard(timeframe) {
  const board = document.getElementById("btStrategyBoard");
  if (!board) return;
  const cat = btCatalog();
  const tf = String(timeframe || "daily");
  const rows = (Array.isArray(cat.strategies) ? cat.strategies : []).filter(
    (s) => String(s.timeframe || "") === tf,
  );
  const allowed = new Set(rows.map((row) => String(row.id || "")));
  const selected = new Set(readBtStrategyIdsFromDom().filter((id) => allowed.has(id)));
  if (!selected.size) {
    const stored = String(board.dataset.selected || "")
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter((id) => allowed.has(id));
    stored.forEach((id) => selected.add(id));
  }
  const primary = btPrimaryStrategyId(tf);
  if (!selected.size && primary && allowed.has(primary)) selected.add(primary);
  const yours = rows.filter((row) => String(row.origin || "iterated") !== "literature");
  const paper = rows.filter((row) => String(row.origin || "") === "literature");
  const renderCard = (row) => {
    const id = String(row.id || "");
    const checked = selected.has(id);
    const disabled = row.available === false || row.runnable === false;
    const status = String(row.status || "research").toLowerCase();
    const mods = [
      checked ? "is-selected" : "",
      disabled ? "is-disabled" : "",
      id === primary ? "scan-studio-strategy--primary" : "",
      String(row.origin || "") === "literature" ? "scan-studio-strategy--paper" : "scan-studio-strategy--yours",
      status === "live" || status === "live_overlay" ? "scan-studio-strategy--live" : "",
    ]
      .filter(Boolean)
      .join(" ");
    return `<label class="scan-studio-strategy ${mods}">
      <input class="scan-studio-strategy-input" type="checkbox" data-bt-strategy="${escapeHtml(id)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
      <span class="scan-studio-strategy-copy">
        <span class="scan-studio-strategy-head">
          <strong>${escapeHtml(row.display_name || id)}</strong>
          <span class="scan-studio-selected-flag">Selected</span>
        </span>
        <span class="scan-studio-strategy-desc">${escapeHtml(row.description || "")}</span>
        <span class="muted small scan-studio-strategy-how">${escapeHtml(btStatusHow(row))}</span>
      </span>
    </label>`;
  };
  const group = (title, hint, list, extra) => {
    if (!list.length) return "";
    return `<fieldset class="scan-studio-group${extra ? ` ${extra}` : ""}">
      <legend class="scan-studio-group-head"><span class="scan-studio-kicker">${escapeHtml(title)}</span>
        ${hint ? `<span class="muted small scan-studio-group-hint">${escapeHtml(hint)}</span>` : ""}</legend>
      <div class="scan-studio-strategies">${list.map(renderCard).join("")}</div>
    </fieldset>`;
  };
  board.innerHTML = `${group("Your strategies", "Check a row to include it in this replay.", yours, "")}
    ${group("Paper screens", "Literature screens — opt-in only, not the live book.", paper, "scan-studio-group--paper")}`;
  board.dataset.selected = [...selected].join(",");
}

export async function refreshBacktestComposerCatalog() {
  try {
    await loadScanCatalog();
  } catch {
    /* bootstrap catalog is enough */
  }
  mountBacktestComposerCatalog();
}

export function setDefaultBacktestDates() {
  const startEl = document.getElementById("btStart");
  const endEl = document.getElementById("btEnd");
  if (startEl?.value && endEl?.value) return;
  const end = new Date();
  const start = new Date();
  start.setFullYear(end.getFullYear() - 5);
  const fmt = (d) => d.toISOString().slice(0, 10);
  if (startEl && !startEl.value) startEl.value = fmt(start);
  if (endEl && !endEl.value) endEl.value = fmt(end);
}

export function restoreBacktestFormFromStorage() {
  try {
    const raw = localStorage.getItem(BACKTEST_PREFS_KEY);
    if (!raw) return false;
    const o = JSON.parse(raw);
    if (!o || typeof o !== "object") return false;
    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (!el || val === undefined || val === null) return;
      el.value = String(val);
    };
    setVal("btUniverse", normalizeBtUniverseValue(o.universe));
    setVal("btTickers", o.tickers);
    setVal("btTheory", o.theory);
    setVal("btStart", o.start);
    setVal("btEnd", o.end);
    setVal("btScanTimeframe", o.timeframe || "daily");
    if (Array.isArray(o.strategyIds)) {
      const board = document.getElementById("btStrategyBoard");
      if (board) board.dataset.selected = o.strategyIds.map((id) => String(id || "").toLowerCase()).join(",");
    }
    setVal("btStage252w", o.stage252w);
    setVal("btStage2SmaDays", o.stage2SmaDays);
    setVal("btVcpDays", o.vcpDays);
    setVal("btVcpGateMode", o.vcpGateMode);
    setVal("btQualityMinScore", o.qualityMinScore);
    setVal("btQualityVolRatio", o.qualityVolRatio);
    setVal("btQualityRequireVol", o.qualityRequireVol);
    setVal("btSlippage", o.slippage);
    setVal("btFeeShare", o.feeShare);
    setVal("btMinFee", o.minFee);
    setVal("btMaxAdv", o.maxAdv);
    setVal("btStartEquity", o.startEquity);
    setVal("btMaxPositions", o.maxPositions);
    setVal("btRiskPerTradePct", o.riskPerTradePct);
    setVal("btPositionSizePct", o.positionSizePct);
    setVal("btQualityGates", o.qualityGates);
    setVal("btBreakoutConfirm", o.breakoutConfirm);
    setVal("btForensicMode", o.forensicMode);
    setVal("btPead", o.pead);
    setVal("btAdaptiveStopEnabled", o.adaptiveStopEnabled);
    setVal("btAdaptiveStopBasePct", o.adaptiveStopBasePct);
    setVal("btAdaptiveGuardrailsEnabled", o.adaptiveGuardrailsEnabled);
    setVal("btMetaPolicyMode", o.metaPolicyMode);
    setVal("btEventRiskMode", o.eventRiskMode);
    setVal("btExitManagerMode", o.exitManagerMode);
    setVal("btExecQualityMode", o.execQualityMode);
    const skip = document.getElementById("btSkipMirofish");
    if (skip && typeof o.skipMirofish === "boolean") skip.checked = o.skipMirofish;
    renderBacktestQueueNowSummary();
    return true;
  } catch {
    return false;
  }
}

export function snapshotBacktestFormForStorage() {
  return {
    universe: document.getElementById("btUniverse")?.value ?? "",
    tickers: document.getElementById("btTickers")?.value ?? "",
    theory: document.getElementById("btTheory")?.value ?? "",
    start: document.getElementById("btStart")?.value ?? "",
    end: document.getElementById("btEnd")?.value ?? "",
    timeframe: document.getElementById("btScanTimeframe")?.value ?? "daily",
    strategyIds: readBtStrategyIdsFromDom(),
    stage252w: document.getElementById("btStage252w")?.value ?? "",
    stage2SmaDays: document.getElementById("btStage2SmaDays")?.value ?? "",
    vcpDays: document.getElementById("btVcpDays")?.value ?? "",
    vcpGateMode: document.getElementById("btVcpGateMode")?.value ?? "",
    qualityMinScore: document.getElementById("btQualityMinScore")?.value ?? "",
    qualityVolRatio: document.getElementById("btQualityVolRatio")?.value ?? "",
    qualityRequireVol: document.getElementById("btQualityRequireVol")?.value ?? "",
    slippage: document.getElementById("btSlippage")?.value ?? "",
    feeShare: document.getElementById("btFeeShare")?.value ?? "",
    minFee: document.getElementById("btMinFee")?.value ?? "",
    maxAdv: document.getElementById("btMaxAdv")?.value ?? "",
    startEquity: document.getElementById("btStartEquity")?.value ?? "",
    maxPositions: document.getElementById("btMaxPositions")?.value ?? "",
    riskPerTradePct: document.getElementById("btRiskPerTradePct")?.value ?? "",
    positionSizePct: document.getElementById("btPositionSizePct")?.value ?? "",
    qualityGates: document.getElementById("btQualityGates")?.value ?? "",
    breakoutConfirm: document.getElementById("btBreakoutConfirm")?.value ?? "",
    forensicMode: document.getElementById("btForensicMode")?.value ?? "",
    pead: document.getElementById("btPead")?.value ?? "",
    adaptiveStopEnabled: document.getElementById("btAdaptiveStopEnabled")?.value ?? "",
    adaptiveStopBasePct: document.getElementById("btAdaptiveStopBasePct")?.value ?? "",
    adaptiveGuardrailsEnabled: document.getElementById("btAdaptiveGuardrailsEnabled")?.value ?? "",
    metaPolicyMode: document.getElementById("btMetaPolicyMode")?.value ?? "",
    eventRiskMode: document.getElementById("btEventRiskMode")?.value ?? "",
    exitManagerMode: document.getElementById("btExitManagerMode")?.value ?? "",
    execQualityMode: document.getElementById("btExecQualityMode")?.value ?? "",
    skipMirofish: Boolean(document.getElementById("btSkipMirofish")?.checked),
  };
}

let _backtestPersistTimer = null;
export function schedulePersistBacktestForm() {
  if (_backtestPersistTimer) clearTimeout(_backtestPersistTimer);
  _backtestPersistTimer = setTimeout(() => {
    _backtestPersistTimer = null;
    try {
      localStorage.setItem(BACKTEST_PREFS_KEY, JSON.stringify(snapshotBacktestFormForStorage()));
    } catch {
      /* quota */
    }
  }, 400);
}

export function wireBacktestFormPersistence() {
  const root = document.getElementById("backtestSection");
  if (!root) return;
  root.addEventListener("input", schedulePersistBacktestForm);
  root.addEventListener("change", schedulePersistBacktestForm);
  if (root.dataset.btComposerBound === "1") return;
  root.dataset.btComposerBound = "1";
  root.addEventListener("change", (ev) => {
    const t = ev.target;
    if (!t) return;
    if (t.name === "btUniverseRadio") {
      const hidden = document.getElementById("btUniverse");
      if (hidden) hidden.value = normalizeBtUniverseValue(t.value);
      syncBtUniverseRow();
      mountBacktestComposerCatalog();
      return;
    }
    if (t.hasAttribute?.("data-bt-strategy")) {
      const board = document.getElementById("btStrategyBoard");
      if (board) board.dataset.selected = readBtStrategyIdsFromDom().join(",");
      t.closest(".scan-studio-strategy")?.classList.toggle("is-selected", Boolean(t.checked));
    }
    renderBacktestQueueNowSummary();
  });
  root.addEventListener("click", (ev) => {
    const btn = ev.target?.closest?.("[data-bt-timeframe]");
    if (!btn) return;
    const tf = String(btn.getAttribute("data-bt-timeframe") || "").toLowerCase();
    if (!tf) return;
    const hidden = document.getElementById("btScanTimeframe");
    if (hidden) hidden.value = tf;
    mountBacktestComposerCatalog();
    schedulePersistBacktestForm();
    renderBacktestQueueNowSummary();
  });
}

export function resetBacktestFormToDefaults() {
  localStorage.removeItem(BACKTEST_PREFS_KEY);
  const u = document.getElementById("btUniverse");
  if (u) u.value = "sp1500";
  const tick = document.getElementById("btTickers");
  if (tick) tick.value = "";
  const th = document.getElementById("btTheory");
  if (th) th.value = "";
  const tf = document.getElementById("btScanTimeframe");
  if (tf) tf.value = "daily";
  const board = document.getElementById("btStrategyBoard");
  if (board) board.dataset.selected = "trend_breakout";
  const s = document.getElementById("btStart");
  const e = document.getElementById("btEnd");
  if (s) s.value = "";
  if (e) e.value = "";
  setDefaultBacktestDates();
  const slip = document.getElementById("btSlippage");
  if (slip) slip.value = "15";
  const fee = document.getElementById("btFeeShare");
  if (fee) fee.value = "0.005";
  const minf = document.getElementById("btMinFee");
  if (minf) minf.value = "1";
  const adv = document.getElementById("btMaxAdv");
  if (adv) adv.value = "0.02";
  const startEq = document.getElementById("btStartEquity");
  if (startEq) startEq.value = "100000";
  const maxPos = document.getElementById("btMaxPositions");
  if (maxPos) maxPos.value = "10";
  const riskPct = document.getElementById("btRiskPerTradePct");
  if (riskPct) riskPct.value = "0.0075";
  const sizePct = document.getElementById("btPositionSizePct");
  if (sizePct) sizePct.value = "0.05";
  const asBase = document.getElementById("btAdaptiveStopBasePct");
  if (asBase) asBase.value = "0.07";
  ["btQualityGates", "btBreakoutConfirm", "btForensicMode", "btPead"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  [
    "btAdaptiveStopEnabled",
    "btAdaptiveGuardrailsEnabled",
    "btMetaPolicyMode",
    "btEventRiskMode",
    "btExitManagerMode",
    "btExecQualityMode",
    "btVcpGateMode",
    "btQualityRequireVol",
  ].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  ["btStage252w", "btStage2SmaDays", "btVcpDays", "btQualityMinScore", "btQualityVolRatio"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  const skip = document.getElementById("btSkipMirofish");
  if (skip) skip.checked = false;
  mountBacktestComposerCatalog();
  syncBtUniverseRow();
  setBtMetaMessage("Form reset to defaults. Queue when ready.");
  logEvent({ kind: "system", severity: "info", message: "Backtest form reset." });
}

export function setBacktestQueueUiBusy(busy) {
  const spin = document.getElementById("btMetaSpinner");
  const metaText = document.getElementById("btMetaText");
  if (spin) spin.classList.toggle("hidden", !busy);
  if (metaText && busy && !metaText.dataset.sticky) metaText.textContent = "Queueing…";
  document.querySelectorAll("[data-bt-queue-years], #btQueueNowBtn, #btQueueBtn, #btQueueCopyBtn, #btQueueUseIndexBtn").forEach((btn) => {
    if (btn) btn.disabled = Boolean(busy);
  });
}

export function setBtMetaMessage(text, { sticky = false } = {}) {
  const metaText = document.getElementById("btMetaText");
  if (metaText) {
    metaText.textContent = text;
    if (sticky) metaText.dataset.sticky = "1";
    else delete metaText.dataset.sticky;
  }
  const land = document.getElementById("btQueueNowStatus");
  if (land) land.textContent = text || "";
}

export function syncBtUniverseRow() {
  const sel = document.getElementById("btUniverse");
  const row = document.getElementById("btTickersRow");
  if (!row) return;
  const preset = normalizeBtUniverseValue(sel?.value || "sp1500");
  row.classList.toggle("hidden", preset !== "custom");
}

export function applyBacktestPresetYears(years) {
  const end = new Date();
  const start = new Date();
  start.setFullYear(end.getFullYear() - Number(years));
  const fmt = (d) => d.toISOString().slice(0, 10);
  const startEl = document.getElementById("btStart");
  const endEl = document.getElementById("btEnd");
  if (startEl) startEl.value = fmt(start);
  if (endEl) endEl.value = fmt(end);
  setBtMetaMessage(`Date range set to last ${years} year(s).`);
  schedulePersistBacktestForm();
  renderBacktestQueueNowSummary();
}

export function collectBacktestOverrides() {
  const o = {};
  const setOnOffBool = (id, key) => {
    const raw = document.getElementById(id)?.value || "";
    if (raw === "on") o[key] = true;
    if (raw === "off") o[key] = false;
  };
  const setMode = (id, key) => {
    const raw = document.getElementById(id)?.value || "";
    if (raw) o[key] = raw;
  };
  const setNumber = (id, key) => {
    const raw = document.getElementById(id)?.value;
    if (raw === undefined || raw === null || raw === "") return;
    const num = Number(raw);
    if (Number.isFinite(num)) o[key] = num;
  };
  const q = document.getElementById("btQualityGates")?.value || "";
  if (q) o.quality_gates_mode = q;
  setOnOffBool("btBreakoutConfirm", "breakout_confirm_enabled");
  setOnOffBool("btPead", "pead_enabled");
  setOnOffBool("btAdaptiveStopEnabled", "adaptive_stop_enabled");
  setOnOffBool("btAdaptiveGuardrailsEnabled", "backtest_adaptive_guardrails_enabled");
  setMode("btMetaPolicyMode", "meta_policy_mode");
  setMode("btEventRiskMode", "event_risk_mode");
  setMode("btExitManagerMode", "exit_manager_mode");
  setMode("btExecQualityMode", "exec_quality_mode");
  setNumber("btStartEquity", "backtest_portfolio_starting_equity");
  setNumber("btMaxPositions", "backtest_portfolio_max_positions");
  setNumber("btRiskPerTradePct", "backtest_risk_per_trade_pct");
  setNumber("btPositionSizePct", "backtest_position_size_pct");
  setNumber("btAdaptiveStopBasePct", "adaptive_stop_base_pct");
  setNumber("btStage252w", "stage2_52w_pct");
  setNumber("btStage2SmaDays", "stage2_sma_upward_days");
  setNumber("btVcpDays", "vcp_days");
  setNumber("btQualityMinScore", "quality_min_signal_score");
  setNumber("btQualityVolRatio", "quality_breakout_volume_min_ratio");
  setOnOffBool("btQualityRequireVol", "quality_require_breakout_volume");
  setMode("btVcpGateMode", "scan_vcp_gate_mode");
  if (document.getElementById("btSkipMirofish")?.checked) o.skip_mirofish = true;
  const fm = document.getElementById("btForensicMode")?.value || "";
  if (fm === "disabled") o.forensic_enabled = false;
  else if (fm === "shadow" || fm === "soft" || fm === "hard") {
    o.forensic_enabled = true;
    o.forensic_filter_mode = fm;
  } else if (fm === "off") {
    o.forensic_enabled = true;
    o.forensic_filter_mode = "off";
  }
  return Object.keys(o).length ? o : null;
}

export function collectBacktestSpecFromForm() {
  const theory = document.getElementById("btTheory")?.value?.trim() || "";
  const preset = normalizeBtUniverseValue(document.getElementById("btUniverse")?.value || "sp1500");
  const custom = preset === "custom";
  const tickersRaw = document.getElementById("btTickers")?.value || "";
  const tickers = tickersRaw
    .split(/[\s,]+/)
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
  const start = document.getElementById("btStart")?.value;
  const end = document.getElementById("btEnd")?.value;
  const slip = Number(document.getElementById("btSlippage")?.value);
  const fee = Number(document.getElementById("btFeeShare")?.value);
  const minf = Number(document.getElementById("btMinFee")?.value);
  const adv = Number(document.getElementById("btMaxAdv")?.value);
  const timeframe = String(document.getElementById("btScanTimeframe")?.value || "daily").toLowerCase();
  let strategyIds = readBtStrategyIdsFromDom();
  if (!strategyIds.length) {
    const primary = btPrimaryStrategyId(timeframe);
    if (primary) strategyIds = [primary];
  }
  const spec = {
    schema_version: 1,
    universe_mode: custom ? "tickers" : "watchlist",
    universe_preset: preset,
    tickers: custom ? tickers : [],
    scan_timeframe: timeframe,
    strategy_ids: strategyIds,
    start_date: start,
    end_date: end,
  };
  if (theory) spec.theory_name = theory;
  if (Number.isFinite(slip)) spec.slippage_bps_per_side = slip;
  if (Number.isFinite(fee)) spec.fee_per_share = fee;
  if (Number.isFinite(minf)) spec.min_fee_per_order = minf;
  if (Number.isFinite(adv)) spec.max_adv_participation = adv;
  const ov = collectBacktestOverrides();
  if (ov) spec.overrides = ov;
  return spec;
}

const PF_MEAN_GATE = 1.2;

function coercePf(value) {
  if (value === "inf" || value === Infinity) return Infinity;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function formatPf(value) {
  const n = coercePf(value);
  if (n === Infinity) return "∞";
  if (n === null) return "—";
  return n.toFixed(2);
}

function pfTone(value) {
  const n = coercePf(value);
  if (n === null) return "neutral";
  if (n >= PF_MEAN_GATE) return "ok";
  if (n >= 1) return "warn";
  return "bad";
}

function renderBacktestPfGate(result) {
  const pf = result?.profit_factor_net;
  const n = coercePf(pf);
  const gateLine =
    n === null
      ? "This run did not report net profit factor."
      : n >= PF_MEAN_GATE
        ? "This single window clears mean PF ≥ 1.20. Worst-era PF is a multi-era check — not LIVE-ready by itself."
        : "Promotion gate is mean PF ≥ 1.20. This is one window, not a multi-era verdict.";
  return `<div class="bt-pf-gate" data-tone="${pfTone(pf)}">
    <div class="bt-pf-gate-value">PF ${formatPf(pf)}</div>
    <p class="bt-pf-gate-note">${escapeHtml(gateLine)}</p>
  </div>`;
}

export function renderBacktestRunCompare(rows) {
  const box = document.getElementById("btRunCompare");
  if (!box) return;
  const completed = (Array.isArray(rows) ? rows : [])
    .filter((r) => r && r.result_summary && typeof r.result_summary === "object")
    .slice(0, 3);
  if (!completed.length) {
    box.innerHTML = "";
    return;
  }
  const body = completed
    .map((r) => {
      const sum = r.result_summary;
      const created = safeText(r.created_at || "").slice(0, 16);
      const spec = runDisplayTitle(r, state.scanCatalog) || backtestSpecSummaryLine(r.spec);
      return `<tr>
        <td>${escapeHtml(created)}</td>
        <td>${escapeHtml(spec || "—")}</td>
        <td class="mono-nums" data-tone="${pfTone(sum.profit_factor_net)}">${formatPf(sum.profit_factor_net)}</td>
        <td class="mono-nums">${formatPercentPoints(sum.win_rate_net, 1)}</td> <!-- points from backtest.py 100*wins/total -->
        <td class="mono-nums">${formatPercentPoints(sum.total_return_net_pct, 2)}</td> <!-- points from backtest.py -->
        <td class="mono-nums">${formatPercentPoints(sum.max_drawdown_net_pct, 2)}</td> <!-- signed points from backtest.py -->
      </tr>`;
    })
    .join("");
  box.innerHTML = `
    <p class="muted small">Last ${completed.length} completed run${completed.length === 1 ? "" : "s"} — chronological, not ranked. PF is per-window.</p>
    <div class="table-wrap">
      <table class="bt-run-compare-table">
        <caption class="visually-hidden">Recent backtest runs</caption>
        <thead>
          <tr>
            <th>When</th>
            <th>Setup</th>
            <th>PF</th>
            <th>Win</th>
            <th>Return</th>
            <th>Max DD</th>
          </tr>
        </thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}

export function renderBacktestResultSummary(result) {
  const box = document.getElementById("btResultSummary");
  const chartWrap = document.getElementById("btEquityChartWrap");
  if (!box) return;
  if (!result || typeof result !== "object") {
    box.innerHTML = "";
    clearBacktestEquityCharts();
    setResearchStatusStrip(
      "backtestStatusStrip",
      "empty",
      "No backtest result selected.",
      "Queue a run or refresh recent runs to inspect evidence.",
    );
    return;
  }
  const tt = result.total_trades;
  if (tt === undefined || tt === null) {
    box.innerHTML = "";
    clearBacktestEquityCharts();
    setResearchStatusStrip(
      "backtestStatusStrip",
      "partial",
      "Backtest result incomplete.",
      "Result returned without a trade count. Re-queue or pick another finished run.",
    );
    return;
  }
  const findings = typeof result.findings === "string" ? result.findings : "";
  const port = result.portfolio_summary && typeof result.portfolio_summary === "object" ? result.portfolio_summary : {};
  const extra = [];
  if (port.ending_equity != null && Number.isFinite(Number(port.ending_equity))) {
    extra.push(
      `<div class="bt-metric"><div class="bt-metric-label">Ending equity</div><div class="bt-metric-value">${formatMoney(port.ending_equity)}</div></div>`,
    );
  }
  if (port.peak_concurrent != null && Number.isFinite(Number(port.peak_concurrent))) {
    extra.push(
      `<div class="bt-metric"><div class="bt-metric-label">Peak concurrent</div><div class="bt-metric-value">${safeText(port.peak_concurrent)}</div></div>`,
    );
  }
  box.innerHTML = `
    <div class="bt-metric-grid bt-metric-grid-extra">
      <div class="bt-metric"><div class="bt-metric-label">Win rate (net)</div><div class="bt-metric-value">${formatPercentPoints(result.win_rate_net, 1)}</div></div>
      <div class="bt-metric"><div class="bt-metric-label">CAGR (net)</div><div class="bt-metric-value">${formatPercentPoints(result.cagr_net_pct, 2)}</div></div>
      ${extra.join("")}
    </div>
    ${renderBacktestPfGate(result)}
    ${findings ? `<div class="bt-findings">${escapeHtml(findings)}</div>` : ""}
  `;
  renderBacktestEquityCharts(chartWrap, result);
  setResearchStatusStrip(
    "backtestStatusStrip",
    "success",
    "Backtest result ready.",
    `PF ${formatPf(result.profit_factor_net)} · Trades ${safeText(tt)} · Win ${formatPercentPoints(result.win_rate_net, 1)} · Return ${formatPercentPoints(result.total_return_net_pct, 2)}.`,
  );
}

export function renderBacktestResultRaw() {
  // Hub briefing is the operator view. Raw JSON dump removed.
}

export function backtestSpecSummaryLine(spec) {
  if (!spec || typeof spec !== "object") return "";
  const cat = btCatalog();
  const preset = normalizeBtUniverseValue(spec.universe_preset || spec.universe_mode || "sp1500");
  const uni = (cat.universes || []).find((u) => String(u.id) === preset);
  const uniLabel =
    spec.universe_mode === "tickers" || preset === "custom"
      ? "Custom tickers"
      : uni?.display_name || humanizeKey(preset);
  const ids = Array.isArray(spec.strategy_ids) ? spec.strategy_ids : [];
  const stratNames = ids
    .map((id) => {
      const row = (cat.strategies || []).find((s) => String(s.id) === String(id));
      return row?.display_name || humanizeKey(id);
    })
    .filter(Boolean);
  const stratPart = stratNames.length ? ` · ${stratNames.slice(0, 2).join(", ")}${stratNames.length > 2 ? " +" : ""}` : "";
  const tfRaw = String(spec.scan_timeframe || "daily").toLowerCase();
  const tfPart = tfRaw && tfRaw !== "daily" ? ` · ${humanizeKey(tfRaw)}` : "";
  const dr = spec.start_date && spec.end_date ? `${safeText(spec.start_date)} → ${safeText(spec.end_date)}` : "";
  const n = Array.isArray(spec.tickers) ? spec.tickers.length : 0;
  const tickPart = (spec.universe_mode === "tickers" || preset === "custom") && n ? ` · ${n} names` : "";
  return `${uniLabel}${tickPart}${stratPart}${tfPart}${dr ? ` · ${dr}` : ""}`;
}

export function switchBacktestHubTab(which) {
  const formTab = document.getElementById("btHubTabForm");
  const chatTab = document.getElementById("btHubTabChat");
  const formPanel = document.getElementById("btHubPanelForm");
  const chatPanel = document.getElementById("strategyChatPanel");
  const isForm = which === "form";
  if (formTab && chatTab) {
    formTab.classList.toggle("tab-btn-active", isForm);
    chatTab.classList.toggle("tab-btn-active", !isForm);
    formTab.setAttribute("aria-selected", isForm ? "true" : "false");
    chatTab.setAttribute("aria-selected", isForm ? "false" : "true");
  }
  if (formPanel) formPanel.classList.toggle("hidden", !isForm);
  if (chatPanel) chatPanel.classList.toggle("hidden", isForm);
  if (!isForm) {
    renderStrategyChatMessages();
    scrollStrategyChatToEnd();
  }
}

export async function refreshBacktestRuns() {
  const list = document.getElementById("btRunList");
  if (!list) return;
  if (!isBacktestQueueAvailable()) {
    list.innerHTML = `<li class="muted">${escapeHtml(LOCAL_BACKTEST_NOTICE)}</li>`;
    renderBacktestRunCompare([]);
    setResearchStatusStrip(
      "backtestStatusStrip",
      "partial",
      "Hosted queue unavailable locally.",
      "Run local backtest scripts to produce validation artifacts.",
    );
    return;
  }
  list.innerHTML = `<li role="status" aria-label="Loading backtest runs">
    <span class="ol-skeleton"></span>
    <span class="ol-skeleton" style="width:88%"></span>
    <span class="ol-skeleton" style="width:76%"></span>
  </li>`;
  setResearchStatusStrip(
    "backtestStatusStrip",
    "loading",
    "Loading recent backtest runs.",
    "Fetching hosted run history and queue status.",
  );
  const out = await api.get("/api/backtest-runs?limit=15");
  if (!out.ok) {
    renderBacktestRunCompare([]);
    setAsyncState(list, ASYNC_ERROR, {
      message: safeText(out.user_message || out.error || "Request failed."),
      onRetry: () => void refreshBacktestRuns(),
    });
    setResearchStatusStrip(
      "backtestStatusStrip",
      "error",
      "Backtest run list unavailable.",
      safeText(out.user_message || out.error || "Request failed."),
    );
    return;
  }
  const rows = Array.isArray(out.data) ? out.data : [];
  if (!rows.length) {
    list.innerHTML = `<li class="muted">No backtests yet.</li>`;
    renderBacktestRunCompare([]);
    setResearchStatusStrip(
      "backtestStatusStrip",
      "empty",
      "No hosted backtests yet.",
      "Choose settings and queue a run to create evidence.",
    );
    return;
  }
  renderBacktestRunCompare(rows);
  list.innerHTML = rows
    .map((r) => {
      const tid = r.celery_task_id ? `${safeText(r.celery_task_id).slice(0, 12)}…` : "—";
      const specLine = backtestSpecSummaryLine(r.spec);
      const sum = r.result_summary && typeof r.result_summary === "object" ? r.result_summary : null;
      let metrics = "";
      if (sum) {
        metrics = `<div class="bt-run-metrics">
          PF ${formatPf(sum.profit_factor_net)} · Trades ${safeText(sum.total_trades)} · Win ${formatPercentPoints(sum.win_rate_net, 1)} ·
          Return ${formatPercentPoints(sum.total_return_net_pct, 2)} · CAGR ${formatPercentPoints(sum.cagr_net_pct, 2)} ·
          Max DD ${formatPercentPoints(sum.max_drawdown_net_pct, 2)}
        </div>`;
        if (sum.findings_preview) {
          metrics += `<div class="muted" style="margin-top:4px;font-size:0.78rem">${escapeHtml(sum.findings_preview)}</div>`;
        }
      } else if (r.error_message) {
        metrics = `<div class="bt-run-metrics muted">${safeText(r.error_message)}</div>`;
      }
      return `<li class="bt-run-item"><strong>${safeText(r.status)}</strong> · task ${tid} · ${safeText(r.created_at)}
        <div class="bt-run-spec">${safeText(specLine)}</div>${metrics}</li>`;
    })
    .join("");
  setResearchStatusStrip(
    "backtestStatusStrip",
    "success",
    `${rows.length} recent backtest run${rows.length === 1 ? "" : "s"}.`,
    "Review completed runs for PF, win rate, return, and drawdown.",
  );
}

export async function pollBacktestTask(taskId, { setJobProgress = () => {}, getDisplayMode = () => "balanced" } = {}) {
  const t0 = Date.now();
  setJobProgress("btJobProgress", "btJobProgressLabel", 0.08, "Queued…");
  for (let i = 0; i < 120; i++) {
    const elapsed = Math.floor((Date.now() - t0) / 1000);
    setBtMetaMessage(`Running… ${elapsed}s · waiting for worker`, { sticky: true });
    const st = await api.get(`/api/backtest-runs/tasks/${encodeURIComponent(taskId)}`, { timeoutMs: 120000 });
    if (!st.ok) {
      setBtMetaMessage(`Status poll failed: ${st.error}`, { sticky: true });
      setResearchStatusStrip(
        "backtestStatusStrip",
        "error",
        "Backtest status poll failed.",
        safeText(st.error || "Request failed."),
      );
      return;
    }
    const d = st.data || {};
    const celery = safeText(d.celery_status || "").toLowerCase();
    setBtMetaMessage(`Running… ${elapsed}s · status: ${celery} · saved: ${safeText(d.db_status || "—")}`, { sticky: true });
    const progFrac =
      celery === "pending" || celery === "received"
        ? 0.15
        : celery === "started" || celery === "retry"
          ? 0.5 + Math.min(0.45, (i / 120) * 0.45)
          : 0.2;
    setJobProgress("btJobProgress", "btJobProgressLabel", progFrac, `${celery} · ${elapsed}s`);
    if (celery === "success" && d.result) {
      renderBacktestResultSummary(d.result);
      setBtMetaMessage("Complete. Briefing and charts are ready.", { sticky: true });
      setJobProgress("btJobProgress", "btJobProgressLabel", 1, "Complete");
      await refreshBacktestRuns();
      window.dispatchEvent(new CustomEvent("backtest_run_updated"));
      return;
    }
    if (celery === "failure" || celery === "revoked") {
      renderBacktestResultSummary(null);
      setBtMetaMessage("Run finished with an error.", { sticky: true });
      setResearchStatusStrip(
        "backtestStatusStrip",
        "error",
        "Backtest run failed.",
        safeText(d.error_message || d.exc_message || "Worker reported a failure. Retry from the queue."),
      );
      setJobProgress("btJobProgress", "btJobProgressLabel", 0, "");
      await refreshBacktestRuns();
      window.dispatchEvent(new CustomEvent("backtest_run_updated"));
      return;
    }
    if (d.db_status === "failed" && d.error_message) {
      renderBacktestResultSummary(null);
      setBtMetaMessage(safeText(d.error_message), { sticky: true });
      setResearchStatusStrip(
        "backtestStatusStrip",
        "error",
        "Backtest run failed.",
        safeText(d.error_message),
      );
      setJobProgress("btJobProgress", "btJobProgressLabel", 0, "");
      await refreshBacktestRuns();
      window.dispatchEvent(new CustomEvent("backtest_run_updated"));
      return;
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
  setBtMetaMessage("Still running; use Refresh list or check back later.", { sticky: true });
  setResearchStatusStrip(
    "backtestStatusStrip",
    "partial",
    "Backtest still running.",
    "Use Refresh list or check back later for completed evidence.",
  );
  setJobProgress("btJobProgress", "btJobProgressLabel", 0.9, "Still running…");
}

let _queuePostInFlight = false;

export function applySpecToForm(spec) {
  if (!spec || typeof spec !== "object") return;
  const u = document.getElementById("btUniverse");
  if (u) u.value = normalizeBtUniverseValue(spec.universe_preset || spec.universe_mode || "sp1500");
  const tick = document.getElementById("btTickers");
  if (tick && Array.isArray(spec.tickers)) tick.value = spec.tickers.join(", ");
  const th = document.getElementById("btTheory");
  if (th && spec.theory_name != null) th.value = String(spec.theory_name);
  const tf = document.getElementById("btScanTimeframe");
  if (tf && spec.scan_timeframe) tf.value = String(spec.scan_timeframe);
  if (Array.isArray(spec.strategy_ids)) {
    const board = document.getElementById("btStrategyBoard");
    if (board) board.dataset.selected = spec.strategy_ids.map((id) => String(id || "").toLowerCase()).join(",");
  }
  if (spec.start_date) {
    const s = document.getElementById("btStart");
    if (s) s.value = String(spec.start_date).slice(0, 10);
  }
  if (spec.end_date) {
    const e = document.getElementById("btEnd");
    if (e) e.value = String(spec.end_date).slice(0, 10);
  }
  const setNum = (id, val) => {
    const el = document.getElementById(id);
    if (el && val != null && val !== "") el.value = String(val);
  };
  setNum("btSlippage", spec.slippage_bps_per_side);
  setNum("btFeeShare", spec.fee_per_share);
  setNum("btMinFee", spec.min_fee_per_order);
  setNum("btMaxAdv", spec.max_adv_participation);
  const ov = spec.overrides && typeof spec.overrides === "object" ? spec.overrides : {};
  setNum("btStartEquity", ov.backtest_portfolio_starting_equity);
  setNum("btMaxPositions", ov.backtest_portfolio_max_positions);
  setNum("btRiskPerTradePct", ov.backtest_risk_per_trade_pct);
  setNum("btPositionSizePct", ov.backtest_position_size_pct);
  setNum("btAdaptiveStopBasePct", ov.adaptive_stop_base_pct);
  setNum("btStage252w", ov.stage2_52w_pct);
  setNum("btStage2SmaDays", ov.stage2_sma_upward_days);
  setNum("btVcpDays", ov.vcp_days);
  setNum("btQualityMinScore", ov.quality_min_signal_score);
  setNum("btQualityVolRatio", ov.quality_breakout_volume_min_ratio);
  const setSel = (id, val) => {
    const el = document.getElementById(id);
    if (el && val != null && val !== "") el.value = String(val);
  };
  setSel("btQualityGates", ov.quality_gates_mode);
  setSel("btVcpGateMode", ov.scan_vcp_gate_mode);
  if (ov.quality_require_breakout_volume === true) setSel("btQualityRequireVol", "on");
  if (ov.quality_require_breakout_volume === false) setSel("btQualityRequireVol", "off");
  if (ov.breakout_confirm_enabled === true) setSel("btBreakoutConfirm", "on");
  if (ov.breakout_confirm_enabled === false) setSel("btBreakoutConfirm", "off");
  if (ov.pead_enabled === true) setSel("btPead", "on");
  if (ov.pead_enabled === false) setSel("btPead", "off");
  const skip = document.getElementById("btSkipMirofish");
  if (skip && Object.prototype.hasOwnProperty.call(ov, "skip_mirofish")) {
    skip.checked = Boolean(ov.skip_mirofish);
  }
  mountBacktestComposerCatalog();
  syncBtUniverseRow();
  schedulePersistBacktestForm();
  renderBacktestQueueNowSummary();
}

export function openBacktestComposer() {
  const details = document.querySelector("#backtestSection .bt-composer-details");
  if (details) details.open = true;
  document.getElementById("backtestSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function previewBacktestWindowYears(years) {
  const y = Number(years);
  const end = new Date();
  const start = new Date();
  start.setFullYear(end.getFullYear() - (Number.isFinite(y) && y > 0 ? y : 5));
  const fmt = (d) => d.toISOString().slice(0, 10);
  return { start: fmt(start), end: fmt(end) };
}

export function extrasFromQueueButton(btn) {
  if (!btn) return { years: null, extras: {} };
  const years = btn.getAttribute("data-bt-queue-years");
  const extras = {};
  const uni = btn.getAttribute("data-bt-queue-universe");
  if (uni) extras.universe = uni;
  const strat = btn.getAttribute("data-bt-queue-strategy");
  if (strat) extras.strategyIds = strat.split(/[\s,]+/).filter(Boolean);
  const stage = btn.getAttribute("data-bt-queue-stage252w");
  if (stage) extras.stage252w = stage;
  const skip = btn.getAttribute("data-bt-queue-skip-mirofish");
  if (skip === "1" || skip === "true") extras.skipMirofish = true;
  const theory = btn.getAttribute("data-bt-queue-theory");
  if (theory) extras.theory = theory;
  return { years, extras };
}

export function renderBacktestQueueNowSummary() {
  const el = document.getElementById("btQueueNowSummary");
  const warn = document.getElementById("btQueueNowWarn");
  const resetBtn = document.getElementById("btQueueUseIndexBtn");
  const hint = document.getElementById("btQueueNowHint");
  if (!el) return;
  const spec = collectBacktestSpecFromForm();
  const withoutDates = { ...spec, start_date: "", end_date: "" };
  const who = backtestSpecSummaryLine(withoutDates) || "current index and strategies";
  const five = previewBacktestWindowYears(5);
  const eight = previewBacktestWindowYears(8);
  el.textContent = `Ready: ${who}. Queue last 5 years uses ${five.start} → ${five.end}. Queue last 8 years uses ${eight.start} → ${eight.end}.`;
  const custom = spec.universe_mode === "tickers" || spec.universe_preset === "custom";
  const weekly = String(spec.scan_timeframe || "daily").toLowerCase() !== "daily";
  const n = Array.isArray(spec.tickers) ? spec.tickers.length : 0;
  const parts = [];
  if (custom) {
    parts.push(
      n
        ? `Custom tickers (${n} name${n === 1 ? "" : "s"}) are selected. Pick an index before queueing a long window.`
        : "Custom tickers are selected but none are listed. Pick an index under Edit settings.",
    );
  }
  if (weekly) {
    parts.push("Horizon is not daily. Switch to daily under Edit settings for the usual Stage 2 replay.");
  }
  if (warn) {
    warn.hidden = parts.length === 0;
    warn.textContent = parts.join(" ");
  }
  if (resetBtn) resetBtn.classList.toggle("hidden", parts.length === 0);
  if (hint && !state.publicConfig?.saas_mode) {
    hint.textContent = "Same index and strategy as Edit settings. One replay at a time.";
  }
}

export function applyNamedIndexQueueDefaults() {
  const tick = document.getElementById("btTickers");
  if (tick) tick.value = "";
  applySpecToForm({
    universe_mode: "watchlist",
    universe_preset: "sp1500",
    tickers: [],
    scan_timeframe: "daily",
    strategy_ids: ["trend_breakout"],
    overrides: { skip_mirofish: true },
  });
  const th = document.getElementById("btTheory");
  if (th) th.value = "";
  renderBacktestQueueNowSummary();
  setBtMetaMessage("Reset to S&P 1500 · Stage 2 / VCP · daily. Click Queue last 5 years or Queue last 8 years.");
}

export async function queueBacktestWindow(
  years,
  extras = {},
  { setJobProgress = () => {}, getDisplayMode = () => "balanced" } = {},
) {
  const y = Number(years);
  if (!Number.isFinite(y) || y < 1) {
    setBtMetaMessage("Pick a window of 2, 5, or 8 years.");
    return;
  }
  if (extras.universe || extras.strategyIds || extras.stage252w != null) {
    const patch = {};
    if (extras.universe) {
      patch.universe_preset = extras.universe;
      patch.universe_mode = extras.universe === "custom" ? "tickers" : "watchlist";
      if (extras.universe !== "custom") patch.tickers = [];
    }
    if (Array.isArray(extras.strategyIds) && extras.strategyIds.length) {
      patch.strategy_ids = extras.strategyIds;
    }
    if (extras.scanTimeframe) patch.scan_timeframe = extras.scanTimeframe;
    if (extras.stage252w != null) {
      patch.overrides = { ...(patch.overrides || {}), stage2_52w_pct: Number(extras.stage252w) };
    }
    applySpecToForm(patch);
  }
  applyBacktestPresetYears(y);
  const skipEl = document.getElementById("btSkipMirofish");
  if (skipEl && (extras.skipMirofish === true || (extras.skipMirofish !== false && y >= 5))) {
    skipEl.checked = true;
  }
  const th = document.getElementById("btTheory");
  if (th && extras.theory) th.value = extras.theory;
  else if (th && /^last\s+\d+\s+years?$/i.test(String(th.value || "").trim())) th.value = "";
  schedulePersistBacktestForm();
  renderBacktestQueueNowSummary();
  await queueUserBacktest({ setJobProgress, getDisplayMode });
}

export async function queueSpec(spec, { setJobProgress = () => {}, getDisplayMode = () => "balanced" } = {}) {
  if (_queuePostInFlight) {
    setBtMetaMessage("Already queueing a run. Wait a moment.");
    return;
  }
  if (!isBacktestQueueAvailable()) {
    setBtMetaMessage(LOCAL_BACKTEST_NOTICE, { sticky: true });
    return;
  }
  if (!spec || !spec.start_date || !spec.end_date) {
    setBtMetaMessage("Choose start and end dates.");
    return;
  }
  _queuePostInFlight = true;
  setBacktestQueueUiBusy(true);
  setBtMetaMessage("Queueing…", { sticky: true });
  try {
    const out = await api.post("/api/backtest-runs", { spec }, { timeoutMs: 120000 });
    if (!out.ok) {
      setBtMetaMessage(safeText(out.error), { sticky: true });
      setResearchStatusStrip(
        "backtestStatusStrip",
        "error",
        "Backtest queue failed.",
        safeText(out.user_message || out.error || "Request failed."),
      );
      logEvent({ kind: "system", severity: "error", message: `Backtest queue failed: ${out.error}` });
      return;
    }
    const taskId = out.data?.task_id;
    setBtMetaMessage(taskId ? `Queued. Tracking task ${safeText(taskId).slice(0, 14)}…` : "Queued.", { sticky: true });
    setResearchStatusStrip(
      "backtestStatusStrip",
      "loading",
      "Backtest queued.",
      taskId ? `Tracking task ${safeText(taskId).slice(0, 14)}…` : "Waiting for hosted run status.",
    );
    logEvent({ kind: "system", severity: "info", message: "Backtest queued." });
    const runId = out.data?.run_id || taskId || "";
    window.dispatchEvent(new CustomEvent("backtest_run_updated", { detail: { runId } }));
    if (taskId) void pollBacktestTask(taskId, { setJobProgress, getDisplayMode });
    else void refreshBacktestRuns();
  } finally {
    _queuePostInFlight = false;
    setBacktestQueueUiBusy(false);
  }
}

export async function queueUserBacktest({ setJobProgress = () => {}, getDisplayMode = () => "balanced" } = {}) {
  if (!isBacktestQueueAvailable()) {
    setBtMetaMessage(LOCAL_BACKTEST_NOTICE, { sticky: true });
    setResearchStatusStrip(
      "backtestStatusStrip",
      "partial",
      "Hosted queue unavailable locally.",
      "Run local backtest scripts to produce validation artifacts.",
    );
    return;
  }
  const start = document.getElementById("btStart")?.value;
  const end = document.getElementById("btEnd")?.value;
  if (!start || !end) {
    setBtMetaMessage("Choose start and end dates.");
    setResearchStatusStrip(
      "backtestStatusStrip",
      "empty",
      "Choose dates first.",
      "Start and end dates are required before queueing a run.",
    );
    return;
  }
  const spec = collectBacktestSpecFromForm();
  if (spec.universe_mode === "tickers" && (!spec.tickers || !spec.tickers.length)) {
    setBtMetaMessage("Add at least one ticker, or pick a named index.");
    setResearchStatusStrip(
      "backtestStatusStrip",
      "empty",
      "Add tickers first.",
      "Custom ticker mode requires at least one symbol.",
    );
    return;
  }
  await queueSpec(spec, { setJobProgress, getDisplayMode });
}
