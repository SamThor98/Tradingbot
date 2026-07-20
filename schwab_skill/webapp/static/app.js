/**
 * Dashboard orchestrator. The big render functions, panel-specific helpers,
 * and the bootstrap IIFE live here. Cleanly-separable concerns have been
 * pulled into ./modules/*.js — see [[static-module-layout]] in the wiki for
 * the map of what lives where.
 */

import {
  state,
  UI_VIEW_MODE_KEY,
  AUTH_TOKEN_KEY,
  LEGACY_AUTH_TOKEN_KEYS,
  BACKTEST_PREFS_KEY,
} from "./modules/state.js";
import {
  safeText,
  escapeHtml,
  safeNum,
  formatMoney,
  formatDecimal,
  pct,
  formatPercentPoints,
  verdictFromScore,
  timeAgo,
  durationSec,
  formatCount,
  formatStrategyLabel,
} from "./modules/format.js";
import { normalizeScanSignal, signalFromScanResultRow } from "./modules/scanSignals.js";
import {
  configureScanTable,
  renderScanRows,
  bindScanSortHandlers,
  applyRankExplainModeSelection,
  setRankExplainMode,
} from "./panels/scanTable.js";
import {
  configureApproveDialog,
  openApproveDialog,
  syncApproveDialogGuardrails,
  approveTradeById,
} from "./panels/approveDialog.js";
import { api, ensureApiKeyOnLoad } from "./modules/api.js";
import {
  applyFreshness,
  markUnavailable,
  clearUnavailable,
  FRESHNESS_BUDGETS_SEC,
} from "./modules/freshness.js";
import { retryGet } from "./modules/asyncState.js";
import {
  authSessionReady,
  markAuthReady,
  normalizeUserJwt,
  getApiAccessToken,
  clearLegacyApiJwtKeys,
  readStoredApiJwt,
  clearStoredApiJwt,
  ensureCookieAuthSession,
  createCookieAuthSession,
  clearCookieAuthSession,
  persistApiJwtFromSession,
  updateSupabaseAuthUI,
  setSupabaseClient,
  SUPABASE_ESM,
  isProbablyAccessJwt,
  JWT_BAD_SHAPE_HINT,
  hasVerifiedEmailOnce,
  authActionLabel,
} from "./modules/auth.js";
import { showToast, addNotification, setupNotifications } from "./modules/notifications.js";
import { setupScrollToTop } from "./modules/scrollToTop.js";
import {
  clearOAuthQueryParams,
  handleRouteHash,
  installRouter,
  isSupabaseAuthCallbackHash,
} from "./modules/router.js";
import {
  attachVerifyCooldownButton,
  requestVerificationEmail,
  wireManualJwtBlock,
} from "./modules/authPresentation.js";
import {
  initPriorityFeed,
  pushPriorityItem,
  removePriorityItem,
  isPriorityFeedActive,
  getTopPriorityItem,
} from "./modules/priorityFeed.js";
import { initFeatureFlags, isFlagEnabled } from "./modules/featureFlags.js";
import {
  setupCommandPalette,
  openCommandPalette,
  closeCommandPalette,
} from "./modules/commandPalette.js";
import { setupKeyboardShortcuts } from "./modules/shortcuts.js";
import {
  logEvent,
  updateActionCenter,
  updateActivityBadge,
  statusClass,
  sentimentTagClass,
  healthBadgeClass,
  healthBadgeStateClass,
  setStatusPill,
  DIAG_LABELS,
} from "./modules/logger.js";
import {
  renderTwoFaPanel,
  refreshTwoFaStatus,
  submitEnableLiveTrading as _submitEnableLiveTradingPanel,
} from "./panels/twoFa.js";
import {
  renderOnboardingCards,
  refreshOnboarding as _refreshOnboardingPanel,
} from "./panels/onboarding.js";
import {
  renderCalibrationPanel,
  refreshCalibration,
  submitTradingHaltSave as _submitTradingHaltSavePanel,
} from "./panels/calibration.js";
import { refreshShadowScoreboard } from "./panels/shadowScoreboard.js";
import { refreshReviewLoop, runReviewBackfill } from "./panels/reviewLoop.js";
import {
  mapRecovery,
  openTradeDrawer,
} from "./panels/tradeDrawer.js";
import { refreshSectors } from "./panels/sectors.js";
import { refreshMovers } from "./panels/movers.js";
import {
  renderQuickCheckCard,
  quickCheck,
  renderTickerChart,
} from "./panels/quickCheck.js";
// Quick-view, decision-card, and recovery have been merged into the
// unified slide-in trade drawer (see imports above).
import {
  refreshPortfolio as _refreshPortfolioPanel,
} from "./panels/portfolio.js";
import {
  loadPortfolioRiskDashboard,
  wirePortfolioSubtabs,
} from "./panels/portfolioRisk.js";
import {
  applySecCompareMode,
  renderSecAnalysisCard,
  toReadableDeltaLabel,
  buildNarrativeSummary,
  renderSecCompareEmpty,
  renderSecCompareVisual as _renderSecCompareVisualPanel,
  buildFallbackSecCompare,
  runSecCompare as _runSecComparePanel,
  resetSecCompareProfileOverride,
  wireSecCompareActions,
} from "./panels/sec.js";
import {
  renderReportTabs,
  renderReportVisual,
  applyReportViewMode,
  runReport,
} from "./panels/report.js";
import {
  runResearchDossier,
  downloadResearchDossier,
  downloadResearchFundamentalWorkbook,
  loadResearchDossierPreflight,
} from "./panels/dossier.js";
import {
  PRESET_SETTING_LABELS,
  presetSettingLabel,
  renderProfilePanel,
  renderPresetApplyPreview,
  loadProfiles,
  applyProfile,
} from "./panels/profile.js";
import {
  renderPerformancePanel as _renderPerformancePanel,
  renderChallengerPanel,
  renderEvolvePanel,
  refreshPerformance as _refreshPerformancePanel,
} from "./panels/performance.js";
import {
  setDefaultBacktestDates,
  restoreBacktestFormFromStorage,
  wireBacktestFormPersistence,
  resetBacktestFormToDefaults,
  setBacktestQueueUiBusy,
  setBtMetaMessage,
  syncBtUniverseRow,
  applyBacktestPresetYears,
  collectBacktestOverrides,
  collectBacktestSpecFromForm,
  renderBacktestResultSummary,
  renderBacktestResultRaw as _renderBacktestResultRawPanel,
  backtestSpecSummaryLine,
  switchBacktestHubTab,
  refreshBacktestRuns,
  pollBacktestTask as _pollBacktestTaskPanel,
  queueUserBacktest as _queueUserBacktestPanel,
} from "./panels/backtest.js";
import {
  strategyChatPayloadMessages,
  scrollStrategyChatToEnd,
  renderStrategyChatMessages,
  hideScQueueCallout,
  showScQueueCallout as _showScQueueCalloutPanel,
  sendStrategyChat as _sendStrategyChatPanel,
} from "./panels/strategyChat.js";
import { renderValidationRecentSteps } from "./modules/validationView.js";
import { setSystemStatusStrip } from "./modules/systemStatus.js";
import { setOperationsStatusStrip } from "./modules/operationsStatus.js";
import { renderSignalTrustRow } from "./modules/signalTrustRow.js";
import { updateKanbanLaneSummaries } from "./modules/kanbanLaneSummaries.js";
import { updateWorkflowKanban } from "./modules/workflowKanban.js";
import {
  syncScanDetailBriefState,
  syncScanDetailPanelState,
  syncScanSectionState,
} from "./modules/operationsPanelState.js";
import { renderOperationsPanelSnapshot } from "./modules/operationsPanelSnapshot.js";
import { paintSystemPanelSnapshot } from "./modules/systemPanelContract.js";
import {
  setHealthRibbonUnavailable,
  setHealthRibbonTiles,
  renderHealthRibbonSummary,
  prioritizeActionCenterFromHealth,
} from "./panels/healthRibbon.js";
import {
  buildScanMeta,
  diagnosticsHeadline,
  renderScanDeltaStrip,
  renderStaleScanBanner,
  renderDiagnostics as _renderDiagnosticsPanel,
} from "./panels/scanDiagnostics.js";
import { refreshPendingBoard as _refreshPendingBoardPanel } from "./panels/pendingBoard.js";
import {
  optionalNum,
  getCompositeScore,
  getConvictionScore,
  getCalibratedPUp,
  getReliabilityScore,
  getRankScore,
  isReliabilityEstimated,
  getEdgeScore,
  getExecutionScore,
  getEv10d,
  formatConfidenceLabel,
} from "./modules/signalScores.js";
import { formatFilterReasons } from "./modules/filterReasons.js";
import {
  isScanSignalStageable,
  renderSignalProvenanceChip,
} from "./modules/signalProvenance.js";
import {
  initResearchTabs,
  applyResearchTab,
  DEFAULT_RESEARCH_TAB,
} from "./modules/researchTabs.js";
import {
  applyScanDetailOverlays,
  renderChartOverlayLegend,
} from "./modules/chartOverlays.js";
import { getLightweightChartsProps } from "./modules/chartThemeAdapters.js";
import {
  renderDecisionDashboard,
  renderDecisionDashboardLoading,
  renderDecisionDashboardUnavailable,
} from "./panels/decisionDashboard.js";
import { initCockpitPanel, primeCockpitPanel } from "./panels/cockpit.js";
import { createOperationsController } from "./screens/operations.js";
import { createResearchController } from "./screens/research.js";
import { createCockpitController } from "./screens/cockpit.js";
import { createDiagnosticsController } from "./screens/diagnostics.js";
import { createSettingsController } from "./screens/settings.js";

// Thin wrappers preserve the call signatures used by `wireEvents`,
// `connectSSE`, `runLazyApi`, etc. without leaking the panel-module
// dependency-injection contract into every call site.
const submitEnableLiveTrading = () =>
  _submitEnableLiveTradingPanel({ refreshAccountMe, refreshPending });
const refreshOnboarding = async () => {
  await _refreshOnboardingPanel({ runLazyApi });
  updateSettingsSummaryLanding();
};
const submitTradingHaltSave = () =>
  _submitTradingHaltSavePanel({ refreshAccountMe });
const refreshPortfolio = async () => {
  await _refreshPortfolioPanel({ runScan });
  updateResearchSummaryLanding();
};
const renderSecCompareVisual = (data) =>
  _renderSecCompareVisualPanel(data, { getDisplayMode });
const runSecCompare = () => _runSecComparePanel({ getDisplayMode });
const refreshPerformance = () => _refreshPerformancePanel({ getDisplayMode });
const renderPerformancePanel = (rootEl, data, opts = {}) =>
  _renderPerformancePanel(rootEl, data, { ...opts, getDisplayMode });
const renderBacktestResultRaw = (result, fallbackText) =>
  _renderBacktestResultRawPanel(result, fallbackText, { getDisplayMode });
const pollBacktestTask = (taskId) =>
  _pollBacktestTaskPanel(taskId, { setJobProgress, getDisplayMode });
const queueUserBacktest = () =>
  _queueUserBacktestPanel({ setJobProgress, getDisplayMode });
const showScQueueCallout = (taskId, runId) =>
  _showScQueueCalloutPanel(taskId, runId, { switchBacktestHubTab });
const sendStrategyChat = () =>
  _sendStrategyChatPanel({ refreshBacktestRuns, switchBacktestHubTab });
function handleFunnelStageClick(stageKey) {
  const next = state.scanFunnelFilter === stageKey ? null : stageKey;
  state.scanFunnelFilter = next;
  const rows = state.latestShortlistSignals?.length ? state.latestShortlistSignals : state.latestSignals;
  renderScanRows(Array.isArray(rows) ? rows : []);
  if (state.lastScanDiagnostics) {
    _renderDiagnosticsPanel(state.lastScanDiagnostics, {
      updateHeroInfographic,
      getDisplayMode,
      onFunnelStageClick: handleFunnelStageClick,
      activeFunnelStage: state.scanFunnelFilter,
      updateKanbanLaneSummaries,
    });
  }
  const nearMissPanel = document.getElementById("nearMissPanel");
  if (next && nearMissPanel) nearMissPanel.open = true;
}

const renderDiagnostics = (diag) => {
  state.lastScanDiagnostics = diag && typeof diag === "object" ? diag : null;
  _renderDiagnosticsPanel(diag, {
    updateHeroInfographic,
    getDisplayMode,
    onFunnelStageClick: handleFunnelStageClick,
    activeFunnelStage: state.scanFunnelFilter,
    updateKanbanLaneSummaries,
  });
  void refreshScanDeltas();
};
const refreshPending = () =>
  _refreshPendingBoardPanel({
    openApproveDialog,
    updateHeroInfographic,
    updateTodaySummaryLanding,
    trackFunnelMilestoneOnce,
    FUNNEL_EVENTS,
    getDisplayMode,
  });

const lazyLoaded = {
  portfolio: false,
  sectors: false,
  movers: false,
  performance: false,
  backtest: false,
  onboarding: false,
  profiles: false,
  calibration: false,
  shadowScoreboard: false,
  reviewLoop: false,
};

// Lazy-keyed panels grouped by owning screen. `refreshAll` re-runs only the
// visible screen's panels plus panels the user has already loaded; everything
// else stays deferred for IntersectionObserver / first-expand loading.
const SCREEN_LAZY_KEYS = Object.freeze({
  operations: [],
  research: ["portfolio", "sectors", "movers", "performance", "backtest"],
  diagnostics: ["calibration", "shadowScoreboard", "reviewLoop"],
  settings: ["onboarding", "profiles"],
});
let _ablationCyclePollTimer = null;
let _lastAblationRunStatus = "idle";

const SCREEN_MODES = Object.freeze(["operations", "research", "diagnostics", "settings"]);
const SCREEN_ALIASES = Object.freeze({
  cockpit: "research",
  today: "operations",
  system: "diagnostics",
});
const SCREEN_CONTEXT = Object.freeze({
  operations: {
    title: "Today",
    text: "Scan, review candidates, and stage only tradeable setups — nothing else.",
    ctaLabel: "Run a scan",
    ctaHref: "#scanSection",
    altCtaLabel: "Review pending",
    altCtaHref: "#pendingSection",
  },
  research: {
    title: "Research",
    text: "Triage holdings first, then dig into a name (Brief/Deep), then lab backtests — one focused surface at a time.",
    ctaLabel: "Open portfolio",
    ctaHref: "#portfolioSection",
    altCtaLabel: "Quick check",
    altCtaHref: "#quickCheckSection",
  },
  diagnostics: {
    title: "System",
    text: "Health, validation, and readiness — verify reliability before it impacts execution.",
    ctaLabel: "Health tiles",
    ctaHref: "#healthRibbon",
    altCtaLabel: "Detailed status",
    altCtaHref: "#statusDetailsPanel",
  },
  settings: {
    title: "Settings",
    text: "Link Schwab, control live orders from the overview, and adjust risk presets when you need finer tuning.",
    ctaLabel: "Connect Schwab",
    ctaHref: "#onboardingSection",
    altCtaLabel: "Live order controls",
    altCtaHref: "#settingsSummaryGuardrails",
  },
  cockpit: {
    title: "One glance, full picture.",
    text: "Market regime, ranked opportunities, portfolio risk, and the execution blotter in a single view with provenance on every lane.",
    ctaLabel: "Opportunities",
    ctaHref: "#laneOpportunities",
    altCtaLabel: "Execution blotter",
    altCtaHref: "#laneBlotter",
  },
});
const SCREEN_NUDGE_KEY_PREFIX = "tradingbot.ui.screen_seen.";
const FEATURE_GUIDE_SEEN_KEY = "tradingbot.ui.feature_guide_seen";
const SCREEN_SECTIONS = Object.freeze({
  operations: [
    "dashboardToday",
    "todaySummaryLanding",
    "workflowPrimary",
    "scanSection",
    "scanDetailPanel",
    "pendingSection",
  ],
  research: [
    "researchTabNav",
    "researchSummaryLanding",
    "quickCheckSection",
    "sectorsSection",
    "moversSection",
    "backtestSection",
    "reportSectionCard",
    "secCompareSection",
    "portfolioSection",
    "portfolioPanelRisk",
    "portfolioPanelBook",
    "performanceSection",
    "cockpitMergedPanel",
    "cockpitSection",
    "recoverySection",
    "learningSection",
  ],
  diagnostics: [
    "systemAlertBanner",
    "systemSummaryLanding",
    "healthRibbon",
    "systemDecisionPanel",
    "decisionDashboardCard",
    "statusDetailsPanel",
    "systemQualityDiagnostics",
    "calibrationSection",
    "shadowScoreboardSection",
    "reviewLoopSection",
  ],
  settings: [
    "settingsSummaryLanding",
    "onboardingSection",
    "settingsSection",
    "settingsAccountPanel",
  ],
});
const SECTION_TO_SCREEN = Object.freeze(
  Object.entries(SCREEN_SECTIONS).reduce((acc, [screen, ids]) => {
    ids.forEach((id) => {
      acc[id] = screen;
    });
    return acc;
  }, {}),
);
let currentScreenMode = "operations";
// Screen controller registry (static/screens/*). Populated by wireEvents via
// buildScreenControllers(); prime() dispatch is gated by the
// screen_controllers flag (see wiki [[section-migration-map]]).
let screenControllers = {};
let screenSwitchTimer = null;

const FUNNEL_EVENTS = Object.freeze({
  SIGNUP: "signup",
  AUTH_LINKED: "auth_linked",
  FIRST_SCAN: "first_scan",
  FIRST_PENDING_TRADE: "first_pending_trade",
  FIRST_APPROVED_TRADE: "first_approved_trade",
  RETAINED_SESSION: "retained_session",
});

let retainedSessionTimer = null;

function canTrackProductAnalytics() {
  return Boolean(state.publicConfig?.saas_mode && state.accountMe?.id);
}

async function trackProductEvent(eventName, properties = {}) {
  if (!canTrackProductAnalytics()) return false;
  const out = await api.post("/api/analytics/event", {
    event: safeText(eventName).toLowerCase(),
    properties: properties && typeof properties === "object" ? properties : {},
  });
  return Boolean(out?.ok);
}

async function trackFunnelMilestoneOnce(eventName, properties = {}) {
  const key = safeText(eventName).toLowerCase();
  if (!key || state.funnelMilestonesSent[key]) return false;
  const sent = await trackProductEvent(key, properties);
  if (sent) state.funnelMilestonesSent[key] = true;
  return sent;
}

/**
 * KPI instrumentation wrapper (wiki [[ux-kpi-baseline]]). Sends through the
 * existing SaaS analytics route when available and always emits a local
 * console.debug so usability sessions can be traced without backend access.
 */
function trackUiEvent(eventName, properties = {}) {
  void trackProductEvent(eventName, properties);
  try {
    console.debug("[ui-event]", safeText(eventName).toLowerCase(), properties);
  } catch {
    /* ignore */
  }
}

function scheduleRetainedSessionTracking() {
  if (retainedSessionTimer) return;
  if (!state.publicConfig?.saas_mode) return;
  retainedSessionTimer = window.setTimeout(() => {
    retainedSessionTimer = null;
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.RETAINED_SESSION, {
      seconds_since_load: 60,
      had_signals_loaded: Array.isArray(state.latestSignals) && state.latestSignals.length > 0,
    });
  }, 60_000);
}

function getDisplayMode() {
  const m = localStorage.getItem(UI_VIEW_MODE_KEY) || "pro";
  return ["simple", "standard", "pro"].includes(m) ? m : "pro";
}

function normalizeScreenMode(raw) {
  const mode = safeText(raw).toLowerCase();
  const resolved = SCREEN_ALIASES[mode] || mode;
  return SCREEN_MODES.includes(resolved) ? resolved : "operations";
}

function inferScreenFromHash() {
  const id = safeText(window.location.hash || "").replace(/^#/, "");
  if (!id) return "";
  return SECTION_TO_SCREEN[id] || "";
}

function getScreenModeFromUrl() {
  try {
    const u = new URL(window.location.href);
    const fromQuery = normalizeScreenMode(u.searchParams.get("screen"));
    if (u.searchParams.get("screen")) return fromQuery;
  } catch {
    /* ignore */
  }
  return normalizeScreenMode(inferScreenFromHash() || "operations");
}

function writeScreenModeToUrl(mode) {
  try {
    const u = new URL(window.location.href);
    u.searchParams.set("screen", mode);
    const q = u.searchParams.toString();
    window.history.replaceState({}, "", `${u.pathname}${q ? `?${q}` : ""}${u.hash || ""}`);
  } catch {
    /* ignore */
  }
}

function refreshScreenSwitchUi(mode) {
  document.querySelectorAll(".screen-switch-btn[data-screen-mode]").forEach((btn) => {
    const active = btn.getAttribute("data-screen-mode") === mode;
    btn.setAttribute("aria-selected", active ? "true" : "false");
    btn.setAttribute("tabindex", active ? "0" : "-1");
    btn.classList.toggle("active", active);
  });
}

function refreshSectionNavForScreen(mode) {
  document.querySelectorAll(".section-nav a[data-nav-screens]").forEach((link) => {
    const scopes = safeText(link.getAttribute("data-nav-screens"))
      .split(",")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);
    const visible = scopes.length === 0 || scopes.includes(mode);
    link.classList.toggle("hidden", !visible);
  });
}

function renderScreenContext(mode) {
  const cfg = SCREEN_CONTEXT[mode] || SCREEN_CONTEXT.operations;
  const titleEl = document.getElementById("screenContextTitle");
  const textEl = document.getElementById("screenContextText");
  const hintEl = document.getElementById("screenContextHint");
  const ctaEl = document.getElementById("screenContextCta");
  const altCtaEl = document.getElementById("screenContextAltCta");
  if (titleEl) titleEl.textContent = cfg.title;
  if (textEl) textEl.textContent = cfg.text;
  if (hintEl) hintEl.textContent = "Press Ctrl/Cmd + 1 Today, 2 Research, 3 System, 4 Settings.";
  if (ctaEl) {
    ctaEl.textContent = cfg.ctaLabel;
    ctaEl.setAttribute("href", cfg.ctaHref);
  }
  if (altCtaEl) {
    altCtaEl.textContent = cfg.altCtaLabel;
    altCtaEl.setAttribute("href", cfg.altCtaHref);
  }
}

function maybePrimeScreenData(mode) {
  const controller = screenControllers[mode];
  if (controller) controller.prime();
}

function maybeShowScreenNudge(mode) {
  if (!mode || mode === "operations") return;
  const key = `${SCREEN_NUDGE_KEY_PREFIX}${mode}`;
  try {
    if (localStorage.getItem(key)) return;
    localStorage.setItem(key, "1");
  } catch {
    return;
  }
  const cfg = SCREEN_CONTEXT[mode] || SCREEN_CONTEXT.operations;
  const nudgeMap = {
    settings: "Finish connectivity and profile settings once, then return to Operations.",
    research: "Use quick check, backtests, SEC compare, and dossiers to validate each setup.",
    diagnostics: "Use this screen to validate reliability and troubleshoot blockers without interrupting operations.",
    cockpit: "Click any opportunity row for the decision card and order-intent preview.",
  };
  const hint = nudgeMap[mode] || "Use the context actions to jump into this screen.";
  showToast(`${cfg.title}: ${hint}`, "info", 2800);
}

let lastTrackedScreen = null;

function applyScreenMode(mode, { updateUrl = false } = {}) {
  const m = normalizeScreenMode(mode);
  if (m !== lastTrackedScreen) {
    trackUiEvent("screen_view", { screen: m, initial: lastTrackedScreen === null });
    lastTrackedScreen = m;
  }
  currentScreenMode = m;
  document.body.classList.add("ui-screen-switching");
  if (screenSwitchTimer) clearTimeout(screenSwitchTimer);
  screenSwitchTimer = window.setTimeout(() => {
    document.body.classList.remove("ui-screen-switching");
    screenSwitchTimer = null;
  }, 170);
  document.body.classList.remove(
    "ui-screen-operations",
    "ui-screen-research",
    "ui-screen-diagnostics",
    "ui-screen-settings",
    "ui-screen-cockpit",
  );
  document.body.classList.add(`ui-screen-${m}`);
  refreshScreenSwitchUi(m);
  refreshSectionNavForScreen(m);
  renderScreenContext(m);
  maybePrimeScreenData(m);
  maybeShowScreenNudge(m);
  if (m === "research") {
    // Preserve a sub-tab already activated by a ?section= deep link (e.g.
    // section=sec → Quick check Deep) or by the user; default to Portfolio.
    const activeTab = document
      .querySelector("[data-research-tab-btn].active")
      ?.getAttribute("data-research-tab-btn");
    applyResearchTab(activeTab || DEFAULT_RESEARCH_TAB);
    updateResearchSummaryLanding();
  }
  if (m === "diagnostics") {
    updateSystemSummaryLanding();
    refreshSystemAlertBanner();
  }
  if (m === "settings") {
    updateSettingsSummaryLanding();
    scrollToConnectSchwabIfNeeded();
  }
  if (updateUrl) writeScreenModeToUrl(m);
}

function shouldForceConnectFirst() {
  return Boolean(state.publicConfig?.saas_mode && state.accountMe?.onboarding_required);
}

function applyConnectFirstExperience() {
  const active = shouldForceConnectFirst();
  document.body.classList.toggle("ui-connect-first", active);

  const banner = document.getElementById("connectSchwabBanner");
  if (banner) {
    banner.classList.toggle("hidden", !active);
    banner.setAttribute("aria-hidden", active ? "false" : "true");
  }

  if (isPriorityFeedActive()) {
    if (active) {
      pushPriorityItem({
        key: "connect_schwab_required",
        title: "Connect Schwab first",
        message: "Link your Schwab account before scans and trades will work. You can still browse the app.",
        severity: "warn",
        href: "/?screen=settings#onboardingSection",
        hrefLabel: "Connect",
      });
    } else {
      removePriorityItem("connect_schwab_required");
    }
  }
}

function scrollToConnectSchwabIfNeeded() {
  if (!shouldForceConnectFirst()) return;
  document.getElementById("onboardingSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/**
 * Consume a `?display=simple|standard|pro` deep link (the retired /simple
 * page redirects here). Strips the param via replaceState, mirroring the
 * `?section=` / `?ff=` handling, and returns the mode or "".
 */
function consumeDisplayModeFromUrl() {
  try {
    const u = new URL(window.location.href);
    const raw = safeText(u.searchParams.get("display")).toLowerCase();
    if (!raw) return "";
    u.searchParams.delete("display");
    const q = u.searchParams.toString();
    window.history.replaceState({}, "", `${u.pathname}${q ? `?${q}` : ""}${u.hash || ""}`);
    return ["simple", "standard", "pro"].includes(raw) ? raw : "";
  } catch {
    return "";
  }
}

function applyDisplayMode(mode) {
  const m = ["simple", "standard", "pro"].includes(mode) ? mode : "pro";
  localStorage.setItem(UI_VIEW_MODE_KEY, m);
  document.body.classList.remove("ui-simple", "ui-standard", "ui-pro");
  document.body.classList.add(`ui-${m}`);
  const sel = document.getElementById("displayModeSelect");
  if (sel) sel.value = m;
  const pro = m === "pro";
  const scanDiag = document.getElementById("scanDiagnosticsPanel");
  const scanAdvanced = document.getElementById("scanAdvancedOptionsPanel");
  const secDerived = document.getElementById("secCompareDerivedPanel");
  if (scanDiag) scanDiag.open = pro;
  if (scanAdvanced) scanAdvanced.open = pro;
  if (secDerived) secDerived.open = pro;
  const perfRaw = document.getElementById("performanceRawDetails");
  if (perfRaw && !pro) perfRaw.open = false;
}

async function runLazyApi(key) {
  if (!key || lazyLoaded[key]) return;
  lazyLoaded[key] = true;
  try {
    if (key === "portfolio") await refreshPortfolio();
    else if (key === "sectors") await refreshSectors();
    else if (key === "movers") await refreshMovers();
    else if (key === "performance") await refreshPerformance();
    else if (key === "backtest") await refreshBacktestRuns();
    else if (key === "onboarding") await refreshOnboarding();
    else if (key === "profiles") {
      await loadProfiles();
    } else if (key === "calibration") {
      await refreshCalibration();
    } else if (key === "shadowScoreboard") {
      await refreshShadowScoreboard();
    } else if (key === "reviewLoop") {
      await refreshReviewLoop();
    }
  } catch (err) {
    console.warn("lazy load failed", key, err);
    lazyLoaded[key] = false;
  }
}

function setupLazySectionLoading() {
  const nodes = document.querySelectorAll("[data-lazy-api]");
  if (!nodes.length) return;
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        // Collapsed disclosures load on first expand (toggle listener below),
        // not on scroll-by — keeps the slim Operations landing cheap.
        if (e.target.tagName === "DETAILS" && !e.target.open) return;
        const k = e.target.getAttribute("data-lazy-api");
        if (k) void runLazyApi(k);
      });
    },
    { rootMargin: "120px 0px", threshold: 0.04 }
  );
  nodes.forEach((n) => {
    io.observe(n);
    if (n.tagName === "DETAILS") {
      n.addEventListener("toggle", () => {
        if (!n.open) return;
        const k = n.getAttribute("data-lazy-api");
        if (k) void runLazyApi(k);
      });
    }
  });
}

function markDeferredDataPlaceholders() {
  const pb = document.getElementById("portfolioBody");
  const firstCell = pb?.querySelector("td");
  if (pb && firstCell && firstCell.textContent === "Loading...") {
    pb.innerHTML = `<tr><td colspan="5" class="muted">Portfolio loads when you scroll here (or use Refresh All).</td></tr>`;
  }
  const pm = document.getElementById("portfolioMeta");
  if (pm && pm.textContent === "Loading...") pm.textContent = "Not loaded yet";
}

function renderLiveTradingSaasPanel() {
  const block = document.getElementById("liveTradingSaasBlock");
  const killBanner = document.getElementById("platformKillSwitchBanner");
  if (killBanner) {
    const freshEl = document.getElementById("platformKillSwitchBannerFresh");
    if (state.publicConfig.platform_live_trading_kill_switch) {
      killBanner.classList.remove("hidden");
      // Mirror into the priority feed (banner stays for regulatory prominence).
      if (isPriorityFeedActive()) {
        pushPriorityItem({
          key: "platform_kill_switch",
          title: "Platform kill switch active",
          message: "New risk-increasing orders are blocked until the host clears the kill switch.",
          severity: "error",
          href: "#platformKillSwitchBanner",
          hrefLabel: "Details",
        });
      }
    } else {
      killBanner.classList.add("hidden");
      if (isPriorityFeedActive()) removePriorityItem("platform_kill_switch");
    }
    // Stamp freshness whenever we re-evaluate, even if hidden — so toggling
    // the banner on/off carries a "verified at" label.
    applyFreshness(freshEl, {
      asOf: new Date().toISOString(),
      source: "/api/public-config",
      surface: "status_details",
      unavailable: "config not loaded",
    });
    refreshSystemAlertBanner();
  }
  if (!block) return;
  const localHint = document.getElementById("settingsGuardrailsLocalHint");
  if (!state.publicConfig.saas_mode) {
    block.classList.add("hidden");
    if (localHint) {
      localHint.classList.remove("hidden");
      localHint.textContent = "Live order toggles appear here when account controls are available.";
    }
    updateSettingsSummaryLanding();
    return;
  }
  if (localHint) localHint.classList.add("hidden");
  block.classList.remove("hidden");
  block.querySelectorAll("input, button").forEach((el) => {
    el.disabled = shouldForceConnectFirst();
  });
  const statusEl = document.getElementById("liveTradingStatus");
  if (statusEl) {
    const on = Boolean(state.accountMe?.live_execution_enabled);
    const halted = Boolean(state.accountMe?.trading_halted);
    let line = on
      ? "Account status: live orders from this app are on."
      : "Account status: live orders from this app are still off.";
    if (halted) line += " Trading pause is on (new approvals blocked).";
    statusEl.textContent = line;
  }
  const haltCb = document.getElementById("tradingHaltedCheckbox");
  if (haltCb && state.accountMe) {
    haltCb.checked = Boolean(state.accountMe.trading_halted);
  }
  updateSettingsSummaryLanding();
}

function billingCallbackUrls() {
  const base = `${window.location.origin}${window.location.pathname}`;
  return {
    success_url: `${base}?billing=checkout_success`,
    cancel_url: `${base}?billing=checkout_cancel`,
  };
}

function renderBillingPanel() {
  const card = document.getElementById("billingSaasBlock");
  if (!card) return;
  card.classList.add("hidden");
  card.setAttribute("aria-hidden", "true");
}

async function beginBillingCheckout() {
  const btn = document.getElementById("billingCheckoutBtn");
  if (btn) btn.disabled = true;
  try {
    const out = await api.post("/api/billing/checkout-session", billingCallbackUrls());
    if (!out.ok) {
      updateActionCenter({ title: "Billing checkout failed", message: out.error, severity: "error" });
      logEvent({ kind: "system", severity: "error", message: `Billing checkout failed: ${out.error}` });
      return;
    }
    const url = safeText(out.data?.url || "");
    if (!url) {
      updateActionCenter({ title: "Billing checkout failed", message: "Missing checkout URL.", severity: "error" });
      return;
    }
    await trackProductEvent("billing_checkout_started", { source: "settings_panel" });
    window.location.href = url;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function openBillingPortal() {
  const btn = document.getElementById("billingPortalBtn");
  if (btn) btn.disabled = true;
  try {
    const out = await api.post("/api/billing/portal-session", {});
    if (!out.ok) {
      updateActionCenter({ title: "Billing portal failed", message: out.error, severity: "error" });
      logEvent({ kind: "system", severity: "error", message: `Billing portal failed: ${out.error}` });
      return;
    }
    const url = safeText(out.data?.url || "");
    if (!url) {
      updateActionCenter({ title: "Billing portal failed", message: "Missing portal URL.", severity: "error" });
      return;
    }
    await trackProductEvent("billing_portal_opened", { source: "settings_panel" });
    window.location.href = url;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function refreshAccountMe() {
  if (!state.publicConfig.saas_mode) {
    state.accountMe = null;
    renderLiveTradingSaasPanel();
    renderBillingPanel();
    applyConnectFirstExperience();
    return;
  }
  const token = await getApiAccessToken();
  if (!token) {
    state.accountMe = null;
    renderLiveTradingSaasPanel();
    renderBillingPanel();
    applyConnectFirstExperience();
    return;
  }
  const out = await api.get("/api/me");
  state.accountMe = out.ok ? out.data : null;
  renderLiveTradingSaasPanel();
  renderBillingPanel();
  applyConnectFirstExperience();
}

async function refreshCritical() {
  await Promise.all([refreshStatus(), refreshAccountMe(), refreshPending(), refreshTwoFaStatus()]);
}

function setJobProgress(barId, labelId, fraction, labelText) {
  const bar = document.getElementById(barId);
  const lbl = labelId ? document.getElementById(labelId) : null;
  const wrap = bar?.closest?.(".job-progress-wrap");
  if (bar && bar.tagName === "PROGRESS") {
    const pct = Math.max(0, Math.min(100, Math.round((fraction || 0) * 100)));
    bar.value = pct;
    if (wrap) wrap.classList.toggle("hidden", pct <= 0 && !labelText);
  }
  if (lbl) lbl.textContent = labelText || "";
}

async function initSupabaseAuth(url, anonKey) {
  let createClient;
  try {
    const mod = await import(SUPABASE_ESM);
    createClient = mod.createClient;
  } catch (err) {
    console.warn("Supabase client SDK failed to load", err);
    logEvent({
      kind: "system",
      severity: "warn",
      message: "Could not load Supabase from CDN; use manual JWT below.",
    });
    markAuthReady();
    return;
  }

  const sb = createClient(url, anonKey, {
    auth: {
      autoRefreshToken: true,
      persistSession: true,
      detectSessionInUrl: true,
    },
  });
  setSupabaseClient(sb);

  const {
    data: { session },
  } = await sb.auth.getSession();
  persistApiJwtFromSession(session);
  if (session?.access_token) {
    await createCookieAuthSession(session.access_token);
  }
  updateSupabaseAuthUI(session);

  sb.auth.onAuthStateChange(async (_event, nextSession) => {
    persistApiJwtFromSession(nextSession);
    if (nextSession?.access_token) {
      await createCookieAuthSession(nextSession.access_token);
    }
    updateSupabaseAuthUI(nextSession);
    if (nextSession?.access_token) scheduleRetainedSessionTracking();
    void refreshAccountMe();
    void loadProfiles();
    void refreshOnboarding();
    void refreshAuthDebugPanel();
  });

  const verifyBtn = document.getElementById("supabaseVerifyBtn");
  if (verifyBtn) {
    // Shared cooldown keeps the topbar and onboarding inline buttons in sync.
    attachVerifyCooldownButton(verifyBtn, { label: authActionLabel });
  }
  verifyBtn?.addEventListener("click", async () => {
    const email = document.getElementById("supabaseEmail")?.value?.trim() || "";
    const result = await requestVerificationEmail({
      supabase: sb,
      email,
      redirectTo: `${window.location.origin}/login`,
      verified: hasVerifiedEmailOnce(),
    });
    logEvent({
      kind: "system",
      severity: result.ok ? "info" : "warn",
      message: result.message,
    });
    if (result.ok) {
      void trackFunnelMilestoneOnce(FUNNEL_EVENTS.SIGNUP, {
        source: "supabase_email_verification",
      });
    }
  });

  document.getElementById("supabaseSignOutBtn")?.addEventListener("click", async () => {
    await sb.auth.signOut();
    clearStoredApiJwt();
    await clearCookieAuthSession();
    const inp = document.getElementById("jwtInput");
    if (inp) inp.value = "";
    logEvent({ kind: "system", severity: "info", message: "Signed out." });
  });

  markAuthReady();
}

function formatStrategySummary(summary = null) {
  if (!summary || typeof summary !== "object") return "";
  const dominant = formatStrategyLabel(summary.dominant_live_strategy || "");
  const total = safeNum(summary.total_ranked, 0);
  const count = safeNum(summary.dominant_count, 0);
  if (!dominant || dominant === "—" || total <= 0 || count <= 0) return "";
  return ` Dominant strategy: ${dominant} (${count}/${total}).`;
}

function updateTopStrategyChip(summary = null) {
  const el = document.getElementById("scanTopStrategy");
  if (!el) return;
  const dominant = formatStrategyLabel(summary?.dominant_live_strategy || "—");
  const total = safeNum(summary?.total_ranked, 0);
  const count = safeNum(summary?.dominant_count, 0);
  if (dominant === "—" || total <= 0 || count <= 0) {
    el.textContent = "Top Strategy: --";
    return;
  }
  el.textContent = `Top Strategy: ${dominant} (${count}/${total})`;
}

function formatRelativeScanTime(iso) {
  if (!iso) return "—";
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return "—";
  const mins = Math.max(0, Math.round((Date.now() - ts) / 60000));
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function updateTodaySummaryLanding() {
  const sigEl = document.getElementById("todaySummarySignals");
  const sigHint = document.getElementById("todaySummarySignalsHint");
  const pendEl = document.getElementById("todaySummaryPending");
  const pendHint = document.getElementById("todaySummaryPendingHint");
  const scanEl = document.getElementById("todaySummaryScan");
  const scanHint = document.getElementById("todaySummaryScanHint");

  if (sigEl) {
    if (state.lastScanAt) {
      clearUnavailable(sigEl);
      const n = Array.isArray(state.latestSignals) ? state.latestSignals.length : 0;
      sigEl.textContent = formatCount(n);
      if (sigHint) sigHint.textContent = n === 0 ? "zero candidates" : `${n} from last scan`;
    } else {
      markUnavailable(sigEl, "no scan run this session");
      if (sigHint) sigHint.textContent = "no scan yet";
    }
  }

  if (pendEl) {
    if (state.lastPendingCount === null || state.lastPendingCount === undefined) {
      markUnavailable(pendEl, "pending queue not loaded");
      if (pendHint) pendHint.textContent = "awaiting status";
    } else {
      clearUnavailable(pendEl);
      pendEl.textContent = formatCount(state.lastPendingCount);
      if (pendHint) {
        pendHint.textContent =
          state.lastPendingCount === 0 ? "nothing staged" : "needs approval on Today";
      }
    }
  }

  if (scanEl) {
    if (state.lastScanAt) {
      clearUnavailable(scanEl);
      scanEl.textContent = formatRelativeScanTime(state.lastScanAt);
      if (scanHint) scanHint.textContent = "most recent scan";
    } else {
      markUnavailable(scanEl, "no scan yet");
      if (scanHint) scanHint.textContent = "run scan to begin";
    }
  }
  updateWorkflowKanban();
}

function setScanStatusLoading(title, detail) {
  setOperationsStatusStrip("scanStatusStrip", "loading", title, detail);
  syncScanSectionState("loading");
  updateWorkflowKanban({ forceState: "loading", title: "Scan running.", detail });
}

function setScanStatusError(title, detail) {
  setOperationsStatusStrip("scanStatusStrip", "error", title, detail);
  syncScanSectionState("error");
  updateWorkflowKanban();
}

function updateHeroInfographic() {
  const sigEl = document.getElementById("heroKpiSignals");
  const sigFreshEl = document.getElementById("heroKpiSignalsFresh");
  const pendEl = document.getElementById("heroKpiPending");
  const pendFreshEl = document.getElementById("heroKpiPendingFresh");
  const wlEl = document.getElementById("heroKpiWatchlist");
  const wlFreshEl = document.getElementById("heroKpiWatchlistFresh");

  // Signals: "—" until a scan has actually returned (state.lastScanAt set).
  if (sigEl) {
    if (state.lastScanAt) {
      clearUnavailable(sigEl);
      sigEl.textContent = formatCount(
        Array.isArray(state.latestSignals) ? state.latestSignals.length : 0,
      );
    } else {
      markUnavailable(sigEl, "no scan run this session");
    }
  }
  applyFreshness(sigFreshEl, {
    asOf: state.lastScanAt,
    source: "/api/scan",
    surface: "scan_results",
    unavailable: "no scan yet",
  });

  // Pending: "—" until /api/pending-trades has answered. We piggyback on the
  // dedicated state field rather than scraping #pendingCount text.
  if (pendEl) {
    if (state.lastPendingCount === null || state.lastPendingCount === undefined) {
      markUnavailable(pendEl, "Pending trades not loaded yet");
    } else {
      clearUnavailable(pendEl);
      pendEl.textContent = formatCount(state.lastPendingCount);
    }
  }
  applyFreshness(pendFreshEl, {
    asOf: state.lastPendingAt || null,
    source: "/api/pending-trades",
    surface: "pending_queue",
    unavailable: "awaiting status",
  });

  // Watchlist universe: defaults to SP1500 (the canonical scan universe)
  // and is overwritten by scan diagnostics. We label provenance so the
  // user knows whether it's the assumed default or a measured count.
  if (wlEl) {
    const n = Number(state.lastWatchlistSize);
    if (!Number.isFinite(n) || n <= 0) {
      markUnavailable(wlEl, "Universe size not reported yet");
    } else {
      clearUnavailable(wlEl);
      wlEl.textContent = formatCount(n);
    }
  }
  applyFreshness(wlFreshEl, {
    asOf: state.lastScanAt,
    source: state.lastScanAt ? "last scan" : "S&P 1500 (default)",
    surface: "scan_results",
    unavailable: "scan to populate",
  });
  updateTodaySummaryLanding();
}

function prefillResearchTicker(ticker, { overwrite = false } = {}) {
  const sym = safeText(ticker || "").trim().toUpperCase();
  if (!sym) return;
  const setTickerValue = (id) => {
    const el = document.getElementById(id);
    if (el && (overwrite || !el.value.trim())) el.value = sym;
  };
  const ti = document.getElementById("tickerInput");
  if (ti) ti.value = sym;
  setTickerValue("reportTickerInput");
  setTickerValue("secCompareTickerA");
  const btUniverse = document.getElementById("btUniverse");
  const btTickers = document.getElementById("btTickers");
  if (btUniverse && (overwrite || !btTickers?.value.trim())) {
    btUniverse.value = "tickers";
    syncBtUniverseRow();
  }
  if (btTickers && (overwrite || !btTickers.value.trim())) btTickers.value = sym;
  updateResearchSummaryLanding();
}

function openResearchForTicker(ticker) {
  const sym = safeText(ticker || _scanDetailSignal?.ticker || _scanDetailSignal?.symbol || "").trim().toUpperCase();
  if (!sym) return;
  prefillResearchTicker(sym);
  applyScreenMode("research", { updateUrl: true });
  applyResearchTab("check");
  document.getElementById("quickCheckSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function syncSystemSummaryKpi(summaryId, ribbonId, hintId, hintWhenOk) {
  const summaryEl = document.getElementById(summaryId);
  const ribbonEl = document.getElementById(ribbonId);
  const hintEl = document.getElementById(hintId);
  if (!summaryEl || !ribbonEl) return;
  if (ribbonEl.hasAttribute("data-unavailable")) {
    markUnavailable(summaryEl, ribbonEl.getAttribute("data-unavailable-reason") || "not loaded");
    if (hintEl) hintEl.textContent = "awaiting health check";
    return;
  }
  clearUnavailable(summaryEl);
  summaryEl.textContent = safeText(ribbonEl.textContent || "—");
  if (hintEl) hintEl.textContent = hintWhenOk;
}

function updateSystemSummaryLanding() {
  const statusLine = document.getElementById("systemSummaryStatusLine");
  const ribbonSummary = document.getElementById("healthRibbonSummary");
  if (statusLine) {
    const line = safeText(ribbonSummary?.textContent || "").trim();
    if (line && !ribbonSummary?.hasAttribute("data-unavailable")) {
      clearUnavailable(statusLine);
      statusLine.textContent = line;
    } else {
      markUnavailable(statusLine, "awaiting first health check");
    }
  }
  syncSystemSummaryKpi("systemSummaryAuth", "ribbonAuth", "systemSummaryAuthHint", "broker session");
  syncSystemSummaryKpi("systemSummaryQuotes", "ribbonQuotes", "systemSummaryQuotesHint", "live market data");
  syncSystemSummaryKpi(
    "systemSummaryValidation",
    "ribbonValidation",
    "systemSummaryValidationHint",
    "validation artifact",
  );
}

function hideSystemAlertBanner() {
  const banner = document.getElementById("systemAlertBanner");
  if (!banner) return;
  banner.classList.add("hidden");
  banner.setAttribute("aria-hidden", "true");
}

function showSystemAlertBanner({ title = "", message = "", severity = "warn", href = "", hrefLabel = "" } = {}) {
  const banner = document.getElementById("systemAlertBanner");
  const titleEl = document.getElementById("systemAlertBannerTitle");
  const textEl = document.getElementById("systemAlertBannerText");
  const linkEl = document.getElementById("systemAlertBannerLink");
  if (!banner || !titleEl || !textEl) return;
  const sev = ["error", "warn", "success", "info"].includes(severity) ? severity : "warn";
  banner.classList.remove("hidden");
  banner.setAttribute("aria-hidden", "false");
  banner.classList.remove("info", "success", "warn", "error");
  banner.classList.add(sev);
  titleEl.textContent = safeText(title) || "System alert";
  textEl.textContent = safeText(message);
  if (linkEl) {
    const target = safeText(href);
    if (target) {
      linkEl.classList.remove("hidden");
      linkEl.href = target;
      linkEl.textContent = safeText(hrefLabel) || "Open";
    } else {
      linkEl.classList.add("hidden");
    }
  }
}

function refreshSystemAlertBanner(health = {}) {
  if (state.publicConfig?.platform_live_trading_kill_switch) {
    showSystemAlertBanner({
      title: "Platform kill switch active",
      message: "New risk-increasing orders are blocked until the host clears the kill switch.",
      severity: "error",
      href: "#platformKillSwitchBanner",
      hrefLabel: "Details",
    });
    return;
  }
  if (state.accountMe?.trading_halted) {
    showSystemAlertBanner({
      title: "Trading pause active",
      message: "New approvals are blocked until trading pause is cleared in Settings.",
      severity: "warn",
      href: "#settingsSummaryGuardrails",
      hrefLabel: "Live order controls",
    });
    return;
  }
  const authState = health.authState;
  if (authState === "disconnected") {
    showSystemAlertBanner({
      title: "Broker authentication blocked",
      message: "Reconnect Schwab account and market sessions before running scans or approving orders.",
      severity: "error",
      href: "#onboardingSection",
      hrefLabel: "Connect Schwab",
    });
    return;
  }
  if (authState === "unverified") {
    showSystemAlertBanner({
      title: "Broker connection unverified",
      message:
        "Schwab tokens are saved but the live API has not confirmed a response yet. Reconnect if this persists.",
      severity: "warn",
      href: "#healthRibbon",
      hrefLabel: "Health tiles",
    });
    return;
  }
  if (health.quoteOk === false || (typeof health.errRate === "number" && health.errRate >= 3.0)) {
    showSystemAlertBanner({
      title: "Market data reliability degraded",
      message: "Quote health or API error rate needs attention before trusting execution.",
      severity: "warn",
      href: "#statusDetailsPanel",
      hrefLabel: "Detailed status",
    });
    return;
  }
  const top = getTopPriorityItem();
  if (top && (top.severity === "error" || top.severity === "warn")) {
    showSystemAlertBanner({
      title: top.title,
      message: top.message,
      severity: top.severity,
      href: top.href || "#healthRibbon",
      hrefLabel: top.hrefLabel || "Open",
    });
    return;
  }
  hideSystemAlertBanner();
}

function updateSettingsSummaryLanding() {
  const connEl = document.getElementById("settingsSummaryConnection");
  const connHint = document.getElementById("settingsSummaryConnectionHint");
  const liveEl = document.getElementById("settingsSummaryLive");
  const liveHint = document.getElementById("settingsSummaryLiveHint");
  const guardrails = document.getElementById("settingsSummaryGuardrails");

  if (guardrails) {
    guardrails.classList.toggle("settings-summary-guardrails--blocked", shouldForceConnectFirst());
  }
  const localHint = document.getElementById("settingsGuardrailsLocalHint");
  if (localHint && shouldForceConnectFirst()) {
    localHint.classList.remove("hidden");
    localHint.textContent = "Connect Schwab first — live order controls unlock after your account is linked.";
  } else if (localHint && state.publicConfig?.saas_mode) {
    localHint.classList.add("hidden");
  }

  const onboardingMeta = document.getElementById("onboardingMeta");
  const metaText = safeText(onboardingMeta?.textContent || "").trim();
  if (connEl) {
    if (metaText && !/loading/i.test(metaText)) {
      clearUnavailable(connEl);
      const linked = /linked|connected|complete|done/i.test(metaText);
      connEl.textContent = linked ? "Linked" : "Not linked";
      if (connHint) {
        connHint.textContent = linked ? "Schwab is connected" : "Start Connect Schwab below";
      }
    } else {
      markUnavailable(connEl, "status not loaded yet");
      if (connHint) connHint.textContent = "open Connect Schwab below";
    }
  }

  if (liveEl) {
    if (state.publicConfig?.saas_mode && state.accountMe) {
      clearUnavailable(liveEl);
      const halted = Boolean(state.accountMe.trading_halted);
      const enabled = Boolean(state.accountMe.live_execution_enabled);
      if (halted) {
        liveEl.textContent = "Paused";
        if (liveHint) liveHint.textContent = "new approvals are blocked";
      } else if (enabled) {
        liveEl.textContent = "On";
        if (liveHint) liveHint.textContent = "live orders can send";
      } else {
        liveEl.textContent = "Off";
        if (liveHint) liveHint.textContent = "paper / review only";
      }
    } else {
      const statusLine = safeText(document.getElementById("liveTradingStatus")?.textContent || "").trim();
      if (statusLine) {
        clearUnavailable(liveEl);
        liveEl.textContent = /pause|halt/i.test(statusLine) ? "Paused" : /live orders.*on/i.test(statusLine) ? "On" : "Off";
        if (liveHint) liveHint.textContent = "see controls below";
      } else {
        markUnavailable(liveEl, "not configured");
        if (liveHint) liveHint.textContent = "turn on after Schwab is linked";
      }
    }
  }
}

function updateResearchSummaryLanding() {
  const posEl = document.getElementById("researchSummaryPositions");
  const posHint = document.getElementById("researchSummaryPositionsHint");
  const alertEl = document.getElementById("researchSummaryAlert");
  const alertHint = document.getElementById("researchSummaryAlertHint");
  const bookEl = document.getElementById("researchSummaryBook");
  const bookHint = document.getElementById("researchSummaryBookHint");
  const nextEl = document.getElementById("researchSummaryNext");
  const nextHint = document.getElementById("researchSummaryNextHint");
  const statusStrip = document.getElementById("researchTriageStatus");
  const statusTitle = document.getElementById("researchTriageStatusTitle");
  const statusDetail = document.getElementById("researchTriageStatusDetail");
  const statusPill = statusStrip?.querySelector(".research-status-pill");

  const portfolioBody = document.getElementById("portfolioBody");
  let positionCount = null;
  const pdata = state.lastPortfolioData;
  if (pdata && typeof pdata.positions_count === "number") {
    positionCount = pdata.positions_count;
  } else if (portfolioBody) {
    const rows = [...portfolioBody.querySelectorAll("tr")].filter((tr) => {
      const cell = tr.querySelector("td");
      return (
        cell &&
        !cell.classList.contains("muted") &&
        !/loading|open portfolio|not loaded|scroll here|no open positions/i.test(cell.textContent || "")
      );
    });
    if (rows.length) positionCount = rows.length;
  }

  if (posEl) {
    if (positionCount === null) {
      markUnavailable(posEl, "portfolio not loaded");
      if (posHint) posHint.textContent = "open Portfolio tab";
    } else {
      clearUnavailable(posEl);
      posEl.textContent = formatCount(positionCount);
      if (posHint) posHint.textContent = positionCount === 1 ? "position · Schwab/manual" : "positions · Schwab/manual";
    }
  }

  const alertCount =
    typeof pdata?.alert_count === "number"
      ? pdata.alert_count
      : Array.isArray(pdata?.alerts)
        ? pdata.alerts.length
        : null;
  if (alertEl) {
    if (positionCount === null) {
      alertEl.textContent = "—";
      if (alertHint) alertHint.textContent = "awaiting portfolio";
    } else if (alertCount == null) {
      alertEl.textContent = "None";
      if (alertHint) alertHint.textContent = "no alert feed yet";
    } else if (alertCount === 0) {
      alertEl.textContent = "None";
      if (alertHint) alertHint.textContent = "concentration OK";
    } else {
      alertEl.textContent = formatCount(alertCount);
      if (alertHint) alertHint.textContent = alertCount === 1 ? "flag to review" : "flags to review";
    }
  }

  const bookPnl =
    typeof pdata?.book_mtd_pnl_pct === "number"
      ? pdata.book_mtd_pnl_pct
      : typeof pdata?.mtd_pnl_pct === "number"
        ? pdata.mtd_pnl_pct
        : null;
  if (bookEl) {
    if (bookPnl == null) {
      markUnavailable(bookEl, "book not loaded");
      if (bookHint) bookHint.textContent = "open Book tab";
    } else {
      clearUnavailable(bookEl);
      const sign = bookPnl > 0 ? "+" : "";
      bookEl.textContent = `${sign}${bookPnl.toFixed(1)}%`;
      if (bookHint) bookHint.textContent = "MTD · journal";
    }
  }

  if (nextEl) {
    if (positionCount === null) {
      nextEl.textContent = "Connect";
      if (nextHint) nextHint.textContent = "link Schwab or add manual book";
    } else if (alertCount > 0) {
      nextEl.textContent = "Review";
      if (nextHint) nextHint.textContent = "Risk tab · open flags";
    } else if (bookPnl == null) {
      nextEl.textContent = "Book";
      if (nextHint) nextHint.textContent = "load calendar / journal";
    } else {
      nextEl.textContent = "Monitor";
      if (nextHint) nextHint.textContent = "Positions · Risk · Book";
    }
  }

  if (statusStrip && statusTitle && statusDetail) {
    if (positionCount === null) {
      statusStrip.dataset.state = "empty";
      if (statusPill) statusPill.textContent = "Empty";
      statusTitle.textContent = "No portfolio loaded.";
      statusDetail.textContent = "Connect Schwab or add a manual portfolio to unlock Research home.";
    } else if (alertCount > 0 || bookPnl == null) {
      statusStrip.dataset.state = "partial";
      if (statusPill) statusPill.textContent = "Partial";
      statusTitle.textContent = "Portfolio loaded with gaps.";
      statusDetail.textContent = `${formatCount(positionCount)} positions · ${
        alertCount > 0 ? `${formatCount(alertCount)} alert(s)` : "no alerts"
      } · ${bookPnl == null ? "book unavailable" : "book current"}.`;
    } else {
      statusStrip.dataset.state = "ready";
      if (statusPill) statusPill.textContent = "Ready";
      statusTitle.textContent = "Portfolio triage looks healthy.";
      statusDetail.textContent = `${formatCount(positionCount)} positions · no concentration alert · book current.`;
    }
  }
}

function setLoading(textMap = {}) {
  if (textMap.scan) document.getElementById("scanMeta").textContent = textMap.scan;
  if (textMap.portfolio) document.getElementById("portfolioMeta").textContent = textMap.portfolio;
}

function validateRuntimeContract(publicCfg, runtimeContract) {
  const cfgMode = safeText(publicCfg?.runtime_mode || (publicCfg?.saas_mode ? "saas" : "local")).toLowerCase();
  const contractMode = safeText(runtimeContract?.runtime_mode || "").toLowerCase();
  const cfgTransport = safeText(publicCfg?.scan_transport || "").toLowerCase();
  const contractTransport = safeText(runtimeContract?.scan_transport || "").toLowerCase();
  if (!contractMode || !contractTransport) return;
  if (cfgMode !== contractMode || cfgTransport !== contractTransport) {
    const message =
      `Frontend/runtime contract mismatch (${cfgMode}/${cfgTransport} vs ${contractMode}/${contractTransport}). ` +
      "Deploy matching frontend+API revisions before continuing.";
    logEvent({ kind: "system", severity: "error", message });
    updateActionCenter({
      title: "App version mismatch",
      message,
      severity: "error",
    });
  }
}

let _scanDetailChart = null;
let _scanDetailResizeObserver = null;
let _scanDetailSignal = null;
let _scanDetailChartTicker = null;
let _scanDetailOverlayDispose = null;

function syncScanDetailStageButton(signal) {
  const btn = document.getElementById("scanDetailStageBtn");
  const researchBtn = document.getElementById("scanDetailResearchBtn");
  const sig = normalizeScanSignal(signal || {});
  const ticker = safeText(sig.ticker || sig.symbol || "");
  if (!ticker) {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Add to approval queue";
      btn.title = "Select a candidate first.";
    }
    if (researchBtn) {
      researchBtn.disabled = true;
      researchBtn.title = "Select a candidate first.";
    }
    return;
  }
  if (researchBtn) {
    researchBtn.disabled = false;
    researchBtn.textContent = `Research ${ticker}`;
    researchBtn.title = `Open ${ticker} in Research quick check.`;
  }
  if (!btn) return;
  const stageable = isScanSignalStageable(sig);
  btn.disabled = !stageable;
  btn.textContent = stageable ? `Add ${ticker} to queue` : `${ticker} can't queue yet`;
  btn.title = stageable
    ? "Send to Pending approvals for review — not a live order yet."
    : "This setup did not pass scan filters. Pick a tradeable row or adjust gates.";
}

function renderScanDetailChartMessage(message) {
  const container = document.getElementById("scanDetailChartContainer");
  if (!container) return;
  container.innerHTML = `<p class="muted">${safeText(message || "Chart unavailable.")}</p>`;
}

function buildScanBriefNoteText(row, sections) {
  const ticker = safeText(row?.ticker || row?.symbol || "?");
  const lines = [
    `${ticker} decision brief`,
    "",
    `Setup summary: ${safeText(sections.setupSummary)}`,
    `Expected move window: ${safeText(sections.expectedMoveWindow)}`,
    "",
    "Key risks:",
    ...(sections.keyRisks || []).map((x) => `- ${safeText(x)}`),
    "",
    "Catalyst notes:",
    ...(sections.catalystNotes || []).map((x) => `- ${safeText(x)}`),
    "",
    "Forensic flags:",
    ...(sections.forensicFlags || []).map((x) => `- ${safeText(x)}`),
    "",
    "SEC notes:",
    ...(sections.secNotes || []).map((x) => `- ${safeText(x)}`),
    "",
    "Entry/stop ideas:",
    ...(sections.entryStopIdeas || []).map((x) => `- ${safeText(x)}`),
  ];
  return lines.join("\n");
}

function briefPillClassForScore(score) {
  const n = optionalNum(score);
  if (n === null) return "neutral";
  if (n >= 70) return "good";
  if (n >= 55) return "warn";
  return "bad";
}

function briefPillClassForConfidence(label) {
  const v = safeText(label).toUpperCase();
  if (v === "HIGH") return "good";
  if (v === "MEDIUM" || v === "MED") return "warn";
  if (v === "LOW") return "bad";
  return "neutral";
}

function briefPillClassForConviction(conviction) {
  const n = optionalNum(conviction);
  if (n === null) return "neutral";
  if (n >= 35) return "good";
  if (n >= 10) return "warn";
  return "bad";
}

function briefPillClassForPup(pUp) {
  const n = optionalNum(pUp);
  if (n === null) return "neutral";
  if (n >= 0.6) return "good";
  if (n >= 0.52) return "warn";
  return "bad";
}

function summarizeBriefRiskSeverity(items) {
  const rows = Array.isArray(items) ? items.map((x) => safeText(x).toLowerCase()) : [];
  if (!rows.length) return { label: "Risk low", cls: "good" };
  const highWords = ["blocked", "high", "distress", "manipulator", "negative surprise", "event risk"];
  const medWords = ["medium", "watch", "unknown", "low/unknown"];
  const hasHigh = rows.some((r) => highWords.some((w) => r.includes(w)));
  if (hasHigh || rows.length >= 3) return { label: "Risk high", cls: "bad" };
  const hasMed = rows.some((r) => medWords.some((w) => r.includes(w)));
  if (hasMed || rows.length === 2) return { label: "Risk medium", cls: "warn" };
  return { label: "Risk low", cls: "good" };
}

function renderScanDetailBrief(row, brief) {
  const container = document.getElementById("scanDetailBrief");
  if (!container) return;
  if (!row) {
    container.innerHTML = `<p class="muted">Select a candidate to load the bullet decision card.</p>`;
    return;
  }
  const fallbackSetup =
    `Score ${getCompositeScore(row) === null ? "—" : formatDecimal(getCompositeScore(row), 1)}, ` +
    `confidence ${formatConfidenceLabel((row.advisory || {}).confidence_bucket ?? row.confidence_bucket ?? row.advisory_confidence)}, ` +
    `strategy ${safeText((row.strategy_attribution || {}).top_live || "unknown")}.`;
  const setupSummary = safeText(brief?.setup_summary || fallbackSetup);
  const keyRisks =
    Array.isArray(brief?.key_risks) && brief.key_risks.length
      ? brief.key_risks
      : ["No hard risk blockers returned by the scanner."];
  const catalystNotes =
    Array.isArray(brief?.catalyst_notes) && brief.catalyst_notes.length
      ? brief.catalyst_notes
      : ["No explicit catalyst note returned."];
  const forensicFlags =
    Array.isArray(brief?.forensic_flags) && brief.forensic_flags.length
      ? brief.forensic_flags
      : ["No forensic flags returned."];
  const secNotes =
    Array.isArray(brief?.sec_notes) && brief.sec_notes.length
      ? brief.sec_notes
      : ["No SEC notes returned."];
  const expectedMoveWindow = safeText(brief?.expected_move_window || "10 trading days");
  const entryStopIdeas =
    Array.isArray(brief?.entry_stop_ideas) && brief.entry_stop_ideas.length
      ? brief.entry_stop_ideas
      : ["Entry/stop ideas not returned."];
  const confidenceLabel = formatConfidenceLabel((row.advisory || {}).confidence_bucket ?? row.confidence_bucket ?? row.advisory_confidence);
  const conviction = getConvictionScore(row);
  const pUp = getCalibratedPUp(row);
  const score = getCompositeScore(row);
  const riskSeverity = summarizeBriefRiskSeverity(keyRisks);
  const sections = {
    setupSummary,
    keyRisks,
    catalystNotes,
    forensicFlags,
    secNotes,
    expectedMoveWindow,
    entryStopIdeas,
  };
  const noteText = buildScanBriefNoteText(row, sections);
  const asList = (items) => `<ul>${items.map((x) => `<li>${escapeHtml(safeText(x))}</li>`).join("")}</ul>`;
  const detailBlock = (title, items, open = false) => `
    <details class="scan-brief-detail"${open ? " open" : ""}>
      <summary>${escapeHtml(title)}</summary>
      ${asList(items)}
    </details>
  `;
  container.innerHTML = `
    <div class="scan-brief-header">
      <strong>Setup summary</strong>
      <div class="scan-brief-badges">
        <span class="pill ${briefPillClassForScore(score)}">Score ${score === null ? "—" : formatDecimal(score, 1)}</span>
        <span class="pill ${briefPillClassForConfidence(confidenceLabel)}">Confidence ${escapeHtml(confidenceLabel || "—")}</span>
        <span class="pill ${briefPillClassForConviction(conviction)}">Conviction ${conviction === null ? "—" : formatDecimal(conviction, 1)}</span>
        <span class="pill ${briefPillClassForPup(pUp)}">P(up) ${pUp === null ? "—" : pct(pUp, 1)}</span>
        <span class="pill ${riskSeverity.cls}">${escapeHtml(riskSeverity.label)}</span>
      </div>
    </div>
    <p class="scan-brief-setup">${escapeHtml(setupSummary)}</p>
    <div class="scan-brief-actions">
      <button id="scanDetailBriefCopyBtn" type="button" class="btn small secondary">Copy brief</button>
      <button id="scanDetailBriefStageNoteBtn" type="button" class="btn small secondary">Use as stage note</button>
    </div>
    <div class="scan-brief-sections">
      ${detailBlock("Key risks", keyRisks, true)}
      ${detailBlock("Catalyst notes", catalystNotes)}
      ${detailBlock("Forensic flags", forensicFlags)}
      ${detailBlock("SEC notes", secNotes)}
      <details class="scan-brief-detail">
        <summary>Expected move window</summary>
        <p>${escapeHtml(expectedMoveWindow)}</p>
      </details>
      ${detailBlock("Entry/stop ideas", entryStopIdeas)}
    </div>
  `;
  const copyBtn = document.getElementById("scanDetailBriefCopyBtn");
  copyBtn?.addEventListener("click", async () => {
    const ok = await copyTextToClipboard(noteText);
    if (ok) {
      updateActionCenter({
        title: "Brief copied",
        message: "Decision brief copied to clipboard.",
        severity: "success",
      });
    } else {
      updateActionCenter({
        title: "Copy failed",
        message: "Could not copy brief to clipboard.",
        severity: "warn",
      });
    }
  });
  const stageNoteBtn = document.getElementById("scanDetailBriefStageNoteBtn");
  const isStageable = isScanSignalStageable(row);
  if (stageNoteBtn) {
    stageNoteBtn.disabled = !isStageable;
    stageNoteBtn.title = isStageable
      ? "Open stage dialog with this brief prefilled as note."
      : "Only qualified rows can be staged in current mode.";
    stageNoteBtn.addEventListener("click", () => {
      if (!isStageable) return;
      openQueueScanDialog(row);
      const noteInput = document.getElementById("queueScanNote");
      if (noteInput) noteInput.value = noteText;
    });
  }
}

async function loadScanDetailBrief(row) {
  if (!row || !row.ticker) {
    renderScanDetailBrief(null, null);
    syncScanDetailBriefState("empty");
    return;
  }
  syncScanDetailBriefState("loading");
  renderScanDetailBrief(row, { setup_summary: "Loading decision brief..." });
  const out = await api.get(`/api/decision-card/${encodeURIComponent(row.ticker)}`);
  if (!out.ok) {
    renderScanDetailBrief(row, null);
    syncScanDetailBriefState("error");
    return;
  }
  renderScanDetailBrief(row, out.data?.brief || null);
  const stageable = isScanSignalStageable(row);
  syncScanDetailBriefState(stageable ? "success" : "partial");
}

function getScanDetailChartWidth(container) {
  if (!container) return 320;
  const measured = Math.round(container.getBoundingClientRect().width || container.clientWidth || 0);
  const panel = container.closest(".scan-detail-panel");
  const panelWidth = panel ? Math.round(panel.getBoundingClientRect().width || panel.clientWidth || 0) : 0;
  const viewportCap = Math.max(220, Math.round((window.innerWidth || 0) - 96));
  const panelCap = panelWidth > 0 ? Math.max(220, panelWidth - 24) : viewportCap;
  const fallback = Math.min(panelCap, viewportCap);
  const safeMeasured = measured > 0 ? measured : fallback;
  // Clamp against parent panel and viewport so canvas sizing cannot blow out layout.
  return Math.max(220, Math.min(safeMeasured, panelCap, viewportCap));
}

async function renderScanDetailChart(ticker) {
  const container = document.getElementById("scanDetailChartContainer");
  if (!container) return;
  if (_scanDetailResizeObserver) {
    _scanDetailResizeObserver.disconnect();
    _scanDetailResizeObserver = null;
  }
  if (typeof _scanDetailOverlayDispose === "function") {
    try {
      _scanDetailOverlayDispose();
    } catch {
      // ignore overlay cleanup failures
    }
    _scanDetailOverlayDispose = null;
  }
  if (_scanDetailChart) {
    try {
      _scanDetailChart.remove();
    } catch {
      // ignore chart cleanup failures
    }
    _scanDetailChart = null;
  }
  _scanDetailChartTicker = null;
  if (!ticker) {
    renderScanDetailChartMessage("Select a ticker to load chart data.");
    return;
  }
  if (typeof LightweightCharts === "undefined") {
    renderScanDetailChartMessage("Chart library unavailable.");
    return;
  }

  container.innerHTML = "";
  const chartHost = document.createElement("div");
  chartHost.className = "scan-detail-chart-canvas";
  container.appendChild(chartHost);
  const out = await api.get(`/api/chart/${encodeURIComponent(ticker)}`);
  if (!out.ok || !out.data?.candles?.length) {
    renderScanDetailChartMessage(`No chart data available for ${ticker}.`);
    return;
  }

  const candles = out.data.candles;
  const chartTheme = getLightweightChartsProps();
  const chart = LightweightCharts.createChart(chartHost, {
    width: getScanDetailChartWidth(container),
    height: 240,
    layout: chartTheme.layout,
    grid: chartTheme.grid,
    rightPriceScale: chartTheme.rightPriceScale,
    timeScale: { ...chartTheme.timeScale, timeVisible: false },
  });
  const candleSeries = chart.addCandlestickSeries(chartTheme.candlestick);
  candleSeries.setData(candles);
  chart.timeScale().fitContent();
  _scanDetailChart = chart;
  _scanDetailChartTicker = ticker;
  const signal = _scanDetailSignal && safeText(_scanDetailSignal.ticker || _scanDetailSignal.symbol).toUpperCase() === safeText(ticker).toUpperCase()
    ? _scanDetailSignal
    : null;
  if (signal) {
    _scanDetailOverlayDispose = applyScanDetailOverlays(chart, candleSeries, signal, candles);
    const diag = state.lastScanDiagnostics || {};
    renderChartOverlayLegend(container, signal, {
      scanBlocked: safeNum(diag.scan_blocked, 0) > 0,
    });
  }
  _scanDetailResizeObserver = new ResizeObserver(() => {
    if (_scanDetailChart) _scanDetailChart.applyOptions({ width: getScanDetailChartWidth(container) });
  });
  _scanDetailResizeObserver.observe(container);
}

async function renderScanDetail(sig) {
  const row = normalizeScanSignal(sig || {});
  const ticker = safeText(row.ticker || row.symbol || "");
  if (ticker && ticker !== state.selectedScanTicker) {
    trackUiEvent("candidate_opened", { ticker, source: "scan_table" });
  }
  _scanDetailSignal = ticker ? row : null;
  state.selectedScanTicker = ticker;
  highlightSelectedScanRow(ticker);
  const advisory = row.advisory || {};
  const rank = getRankScore(row);
  const score = getCompositeScore(row);
  const conviction = getConvictionScore(row);
  const pUp = getCalibratedPUp(row);
  const confidence = formatConfidenceLabel(advisory.confidence_bucket ?? row.confidence_bucket ?? row.advisory_confidence);
  const strategy = formatStrategyLabel(row?.strategy_attribution?.top_live || "—");
  const reliability = getReliabilityScore(row);
  const reliabilityLabel =
    reliability === null
      ? "—"
      : `${formatDecimal(reliability, 1)}${isReliabilityEstimated(row) ? " (est.)" : ""}`;
  const edge = getEdgeScore(row);
  const execution = getExecutionScore(row);
  const ev10d = getEv10d(row);

  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  setText("scanDetailTicker", ticker || "Select a ticker");
  setText("scanDetailStrategy", ticker ? `Top strategy: ${strategy}` : "Choose a scan row to review chart and scoring context.");
  setText("scanDetailRank", rank === null ? "—" : formatDecimal(rank, 1));
  setText("scanDetailPrice", row.price || row.current_price ? formatMoney(row.price || row.current_price) : "—");
  setText("scanDetailScore", score === null ? "—" : formatDecimal(score, 1));
  setText("scanDetailPup", pUp === null ? "—" : pct(pUp, 1));
  setText("scanDetailConfidence", confidence || "—");
  setText("scanDetailConviction", conviction === null ? "—" : formatDecimal(conviction, 1));
  setText("scanDetailSector", safeText(row.sector_etf || "—"));
  setText("scanDetailEdge", edge === null ? "—" : formatDecimal(edge, 1));
  setText("scanDetailReliability", reliabilityLabel);
  setText("scanDetailExecution", execution === null ? "—" : formatDecimal(execution, 1));
  setText("scanDetailEv10d", ev10d === null ? "—" : pct(ev10d, 2));
  if (!ticker) {
    setOperationsStatusStrip(
      "scanDetailStatusStrip",
      "empty",
      "No ticker selected.",
      "Pick a candidate row to review evidence before staging.",
    );
    syncScanDetailPanelState("empty");
    syncScanDetailBriefState("empty");
    renderOperationsPanelSnapshot("scanDetailSnapshot", "scanDetailPanel", "empty", {
      hint: "Detail path: select a scan row → review brief → stage",
      kpis: [
        { label: "SCORE", sub: "composite rank", value: "—", tone: "neutral" },
        { label: "RELIABILITY", sub: "evidence quality", value: "—", tone: "neutral" },
        { label: "DATA", sub: "stageable", value: "Clear", tone: "neutral" },
      ],
      lines: ["No ticker selected.", "Pick a candidate row to review evidence before staging."],
    });
  } else {
    const stageable = isScanSignalStageable(row);
    const missingEvidence = [
      score === null ? "score" : "",
      reliability === null ? "reliability" : "",
      row.price == null && row.current_price == null ? "price" : "",
    ].filter(Boolean);
    const detailState =
      stageable && missingEvidence.length === 0 ? "success" : "partial";
    setOperationsStatusStrip(
      "scanDetailStatusStrip",
      detailState,
      stageable ? `${ticker} ready for evidence review.` : `${ticker} is filtered or guarded.`,
      missingEvidence.length
        ? `Missing ${missingEvidence.join(", ")}; keep guardrails visible before staging.`
        : `Score ${score === null ? "—" : formatDecimal(score, 1)} · Reliability ${reliabilityLabel} · Execution ${execution === null ? "—" : formatDecimal(execution, 1)}.`,
    );
    syncScanDetailPanelState(detailState);
    syncScanDetailBriefState("loading");
    renderOperationsPanelSnapshot("scanDetailSnapshot", "scanDetailPanel", detailState, {
      hint: `Reviewing ${ticker} — chart, brief, and staging controls`,
      kpis: [
        {
          label: "SCORE",
          sub: "composite rank",
          value: score === null ? "—" : formatDecimal(score, 1),
          tone: score === null ? "warn" : "success",
        },
        {
          label: "RELIABILITY",
          sub: "evidence quality",
          value: reliability === null ? "—" : formatDecimal(reliability, 1),
          tone: reliability === null ? "warn" : "success",
        },
        {
          label: "DATA",
          sub: "stageable",
          value: stageable ? "Ready" : "Filtered",
          tone: stageable ? "success" : "warn",
        },
      ],
      meters: {
        reliability: reliability,
        execution: execution,
      },
      lines: [
        stageable ? `${ticker} ready for evidence review.` : `${ticker} is filtered or guarded.`,
        missingEvidence.length
          ? `Missing ${missingEvidence.join(", ")}; keep guardrails visible before staging.`
          : `Score ${score === null ? "—" : formatDecimal(score, 1)} · Reliability ${reliabilityLabel} · Execution ${execution === null ? "—" : formatDecimal(execution, 1)}.`,
      ],
    });
  }
  const trustEl = document.getElementById("scanDetailTrust");
  if (trustEl) {
    trustEl.innerHTML = ticker ? renderSignalTrustRow(row) : "";
  }
  syncScanDetailStageButton(_scanDetailSignal);
  updateWorkflowKanban({ selectedTicker: ticker });
  await loadScanDetailBrief(row);
  await renderScanDetailChart(ticker);
}

function highlightSelectedScanRow(ticker) {
  const body = document.getElementById("scanTableBody");
  if (!body) return;
  const selected = safeText(ticker).toUpperCase();
  body.querySelectorAll("tr[data-scan-ticker]").forEach((tr) => {
    const rowTicker = safeText(tr.getAttribute("data-scan-ticker")).toUpperCase();
    tr.classList.toggle("is-active", Boolean(selected) && rowTicker === selected);
  });
}

// Update both `state.latestSignals` (kept-only — used for trade staging /
// pending-queue counts) and `state.latestShortlistSignals` (full Stage-B
// shortlist with `_filter_status` tags — used for the candidate table).
// Returns the rows that should drive `renderScanRows`: prefers the full
// shortlist when the backend provided one, falls back to kept signals so
// older API versions still render.
function applyScanResponseSignals(payload = {}) {
  const signals = Array.isArray(payload?.signals) ? payload.signals : [];
  const shortlist = Array.isArray(payload?.shortlist_signals) ? payload.shortlist_signals : [];
  state.latestSignals = signals;
  state.latestShortlistSignals = shortlist;
  return shortlist.length > 0 ? shortlist : signals;
}

const SCAN_MODE_DEFAULT = "balanced";
const SCAN_MODE_PROFILES = {
  balanced: {
    label: "Balanced",
    minScore: 50,
    minVolumeRatio: 1.1,
  },
  strict: {
    label: "Strict",
    minScore: 60,
    minVolumeRatio: 1.2,
  },
};
const AUTO_SCAN_COOLDOWN_MS = 20 * 60 * 1000;
const AUTO_SCAN_STORAGE_KEY = "tradingbot.scan.auto_run_at";

function getScanMode() {
  const raw = safeText(document.getElementById("scanModeSelect")?.value || SCAN_MODE_DEFAULT).toLowerCase();
  return Object.prototype.hasOwnProperty.call(SCAN_MODE_PROFILES, raw) ? raw : SCAN_MODE_DEFAULT;
}

function getScanModeProfile(mode = getScanMode()) {
  return SCAN_MODE_PROFILES[mode] || SCAN_MODE_PROFILES[SCAN_MODE_DEFAULT];
}

async function refreshScanDeltas() {
  const out = await api.get("/api/cockpit/deltas");
  if (out.ok) renderScanDeltaStrip(out.data);
}

function updateScanModeHelperText() {
  const helperEl = document.getElementById("scanModeHelperText");
  if (!helperEl) return;
  const mode = getScanMode();
  const profile = getScanModeProfile(mode);
  helperEl.textContent =
    `${profile.label}: score >= ${profile.minScore}, volume ratio >= ${profile.minVolumeRatio.toFixed(1)}. ` +
    "This scan uses softer quality filters for this run (may differ from saved settings).";
}

function mergeScanRunOptionsWithMode(baseOptions) {
  const body = baseOptions && typeof baseOptions === "object" ? { ...baseOptions } : {};
  const mode = getScanMode();
  const profile = getScanModeProfile(mode);
  const rawOverrides = body.strategy_overrides;
  const strategyOverrides =
    rawOverrides && typeof rawOverrides === "object" && !Array.isArray(rawOverrides)
      ? { ...rawOverrides }
      : {};
  strategyOverrides.quality_gates_mode = "soft";
  strategyOverrides.quality_min_signal_score = profile.minScore;
  strategyOverrides.quality_require_breakout_volume = true;
  strategyOverrides.quality_breakout_volume_min_ratio = profile.minVolumeRatio;
  body.strategy_overrides = strategyOverrides;
  return body;
}

function applySchwabConnectButtonVisibility() {
  // Single-path onboarding keeps one CTA visible; availability checks happen on click.
}

async function copyTextToClipboard(text) {
  const value = String(text || "");
  if (!value) return false;
  if (navigator?.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  const ta = document.createElement("textarea");
  ta.value = value;
  ta.setAttribute("readonly", "");
  ta.style.position = "absolute";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(ta);
  return ok;
}

function setAuthDebugValue(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.value = safeText(value || "—");
}

async function refreshAuthDebugPanel() {
  const cfg = state.publicConfig || {};
  const authSetup = cfg?.auth_setup && typeof cfg.auth_setup === "object" ? cfg.auth_setup : {};
  const hasSupabaseUi = Boolean(cfg?.supabase?.url && cfg?.supabase?.anon_key);
  const jwtReady =
    authSetup.jwt_verification_ready === true ||
    (authSetup.jwt_verification_ready === undefined && authSetup.jwt_secret_configured === true);

  setAuthDebugValue("authDebugSupabaseUi", hasSupabaseUi ? "ready" : "missing SUPABASE_URL / SUPABASE_ANON_KEY");
  setAuthDebugValue("authDebugJwtVerify", jwtReady ? "ready" : "missing SUPABASE_URL and/or SUPABASE_JWT_SECRET");

  const hint = document.getElementById("authDebugHint");
  if (hint) hint.textContent = "Checking sign-in status…";

  try {
    const out = await api.get("/api/auth/session");
    if (!out.ok) {
      setAuthDebugValue("authDebugSession", `error: ${out.error || "request failed"}`);
      setAuthDebugValue("authDebugSubject", "—");
      setAuthDebugValue("authDebugEmail", "—");
      if (hint) hint.textContent = "Session check failed. Verify your email session and retry.";
      return;
    }
    const data = out.data || {};
    const authed = Boolean(data.authenticated);
    setAuthDebugValue("authDebugSession", authed ? "yes" : "no");
    setAuthDebugValue("authDebugSubject", data.sub || "—");
    setAuthDebugValue("authDebugEmail", data.email || "—");
    if (hint) {
      hint.textContent = authed
        ? "Session cookie is valid for protected APIs."
        : "No auth session cookie yet. Use Verify email to unlock protected APIs.";
    }
  } catch (err) {
    setAuthDebugValue("authDebugSession", "error");
    setAuthDebugValue("authDebugSubject", "—");
    setAuthDebugValue("authDebugEmail", "—");
    if (hint) hint.textContent = `Session check error: ${safeText(err?.message || err || "unknown")}`;
  }
}

async function loadConfig() {
  const tokenInput = document.getElementById("jwtInput");
  const saveBtn = document.getElementById("saveJwtBtn");
  const copyBtn = document.getElementById("copyJwtBtn");
  const manualDetails = document.getElementById("manualJwtDetails");
  const manualSummary = document.getElementById("manualJwtSummary");
  const supabaseBlock = document.getElementById("supabaseAuthBlock");

  let publicCfg = {
    supabase: null,
    saas_mode: false,
    runtime_mode: "local",
    schwab_oauth: false,
    schwab_market_oauth: false,
    auth_setup: null,
  };
  // Bootstrap GETs are idempotent — retry with capped backoff on transient
  // failures so a single packet loss doesn't poison the entire dashboard.
  // Mutations (POST/PATCH/DELETE) are NEVER auto-retried; they require an
  // explicit user click so unintended duplicates can't slip through.
  const cfgOut = await retryGet(() => api.get("/api/public-config", { timeoutMs: 20000 }), {
    attempts: 3,
    baseDelayMs: 500,
  });
  if (cfgOut?.ok && cfgOut?.data) {
    publicCfg = { ...publicCfg, ...cfgOut.data };
  } else if (cfgOut?.error) {
    logEvent({ kind: "system", severity: "warn", message: `Public config unavailable: ${cfgOut.error}` });
  }
  const runtimeOut = await retryGet(
    () => api.get("/api/runtime-contract", { timeoutMs: 20000 }),
    { attempts: 3, baseDelayMs: 500 },
  );
  if (runtimeOut?.ok && runtimeOut?.data) {
    state.runtimeContract = runtimeOut.data;
    validateRuntimeContract(publicCfg, runtimeOut.data);
  } else {
    state.runtimeContract = null;
  }
  state.publicConfig = publicCfg;
  state.sseEnabled = publicCfg?.sse_enabled === true;
  state.allowManualJwt = publicCfg?.manual_jwt_entry_enabled !== false;
  applySchwabConnectButtonVisibility();
  renderLiveTradingSaasPanel();

  const implLink = document.getElementById("implementationGuideLink");
  const implUrl = (publicCfg?.implementation_guide_url || "").trim();
  if (implLink) {
    if (implUrl) {
      implLink.href = implUrl;
      implLink.classList.remove("hidden");
    } else {
      implLink.classList.add("hidden");
      implLink.setAttribute("href", "#");
    }
  }

  const hasSupabaseUi = Boolean(publicCfg?.supabase?.url && publicCfg?.supabase?.anon_key);
  const manualJwtAllowed = Boolean(state.allowManualJwt);
  if (!manualJwtAllowed) clearStoredApiJwt();
  if (hasSupabaseUi && supabaseBlock) {
    supabaseBlock.classList.remove("hidden");
    if (manualDetails) {
      manualDetails.classList.add("hidden");
      manualDetails.open = false;
    }
    await initSupabaseAuth(publicCfg.supabase.url, publicCfg.supabase.anon_key);
  } else {
    if (supabaseBlock) supabaseBlock.classList.add("hidden");
    if (manualDetails) {
      manualDetails.classList.toggle("hidden", !manualJwtAllowed);
      manualDetails.open = false;
    }
    if (manualSummary) {
      manualSummary.textContent = "Session token";
      manualSummary.classList.add("manual-jwt-summary--hidden");
    }
    markAuthReady();
  }

  wireManualJwtBlock({
    input: tokenInput,
    saveBtn,
    copyBtn,
    allowManual: manualJwtAllowed,
    normalizeJwt: normalizeUserJwt,
    isProbablyJwt: isProbablyAccessJwt,
    badShapeHint: JWT_BAD_SHAPE_HINT,
    readStoredToken: readStoredApiJwt,
    saveToken: (token) => {
      localStorage.setItem(AUTH_TOKEN_KEY, token);
      clearLegacyApiJwtKeys();
      void createCookieAuthSession(token);
    },
    clearToken: () => {
      clearStoredApiJwt();
      void clearCookieAuthSession();
    },
    onMessage: (text, severity) => logEvent({ kind: "system", severity, message: text }),
  });
  state.config = { auth_mode: hasSupabaseUi ? "supabase" : "jwt" };
  await refreshAuthDebugPanel();
  const authSetup = publicCfg?.auth_setup && typeof publicCfg.auth_setup === "object" ? publicCfg.auth_setup : {};
  const saasHost = Boolean(publicCfg?.saas_mode);
  const originHint = window.location.origin || "";
  const jwtReady =
    authSetup.jwt_verification_ready === true ||
    (authSetup.jwt_verification_ready === undefined && authSetup.jwt_secret_configured === true);
  if (saasHost && !jwtReady) {
    updateActionCenter({
      title: "Server cannot verify Supabase tokens",
      message:
        "Set SUPABASE_URL (for ES256/RS256 JWKS) and/or SUPABASE_JWT_SECRET (for legacy HS256) from Supabase → Project Settings → API on your host (e.g. Render → Environment), then redeploy.",
      severity: "error",
    });
  } else if (saasHost && authSetup.supabase_sign_in_available === false) {
    updateActionCenter({
      title: "Hosted sign-in not configured",
      message: hasSupabaseUi
        ? "Use Verify email when prompted. Your session token is used automatically."
        : `This server did not expose Supabase browser sign-in (set SUPABASE_URL and SUPABASE_ANON_KEY in Render to match your local .env). In Supabase → Authentication → URL configuration, add ${originHint} to Site URL and Redirect URLs.`,
      severity: "warn",
    });
  } else if (saasHost) {
    updateActionCenter({
      title: "Authentication Required",
      message: hasSupabaseUi
        ? "Use Verify email when prompted. Your session token is handled automatically."
        : "Supabase browser auth is required to access protected APIs.",
      severity: "warn",
    });
  } else if (hasSupabaseUi) {
    updateActionCenter({
      title: "Local sign-in available",
      message: "Use Verify email when prompted. Your session token is handled automatically.",
      severity: "info",
    });
  } else if (publicCfg?.api_key_required) {
    const hasKey = Boolean((localStorage.getItem("tradingbot.api_key") || "").trim());
    updateActionCenter({
      title: hasKey ? "Local mode" : "API key required",
      message: hasKey
        ? "Local dashboard — WEB_API_KEY saved in this browser. Run Scan to load candidates."
        : "Local dashboard — enter your WEB_API_KEY when prompted (same value as in schwab_skill/.env).",
      severity: hasKey ? "info" : "warn",
    });
  } else {
    updateActionCenter({
      title: "Local mode",
      message: "No sign-in required on localhost. Run Scan to load candidates.",
      severity: "info",
    });
  }

  if (!saasHost && publicCfg?.api_key_required && ensureApiKeyOnLoad()) {
    updateActionCenter({
      title: "Local mode",
      message: "WEB_API_KEY saved in this browser. Run Scan to load candidates.",
      severity: "info",
    });
  }

  const params = new URLSearchParams(window.location.search);
  const section = safeText(params.get("section") || "").trim().toLowerCase();
  if (section === "connect") {
    applyScreenMode("settings", { updateUrl: false });
    const onboardingEl = document.getElementById("onboardingSection");
    onboardingEl?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  const oauthSt = params.get("schwab_oauth");
  const marketOauthSt = params.get("schwab_market_oauth");
  const billingSt = params.get("billing");
  if (oauthSt || marketOauthSt) {
    const msg = params.get("message") || "";
    clearOAuthQueryParams(["schwab_oauth", "schwab_market_oauth", "message"]);
    applySchwabConnectButtonVisibility();

    if (oauthSt) {
      if (oauthSt === "ok") {
        logEvent({ kind: "system", severity: "info", message: "Schwab account linked successfully." });
        updateActionCenter({
          title: "Schwab",
          message: "Brokerage side linked (balances, positions, orders). If you have not yet, also connect market data.",
          severity: "success",
        });
        try { showToast("Schwab account linked.", "success", 4000); } catch { /* ignore */ }
        void trackFunnelMilestoneOnce(FUNNEL_EVENTS.AUTH_LINKED, {
          source: "oauth_callback_account",
        });
      } else {
        logEvent({ kind: "system", severity: "error", message: `Schwab OAuth: ${msg || "failed"}` });
        updateActionCenter({ title: "Schwab OAuth", message: msg || "Connection failed.", severity: "error" });
        try { showToast(`Schwab OAuth: ${msg || "failed"}`, "error", 6000); } catch { /* ignore */ }
      }
    }
    if (marketOauthSt) {
      if (marketOauthSt === "ok") {
        logEvent({ kind: "system", severity: "info", message: "Schwab market data linked successfully." });
        updateActionCenter({
          title: "Schwab market",
          message: "Market data linked (quotes and history for scans).",
          severity: "success",
        });
        try { showToast("Schwab market data linked.", "success", 4000); } catch { /* ignore */ }
        void trackFunnelMilestoneOnce(FUNNEL_EVENTS.AUTH_LINKED, {
          source: "oauth_callback_market",
        });
      } else {
        logEvent({ kind: "system", severity: "error", message: `Schwab market OAuth: ${msg || "failed"}` });
        updateActionCenter({
          title: "Schwab market OAuth",
          message: msg || "Connection failed.",
          severity: "error",
        });
        try { showToast(`Schwab market OAuth: ${msg || "failed"}`, "error", 6000); } catch { /* ignore */ }
      }
    }

    // After any Schwab OAuth callback, the server has updated /api/onboarding/status
    // (schwab_linked, wizard_required, etc.). Re-pull it so the wizard stepper, CTA,
    // and connection meta line reflect the new state instead of the cached pre-link view.
    try {
      await refreshOnboarding();
    } catch (err) {
      logEvent({
        kind: "system",
        severity: "warn",
        message: `Could not refresh onboarding after OAuth: ${err?.message || err}`,
      });
    }
  }
  if (billingSt) {
    clearOAuthQueryParams(["billing"]);
    if (billingSt === "checkout_success") {
      updateActionCenter({
        title: "Billing updated",
        message: "Checkout completed. Refreshing account subscription state.",
        severity: "success",
      });
      void trackProductEvent("billing_checkout_success", { source: "redirect_query" });
      void refreshAccountMe();
    } else if (billingSt === "checkout_cancel") {
      updateActionCenter({
        title: "Checkout canceled",
        message: "No charge was made. You can restart checkout anytime.",
        severity: "warn",
      });
      void trackProductEvent("billing_checkout_canceled", { source: "redirect_query" });
    }
  }
  applyConnectFirstExperience();
}

/**
 * Restore scan table + diagnostics from persisted last_scan (local) or scan-results (SaaS).
 * Without this, the UI stayed empty after refresh even when a scan had completed.
 */
async function hydrateScanTableFromStatus(status) {
  const ls = status.last_scan;
  if (!ls || !ls.at) return;

  const diag = ls.diagnostics || ls.diagnostics_summary || {};
  const metaEl = document.getElementById("scanMeta");
  const strat = ls.strategy_summary || null;

  if (state.publicConfig.saas_mode) {
    const jobId = safeText(ls.job_id || "").trim();
    const foundRaw = ls.signals_found;
    const foundN = foundRaw === null || foundRaw === undefined ? null : safeNum(foundRaw, 0);

    if (jobId && foundN === 0) {
      const tableRows = applyScanResponseSignals({ signals: [], shortlist_signals: ls.shortlist_signals });
      const headline = diagnosticsHeadline(diag);
      if (metaEl) metaEl.textContent = (headline || buildScanMeta([], 0)) + formatStrategySummary(strat);
      updateTopStrategyChip(strat);
      renderDiagnostics(diag);
      renderScanRows(tableRows);
      return;
    }

    const url = jobId
      ? `/api/scan-results?limit=5000&job_id=${encodeURIComponent(jobId)}`
      : `/api/scan-results?limit=5000`;
    const listOut = await api.get(url);
    if (!listOut.ok) return;
    const rows = Array.isArray(listOut.data) ? listOut.data : [];
    const signals = rows.map((r) => signalFromScanResultRow(r)).filter((p) => p && typeof p === "object");
    // SaaS path persists kept-only signals via /api/scan-results; the
    // shortlist (when present) comes from the lifecycle response itself.
    const tableRows = applyScanResponseSignals({ signals, shortlist_signals: ls.shortlist_signals });
    const headline = diagnosticsHeadline(diag);
    if (metaEl)
      metaEl.textContent =
        (headline || buildScanMeta(signals, ls.signals_found ?? signals.length)) + formatStrategySummary(strat);
    updateTopStrategyChip(strat);
    renderDiagnostics(diag);
    renderScanRows(tableRows);
    return;
  }

  const localSignals = Array.isArray(ls.signals) ? ls.signals : [];
  const tableRows = applyScanResponseSignals({
    signals: localSignals,
    shortlist_signals: ls.shortlist_signals,
  });
  const headline = diagnosticsHeadline(diag);
  if (metaEl)
    metaEl.textContent =
      (headline || buildScanMeta(localSignals, ls.signals_found)) + formatStrategySummary(strat);
  updateTopStrategyChip(strat);
  renderDiagnostics(diag);
  renderScanRows(tableRows);
}

// Render the Schwab refresh-token health chip + detail row.
// Status field comes from /api/status -> schwab_token_health (a roll-up of
// market + account sessions, severity-ordered). The chip warns *before* the
// 7-day refresh-token TTL silently runs out, so the operator can re-OAuth on
// schedule rather than being surprised by 401s mid-session.
function renderSchwabTokenHealth(health) {
  const badgeEl = document.getElementById("tokenAgeBadge");
  const detailEl = document.getElementById("tokenAgeDetail");
  if (!badgeEl || !detailEl) return;

  const safe = health && typeof health === "object" ? health : {};
  const market = (safe.market && typeof safe.market === "object") ? safe.market : {};
  const account = (safe.account && typeof safe.account === "object") ? safe.account : {};
  const status = safeText(safe.status || "unknown").toLowerCase();

  // Pick the worst session for the visible badge (matches roll-up semantics).
  const severity = { healthy: 0, unknown: 1, warn: 2, critical: 3, expired: 4 };
  const worstSession =
    (severity[market.status] || 0) >= (severity[account.status] || 0) ? market : account;
  const hours = Number.isFinite(worstSession.hours_until_expiry)
    ? worstSession.hours_until_expiry
    : null;

  clearUnavailable(badgeEl);
  badgeEl.removeAttribute("data-unavailable");

  let label = "—";
  let pillClass = "pill neutral";
  let detail = "no token age recorded yet";

  if (status === "healthy") {
    pillClass = "pill ok";
    if (hours !== null) {
      const days = hours / 24;
      label = days >= 1 ? `Fresh · ${days.toFixed(1)}d left` : `Fresh · ${Math.max(1, Math.round(hours))}h left`;
    } else {
      label = "Fresh";
    }
    detail = "Both Schwab refresh tokens are healthy.";
  } else if (status === "warn") {
    pillClass = "pill warn";
    label = hours !== null ? `Refresh soon · ${(hours / 24).toFixed(1)}d` : "Refresh soon";
    detail = "Re-OAuth within 1–2 days. Run python run_dual_auth.py from schwab_skill/.";
  } else if (status === "critical") {
    pillClass = "pill warn";
    label = hours !== null ? `Refresh now · ${Math.max(0, Math.round(hours))}h` : "Refresh now";
    detail = "< 12 h until Schwab refresh tokens expire. Re-OAuth immediately.";
  } else if (status === "expired") {
    pillClass = "pill error";
    label = "Expired";
    detail = "Schwab refresh tokens are dead. Re-OAuth (python run_dual_auth.py) before scanning.";
  } else {
    // "unknown" — token file present but no _last_refresh_at marker (legacy
    // file from before the timestamp rollout). Trigger one save cycle and
    // the badge will populate; in the meantime just say "unknown".
    pillClass = "pill neutral";
    label = "Unknown";
    detail = "Token age unknown — will populate on next refresh cycle (~25 min).";
  }
  badgeEl.className = pillClass;
  badgeEl.textContent = label;
  detailEl.textContent = detail;
  detailEl.dataset.freshness = status;

  // Mirror critical/expired into the action center so it's visible without
  // expanding the "Detailed system status" disclosure.
  if (status === "expired" || status === "critical") {
    updateActionCenter({
      title: status === "expired" ? "Schwab tokens expired" : "Schwab tokens expiring soon",
      message: detail,
      severity: status === "expired" ? "error" : "warn",
    });
  }
}

async function refreshStatus() {
  const saasMode = !!state.publicConfig?.saas_mode;
  // /api/status is now network-free: it returns token *presence* only and does
  // NOT run a live Schwab quote probe (that probe refreshed per-tenant tokens
  // and raced the scan worker on Schwab's single-use refresh token). Live quote
  // health comes from /api/health/deep (server-side cached). Older servers may
  // still embed quote_ok/quote_health in status.api_health — if so, reuse it to
  // avoid a redundant probe; otherwise fetch /api/health/deep.
  const statusRes = await api.get("/api/status");
  let deepRes;
  if (saasMode) {
    if (statusRes.ok) {
      const ah = statusRes.data?.api_health || {};
      const hasEmbeddedApiHealth =
        Object.prototype.hasOwnProperty.call(ah, "quote_ok") ||
        Object.prototype.hasOwnProperty.call(ah, "quote_health");
      if (hasEmbeddedApiHealth) {
        deepRes = {
          ok: true,
          data: {
            db_ok: true,
            market_token_ok: !!ah.market_token_ok,
            account_token_ok: !!ah.account_token_ok,
            quote_ok: !!ah.quote_ok,
            quote_health: ah.quote_health || {
              symbol: "AAPL",
              ok: !!ah.quote_ok,
              reason: ah.quote_ok ? null : ah.error || "not_linked_or_probe_failed",
              operator_hint: null,
            },
            metrics: ah.metrics || { requests_total: 0, errors_total: 0, client_errors_total: 0 },
          },
        };
      } else {
        deepRes = await api.get("/api/health/deep", { timeoutMs: 30000 });
      }
    } else {
      deepRes = { ok: false, error: statusRes.error };
    }
  } else {
    deepRes = await api.get("/api/health/deep", { timeoutMs: 30000 });
  }
  if (!statusRes.ok) {
    logEvent({ kind: "system", severity: "error", message: `Status failed: ${statusRes.error}` });
    const statusUiError = (() => {
      const raw = safeText(statusRes.error || "").trim();
      const lower = raw.toLowerCase();
      if (
        lower.includes("missing authentication") ||
        lower.includes("authorization: bearer") ||
        lower.includes("auth session cookie")
      ) {
        return "Verify your email session first, then connect Schwab to unlock live status.";
      }
      if (lower.includes("expired") && lower.includes("token")) {
        return "Session expired. Verify your email again to continue.";
      }
      return raw || "Status check unavailable right now.";
    })();
    // Mark each detail pill unavailable. Honest "—" beats a confident "Unknown"
    // because "Unknown" can read as "passed Unknown check" to a tired user.
    [
      "marketToken",
      "accountToken",
      "quoteHealth",
      "validationHealth",
      "lastScan",
      "apiErrorRate",
    ].forEach((id) => markUnavailable(document.getElementById(id), statusUiError));
    // Reset ribbon to honest unknown.
    setHealthRibbonUnavailable(statusUiError);
    setSystemStatusStrip(
      "statusDetailsStatusStrip",
      "error",
      "Detailed status unavailable.",
      statusUiError,
    );
    paintSystemPanelSnapshot("statusDetailsSnapshot", "statusDetailsPanel", "error", {
      hint: "Tokens · last scan · quotes · validation",
      kpis: [
        { label: "AUTH", sub: "session", value: "—", tone: "bad" },
        { label: "QUOTES", sub: "market", value: "—", tone: "bad" },
        { label: "API", sub: "errors", value: "—", tone: "bad" },
      ],
      lines: ["Detailed status unavailable.", statusUiError],
    });
    updateActionCenter({ title: "Status unavailable", message: statusUiError, severity: "error" });
    return;
  }

  const status = statusRes.data || {};
  try {
    await hydrateScanTableFromStatus(status);
  } catch (e) {
    console.warn("hydrateScanTableFromStatus", e);
  }
  renderStaleScanBanner(status?.last_scan?.at);
  const marketTokenEl = document.getElementById("marketToken");
  const accountTokenEl = document.getElementById("accountToken");
  clearUnavailable(marketTokenEl);
  clearUnavailable(accountTokenEl);
  setStatusPill(marketTokenEl, status.market_state || (status.market_token_ok ? "Connected" : "Disconnected"));
  setStatusPill(accountTokenEl, status.account_state || (status.account_token_ok ? "Connected" : "Disconnected"));
  renderSchwabTokenHealth(status.schwab_token_health);

  const lastScanEl = document.getElementById("lastScan");
  if (lastScanEl) {
    if (status.last_scan && status.last_scan.at) {
      clearUnavailable(lastScanEl);
      lastScanEl.className = "pill neutral";
      const ts = new Date(status.last_scan.at);
      const when = Number.isNaN(ts.getTime()) ? "recently" : ts.toLocaleTimeString();
      lastScanEl.textContent = `${formatCount(status.last_scan.signals_found, "0")} @ ${when}`;
      // Carry the timestamp into shared state so the hero KPI can render
      // freshness without re-parsing pill text.
      if (!state.lastScanAt && !Number.isNaN(ts.getTime())) {
        state.lastScanAt = ts.toISOString();
      }
    } else {
      markUnavailable(lastScanEl, "no scan recorded yet");
    }
  }

  const quoteEl = document.getElementById("quoteHealth");
  const errEl = document.getElementById("apiErrorRate");
  const validationEl = document.getElementById("validationHealth");
  const validationAgeEl = document.getElementById("validationAge");
  const validationProgressEl = document.getElementById("validationProgress");
  const validation = status.validation_status || {};
  const runStatus = safeText(validation.run_status || "idle").toLowerCase();
  if (runStatus === "running") {
    setStatusPill(validationEl, "Running");
  } else if (validation.exists && validation.passed === true) {
    setStatusPill(validationEl, "Pass");
  } else if (validation.exists && validation.passed === false) {
    setStatusPill(validationEl, "Fail");
  } else if (validation.exists) {
    setStatusPill(validationEl, "Degraded");
  } else {
    setStatusPill(validationEl, "Unknown");
  }
  if (validationAgeEl) {
    const failedSteps = (validation.failed_steps || []).slice(0, 2).join(", ");
    const failHint = failedSteps ? ` | failed: ${failedSteps}` : "";
    if (validation.exists) {
      validationAgeEl.textContent = `Updated ${timeAgo(validation.generated_at)}${failHint}`;
    } else if (state.publicConfig?.saas_mode) {
      validationAgeEl.textContent =
        "No validation artifact on this server (ephemeral deploy). Run validate_all locally or in CI.";
    } else {
      validationAgeEl.textContent = "No validation artifact yet — run: python scripts/validate_all.py --profile local";
    }
  }
  if (validationProgressEl) {
    if (runStatus === "running") {
      const completed = safeNum(validation.completed_steps, 0);
      const total = safeNum(validation.total_steps, 0);
      const pctDone = safeNum(validation.progress_pct, 0);
      const stepName = safeText(validation.current_step || "starting");
      validationProgressEl.textContent = `Progress: ${completed}/${total} (${pctDone}%) | step: ${stepName}`;
    } else if (validation.exists) {
      const completed = safeNum(validation.completed_steps, 0);
      const total = safeNum(validation.total_steps, 0);
      if (total > 0) {
        validationProgressEl.textContent = `Progress: ${completed}/${total} (100%)`;
      } else {
        validationProgressEl.textContent = "Progress: complete";
      }
    } else {
      validationProgressEl.textContent = "Progress: --";
    }
  }
  renderValidationRecentSteps(validation);
  if (deepRes.ok) {
    const qh = deepRes.data.quote_health;
    // Surface the operator hint (e.g. Schwab 401 market-data entitlement) on the
    // pill tooltip so "Degraded" is actionable at a glance, not just in the log.
    const quoteTooltip =
      !deepRes.data.quote_ok && qh && qh.operator_hint ? qh.operator_hint : "";
    setStatusPill(quoteEl, deepRes.data.quote_ok ? "Connected" : "Degraded", quoteTooltip);
    if (!deepRes.data.quote_ok && qh && qh.operator_hint) {
      const sig = `${qh.reason || ""}|${qh.operator_hint}`;
      if (sig !== state.lastQuoteHealthLogSig) {
        state.lastQuoteHealthLogSig = sig;
        logEvent({
          kind: "system",
          severity: "warn",
          message: `Quotes: ${qh.reason || "issue"} — ${qh.operator_hint}`,
        });
      }
    } else if (deepRes.data.quote_ok) {
      state.lastQuoteHealthLogSig = null;
    }
    const metrics = deepRes.data.metrics || {};
    const req = safeNum(metrics.requests_total, 0);
    const srvErr = safeNum(metrics.errors_total, 0);
    const clientErr = safeNum(metrics.client_errors_total, 0);
    const rate = req > 0 ? `${((srvErr / req) * 100).toFixed(1)}%` : "0.0%";
    errEl.className = statusClass(srvErr > 0 ? "warn" : clientErr > 0 ? "info" : "info");
    errEl.textContent =
      clientErr > 0 ? `${rate} srv (${srvErr}/${req}, 4xx:${clientErr})` : `${rate} srv (${srvErr}/${req})`;
  } else {
    setStatusPill(quoteEl, "Unknown");
    errEl.className = "pill neutral";
    errEl.textContent = "--";
  }

  // Tri-state broker auth: green is reserved for a *live*-confirmed connection.
  // Token presence (from /api/status) alone is "Verifying" (amber), never green,
  // so a saved-but-revoked token no longer reads as Connected.
  const authPresent = Boolean(status.market_token_ok && status.account_token_ok);
  let authState; // "connected" | "unverified" | "disconnected"
  if (deepRes.ok && deepRes.data && typeof deepRes.data.connection_state === "string") {
    authState = deepRes.data.connection_state;
  } else if (deepRes.ok && deepRes.data) {
    const liveOk = Boolean(
      deepRes.data.market_token_ok && deepRes.data.account_token_ok && deepRes.data.quote_ok,
    );
    authState = liveOk ? "connected" : authPresent ? "unverified" : "disconnected";
  } else {
    // Deep probe unreachable: we can confirm presence but not a live response.
    authState = authPresent ? "unverified" : "disconnected";
  }
  const authOk = authState === "connected";
  const quoteOk = Boolean(deepRes.ok && deepRes.data?.quote_ok);
  const req = safeNum(deepRes?.data?.metrics?.requests_total, 0);
  const srvErrRibbon = safeNum(deepRes?.data?.metrics?.errors_total, 0);
  const errRate = req > 0 ? (srvErrRibbon / req) * 100 : 0;

  const ribbonAuth = document.getElementById("ribbonAuth");
  const ribbonQuotes = document.getElementById("ribbonQuotes");
  const ribbonApi = document.getElementById("ribbonApiErrorRate");
  const ribbonValidation = document.getElementById("ribbonValidation");
  const nowIso = new Date().toISOString();
  state.lastStatusAt = nowIso;
  if (ribbonAuth) {
    clearUnavailable(ribbonAuth);
    ribbonAuth.className = healthBadgeStateClass(authState);
    if (authState === "connected") {
      ribbonAuth.textContent = "Connected";
      ribbonAuth.title = "Schwab market data and account APIs responded successfully just now.";
    } else if (authState === "unverified") {
      ribbonAuth.textContent = "Verifying";
      ribbonAuth.title =
        "Schwab tokens are saved but a live API response hasn't been confirmed yet. If this persists, reconnect Schwab.";
    } else {
      ribbonAuth.textContent = "Disconnected";
      ribbonAuth.title = "No usable Schwab session. Connect or re-authenticate Schwab to continue.";
    }
  }
  const ribbonAuthReason = document.getElementById("ribbonAuthReason");
  if (ribbonAuthReason) {
    ribbonAuthReason.textContent =
      authState === "connected"
        ? "Schwab market data and account APIs are responding."
        : authState === "unverified"
          ? "Tokens saved, but the live API hasn't confirmed yet. Reconnect if this persists."
          : "No usable Schwab session — connect or re-authenticate.";
  }
  applyFreshness(document.getElementById("ribbonAuthFresh"), {
    asOf: nowIso,
    source: "/api/status",
    surface: "health_ribbon",
  });
  if (ribbonQuotes) {
    clearUnavailable(ribbonQuotes);
    if (deepRes.ok) {
      ribbonQuotes.className = healthBadgeClass(quoteOk);
      ribbonQuotes.textContent = quoteOk ? "Healthy" : "Degraded";
    } else {
      markUnavailable(ribbonQuotes, deepRes.error || "Quote health check failed");
      ribbonQuotes.className = "health-badge bg-slate-900";
      ribbonQuotes.textContent = "Unknown";
    }
  }
  const ribbonQuotesReason = document.getElementById("ribbonQuotesReason");
  if (ribbonQuotesReason) {
    const qh = deepRes?.data?.quote_health || {};
    if (!deepRes.ok) {
      ribbonQuotesReason.textContent = "Live market-data probe is unreachable.";
    } else if (quoteOk) {
      ribbonQuotesReason.textContent = "Live AAPL quote returned successfully.";
    } else {
      const reason = safeText(qh.operator_hint || qh.reason || "").trim();
      ribbonQuotesReason.textContent = reason
        ? `Quote check failed: ${reason}`
        : "Quote check failed. See logs for details.";
    }
  }
  applyFreshness(document.getElementById("ribbonQuotesFresh"), {
    asOf: deepRes.ok ? nowIso : null,
    source: "/api/health/deep",
    surface: "health_ribbon",
    unavailable: "deep health unreachable",
  });
  if (ribbonApi) {
    if (deepRes.ok) {
      clearUnavailable(ribbonApi);
      const apiHealthy = errRate < 2.0;
      ribbonApi.className = healthBadgeClass(apiHealthy);
      ribbonApi.textContent = `${errRate.toFixed(1)}%`;
    } else {
      markUnavailable(ribbonApi, deepRes.error || "no metrics available");
      ribbonApi.className = "health-badge bg-slate-900";
      ribbonApi.textContent = "—";
    }
  }
  applyFreshness(document.getElementById("ribbonApiErrorRateFresh"), {
    asOf: deepRes.ok ? nowIso : null,
    source: "/api/health/deep metrics",
    surface: "health_ribbon",
    unavailable: "metrics unreachable",
  });
  if (ribbonValidation) {
    if (validation.exists) {
      clearUnavailable(ribbonValidation);
      const validOk = validation.passed === true;
      ribbonValidation.className = healthBadgeClass(validOk);
      ribbonValidation.textContent = validOk ? "Pass" : safeText(validation.run_status || "Fail");
    } else {
      const noArtifactHint =
        state.publicConfig?.saas_mode
          ? "Run validate_all on a build host"
          : "run python scripts/validate_all.py";
      markUnavailable(ribbonValidation, `no artifact — ${noArtifactHint}`);
      ribbonValidation.className = "health-badge bg-slate-900";
      ribbonValidation.textContent = "No run";
    }
  }
  applyFreshness(document.getElementById("ribbonValidationFresh"), {
    asOf: validation.generated_at || null,
    source: "validation_status.generated_at",
    surface: "health_ribbon",
    budgetSec: 24 * 3600,
    unavailable: "no validation artifact",
  });
  setHealthRibbonTiles(authState, quoteOk, errRate, validation);
  renderHealthRibbonSummary({ authState, quoteOk, deepReachable: deepRes.ok, lastScan: status?.last_scan });
  const statusState =
    authState === "connected" && quoteOk && errRate < 2.0
      ? "success"
      : authState === "disconnected" || !deepRes.ok
        ? "error"
        : "partial";
  setSystemStatusStrip(
    "statusDetailsStatusStrip",
    statusState,
    "Detailed system status loaded.",
    `Auth ${authState} · quotes ${quoteOk ? "healthy" : "degraded"} · API errors ${errRate.toFixed(1)}%.`,
  );
  paintSystemPanelSnapshot("statusDetailsSnapshot", "statusDetailsPanel", statusState, {
    hint: "Tokens · last scan · quotes · validation",
    kpis: [
      {
        label: "AUTH",
        sub: "broker session",
        value: authState === "connected" ? "ok" : authState === "unverified" ? "check" : "off",
        tone: authState === "connected" ? "success" : authState === "unverified" ? "warn" : "bad",
      },
      {
        label: "QUOTES",
        sub: "market data",
        value: quoteOk ? "live" : "degraded",
        tone: quoteOk ? "success" : "warn",
      },
      {
        label: "API",
        sub: "error rate",
        value: `${errRate.toFixed(1)}%`,
        tone: errRate < 2 ? "success" : errRate < 8 ? "warn" : "bad",
      },
    ],
    lines: [
      "Detailed system status loaded.",
      `Auth ${authState} · quotes ${quoteOk ? "healthy" : "degraded"} · API errors ${errRate.toFixed(1)}%.`,
    ],
  });
  updateSystemSummaryLanding();
  refreshSystemAlertBanner({ authState, quoteOk, errRate });
  // Mark the ribbon container as success now that it has rendered real data.
  const ribbonContainer = document.getElementById("healthRibbon");
  if (ribbonContainer) ribbonContainer.setAttribute("data-async-state", "success");
  const topBlocker =
    status?.last_scan?.diagnostics_summary?.top_blockers?.[0]?.key ||
    status?.last_scan?.diagnostics_summary?.headline ||
    "";
  prioritizeActionCenterFromHealth({
    authState,
    quoteOk,
    errRate,
    validation,
    topBlocker,
    quoteHealth: deepRes?.data?.quote_health || null,
  });
  if (authOk) {
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.AUTH_LINKED, {
      source: "status_health_check",
    });
  }
  updateHeroInfographic();
  void maybeResumeLocalScanPolling();
}

async function maybeResumeLocalScanPolling() {
  if (state.publicConfig?.saas_mode) return;
  if (localScanPollActive) return;
  const lifecycle = await api.get("/api/scan-lifecycle", { timeoutMs: 20000 });
  if (!lifecycle.ok) return;
  const data = lifecycle.data || {};
  if (safeText(data.status).toLowerCase() !== "running") return;
  const jobId = safeText(data.job_id || "").trim();
  if (jobId && jobId === resumedLocalScanJobId) return;
  if (jobId) resumedLocalScanJobId = jobId;
  const scanBtn = document.getElementById("scanBtn");
  try {
    if (scanBtn) scanBtn.disabled = true;
    await waitForScanCompletion();
  } finally {
    if (scanBtn) scanBtn.disabled = false;
  }
}

function applyEntryTimingExperimentPreflight(preflight) {
  if (!preflight || typeof preflight !== "object") return;
  state.entryTimingScanPreflight = preflight;
  const scanBtn = document.getElementById("scanBtn");
  const needsRestart = preflight.needs_dashboard_restart === true;
  const needsRescan = preflight.stale_last_scan === true;
  const needsConfig = preflight.experiment_recommended && !preflight.experiment_env_ready && !preflight.experiment_env_file_ready;
  if (scanBtn) {
    if (needsRestart) {
      scanBtn.title = "Restart the dashboard to load experiment .env vars, then Run Scan.";
    } else if (needsRescan) {
      scanBtn.title = "Experiment env is loaded — Run Scan to refresh entry-timing shadow counters.";
    } else if (needsConfig) {
      scanBtn.title = "Run scripts/apply_entry_timing_experiment_env.py, restart server, then Run Scan.";
    } else {
      scanBtn.title = "";
    }
  }
  const warn = (preflight.warnings || [])[0];
  if (!warn) return;
  updateActionCenter({
    title: needsRestart
      ? "Restart dashboard for experiment env"
      : needsRescan
        ? "Run Scan for experiment evidence"
        : "Configure entry-timing experiment",
    message: warn,
    severity: "warn",
  });
}

async function refreshDecisionDashboard() {
  const freshEl = document.getElementById("decisionDashboardFresh");
  renderDecisionDashboardLoading();
  const out = await api.get("/api/decision-dashboard");
  if (!out.ok) {
    const msg = safeText(out.user_message || out.error || "Decision dashboard unavailable.");
    renderDecisionDashboardUnavailable(msg, () => refreshDecisionDashboard());
    [
      "decisionReliabilityState",
      "decisionPromotionState",
    ].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.className = "health-badge bg-slate-900";
      el.textContent = "Unknown";
      markUnavailable(el, msg);
    });
    [
      "decisionValidationStatus",
      "decisionSloStatus",
      "decisionLastScan",
      "decisionSignalsFound",
      "decisionStrategyLead",
      "decisionDataQuality",
      "decisionSignalEdgeState",
      "decisionEarlyStopConstraint",
      "decisionRankFilterShadow",
      "decisionEntryTimingExperiment",
      "decisionLatestPromotion",
      "decisionAblationStatus",
      "decisionAblationLift",
      "decisionAblationSummary",
    ].forEach((id) => markUnavailable(document.getElementById(id), msg));
    const ablationList = document.getElementById("decisionAblationTopList");
    if (ablationList) {
      ablationList.innerHTML = '<li class="muted">Strategy comparison unavailable.</li>';
    }
    applyFreshness(freshEl, {
      asOf: null,
      source: "/api/decision-dashboard",
      surface: "decision_dashboard",
      unavailable: msg,
    });
    return;
  }
  state.lastDecisionDashboardAt = new Date().toISOString();
  [
    "decisionReliabilityState",
    "decisionPromotionState",
    "decisionValidationStatus",
    "decisionSloStatus",
    "decisionLastScan",
    "decisionSignalsFound",
    "decisionStrategyLead",
    "decisionDataQuality",
    "decisionSignalEdgeState",
    "decisionEarlyStopConstraint",
    "decisionRankFilterShadow",
    "decisionEntryTimingExperiment",
    "decisionLatestPromotion",
    "decisionAblationStatus",
    "decisionAblationLift",
    "decisionAblationSummary",
  ].forEach((id) => clearUnavailable(document.getElementById(id)));
  renderDecisionDashboard(out.data || {});
  applyEntryTimingExperimentPreflight(out.data?.scan_preflight || null);
  applyFreshness(freshEl, {
    asOf: state.lastDecisionDashboardAt,
    source: "/api/decision-dashboard",
    surface: "decision_dashboard",
  });
}

function _setAblationStatusUi(statusText, metaText) {
  const statusEl = document.getElementById("ablationCycleStatus");
  const metaEl = document.getElementById("ablationCycleMeta");
  if (statusEl) statusEl.textContent = statusText;
  if (metaEl) metaEl.textContent = metaText;
}

function _syncAblationButtons(running) {
  const runBtn = document.getElementById("ablationCycleBtn");
  if (!runBtn) return;
  runBtn.disabled = Boolean(running);
  runBtn.textContent = running ? "Strategy test running…" : "Run strategy comparison";
}

async function refreshAblationCycleStatus({ quiet = false } = {}) {
  const out = await api.get("/api/ablation/status");
  if (!out.ok) {
    _setAblationStatusUi("Status: unknown", safeText(out.error || "Strategy test status unavailable."));
    if (!quiet) {
      updateActionCenter({
        title: "Strategy test unavailable",
        message: safeText(out.error || "Could not load strategy test status."),
        severity: "warn",
      });
    }
    _syncAblationButtons(false);
    return;
  }
  const data = out.data || {};
  const runStatus = safeText(data.run_status || "idle").toLowerCase();
  const running = Boolean(data.running) || runStatus === "running";
  const report = data.latest_report || {};
  const summary = report.summary || {};
  const passCount = Number(summary.pass_count ?? 0);
  const failCount = Number(summary.fail_count ?? 0);
  const best = report.best || {};
  const bestId = safeText(best.variant_id || "—");
  const bestLiftRaw = Number(best.relative_lift_vs_baseline);
  const bestLift = Number.isFinite(bestLiftRaw) ? `${bestLiftRaw >= 0 ? "+" : ""}${(bestLiftRaw * 100).toFixed(1)}%` : "—";
  const startedAt = safeText(data.started_at || "");
  const finishedAt = safeText(data.finished_at || "");
  const stamp = running ? startedAt : finishedAt;
  const when = stamp ? ` (${timeAgo(stamp)})` : "";
  _setAblationStatusUi(
    `Status: ${runStatus}${when}`,
    report.exists
      ? `Best ${bestId} ${bestLift} | pass ${passCount}, fail ${failCount}`
      : "No ablation report artifact yet."
  );
  _syncAblationButtons(running);
  if (running) {
    if (!_ablationCyclePollTimer) {
      _ablationCyclePollTimer = window.setInterval(() => {
        void refreshAblationCycleStatus({ quiet: true });
      }, 5000);
    }
  } else if (_ablationCyclePollTimer) {
    window.clearInterval(_ablationCyclePollTimer);
    _ablationCyclePollTimer = null;
  }
  if (_lastAblationRunStatus === "running" && runStatus !== "running") {
    void refreshDecisionDashboard();
    if (!quiet) {
      const msg = runStatus === "completed" ? "Strategy comparison completed." : "Strategy comparison finished with issues.";
      updateActionCenter({ title: "Strategy comparison", message: msg, severity: runStatus === "completed" ? "success" : "warn" });
    }
  }
  _lastAblationRunStatus = runStatus;
}

async function runAblationCycle() {
  const btn = document.getElementById("ablationCycleBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Starting...";
  }
  try {
    const out = await api.post("/api/ablation/run", {});
    if (!out.ok) {
      const msg = safeText(out.error || "Could not start ablation cycle.");
      _setAblationStatusUi("Status: failed_to_start", msg);
      updateActionCenter({ title: "Strategy comparison failed to start", message: msg, severity: "error" });
      return;
    }
    const d = out.data || {};
    if (d.already_running) {
      updateActionCenter({
        title: "Strategy test already running",
        message: "A previous run is still in progress.",
        severity: "info",
      });
    } else {
      updateActionCenter({
        title: "Strategy comparison started",
        message: "Running parameter sweep and report scoring in the background.",
        severity: "success",
      });
    }
    await refreshAblationCycleStatus({ quiet: true });
  } catch (e) {
    const msg = safeText(String(e));
    _setAblationStatusUi("Status: error", msg);
    updateActionCenter({ title: "Strategy comparison error", message: msg, severity: "error" });
  } finally {
    _syncAblationButtons(_lastAblationRunStatus === "running");
  }
}

const SCAN_START_META = "Scanning S&P 1500 candidates…";
let localScanPollActive = false;
let resumedLocalScanJobId = null;

function scanBodyFromBacktestSpec(spec) {
  if (!spec || typeof spec !== "object") return {};
  const out = {};
  if (spec.overrides && typeof spec.overrides === "object" && Object.keys(spec.overrides).length) {
    out.strategy_overrides = spec.overrides;
  }
  const um = safeText(spec.universe_mode || "").toLowerCase();
  // Scan defaults to server-side SP1500; only carry explicit ticker overrides.
  if (um === "tickers") out.universe_mode = um;
  if (um === "tickers" && Array.isArray(spec.tickers)) out.tickers = spec.tickers;
  return out;
}

function readScanOptionsFromForm() {
  const ta = document.getElementById("scanOptionsJson");
  if (!ta) {
    state.scanRunOptions = null;
    return true;
  }
  const raw = ta.value.trim();
  if (!raw) {
    state.scanRunOptions = null;
    return true;
  }
  try {
    const parsed = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Scan options must be a JSON object.");
    }
    state.scanRunOptions = parsed;
    return true;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    logEvent({ kind: "scan", severity: "error", message: `Invalid scan options JSON: ${msg}` });
    updateActionCenter({ title: "Scan options", message: msg, severity: "error" });
    return false;
  }
}

async function fillScanOptionsFromLatestBacktest() {
  const ta = document.getElementById("scanOptionsJson");
  if (!ta) return;
  // `/api/backtest-runs` is SaaS-only; locally this would 404.
  if (!state.publicConfig?.saas_mode) {
    updateActionCenter({
      title: "Backtests",
      message: "Hosted backtest history is SaaS-only. Paste scan options manually, or run python backtest.py locally.",
      severity: "info",
    });
    return;
  }
  const out = await api.get("/api/backtest-runs?limit=1");
  if (!out.ok) {
    logEvent({ kind: "scan", severity: "error", message: `Backtest list failed: ${out.error}` });
    updateActionCenter({ title: "Backtests", message: safeText(out.error), severity: "error" });
    return;
  }
  const rows = Array.isArray(out.data) ? out.data : [];
  if (!rows.length) {
    updateActionCenter({ title: "Backtests", message: "No backtest runs yet.", severity: "info" });
    return;
  }
  const spec = rows[0].spec;
  const body = scanBodyFromBacktestSpec(spec);
  ta.value = JSON.stringify(body, null, 2);
  readScanOptionsFromForm();
  logEvent({ kind: "scan", severity: "info", message: "Scan options filled from latest backtest." });
  updateActionCenter({
    title: "Scan options",
    message: "Filled from your most recent backtest. Edit JSON if needed, then Run Scan.",
    severity: "info",
  });
}

function strategySummaryFromSignals(signals) {
  const rows = Array.isArray(signals) ? signals : [];
  const counts = {};
  rows.forEach((sig) => {
    const attr = sig?.strategy_attribution;
    const name = String((attr && attr.top_live) || "unknown");
    counts[name] = (counts[name] || 0) + 1;
  });
  const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const dominant = ranked[0]?.[0] || "—";
  const dominantCount = ranked[0]?.[1] || 0;
  return {
    dominant_live_strategy: dominant,
    dominant_count: dominantCount,
    total_ranked: rows.length,
    counts: Object.fromEntries(ranked),
  };
}

async function waitForSaaScanCompletion(taskId) {
  const isGatewayLikeFailure = (out) => {
    const err = safeText(out?.error || "").toLowerCase();
    const statusCode = Number(out?.status || 0);
    return statusCode === 502 || err.includes("invalid json response (502)") || err.includes("bad gateway");
  };
  const pollScanStatus = async () => {
    const primary = await api.get(`/api/scan-lifecycle?task_id=${encodeURIComponent(taskId)}`);
    if (primary.ok) return primary;
    const fallback = await api.get(`/api/scan/${encodeURIComponent(taskId)}`);
    if (fallback.ok) {
      const d = fallback.data || {};
      return {
        ok: true,
        data: {
          status: d.status,
          result: d.result,
          worker_queue: d.worker_queue,
        },
      };
    }
    const errParts = [primary.error, fallback.error].filter(Boolean);
    return {
      ok: false,
      status: primary.status || fallback.status,
      error: errParts.join(" | ") || "Scan status unavailable.",
    };
  };
  const maxPolls = 400;
  const metaEl = document.getElementById("scanMeta");
  let firstPendingAt = null;
  let workerHintShown = false;
  let transientGatewayFailures = 0;
  let unknownStatusStreak = 0;
  setJobProgress("scanJobProgress", "scanJobProgressLabel", 0.05, "Queued…");
  for (let i = 0; i < maxPolls; i++) {
    const status = await pollScanStatus();
    if (!status.ok) {
      if (isGatewayLikeFailure(status) && transientGatewayFailures < 12) {
        transientGatewayFailures += 1;
        metaEl.textContent = "Scan status endpoint temporarily unavailable… retrying.";
        updateActionCenter({
          title: "Scan Polling Retrying",
          message: "Temporary gateway error while checking task status. Retrying automatically.",
          severity: "warn",
        });
        await new Promise((r) => setTimeout(r, Math.min(2000 * transientGatewayFailures, 12000)));
        continue;
      }
      metaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: `Scan task status failed: ${status.error}` });
      updateActionCenter({ title: "Scan Failed", message: status.error, severity: "error" });
      return;
    }
    transientGatewayFailures = 0;
    const data = status.data || {};
    const celeryStatus = safeText(data.status || "").toLowerCase();
    if (celeryStatus === "pending" || celeryStatus === "received") {
      if (firstPendingAt === null) firstPendingAt = Date.now();
      // Distinguish "worker is reachable but busy with another scan" from
      // "no worker is consuming the queue". A reachable worker with an active
      // or reserved task means we're simply queued behind a running scan, so
      // the "start a worker" hint would be misleading.
      const wq = data.worker_queue || {};
      const busyHint = wq.busy_hint && typeof wq.busy_hint === "object" ? wq.busy_hint : {};
      const workerReachable = wq.inspect_available === true && wq.inspect_error !== true;
      const workerBusy =
        wq.worker_busy === true ||
        busyHint.busy === true ||
        (workerReachable && (safeNum(wq.active_total, 0) + safeNum(wq.reserved_total, 0)) > 0);
      metaEl.textContent = workerBusy
        ? "Scan queued… worker is busy finishing another scan."
        : "Scan queued… waiting for worker.";
      setJobProgress(
        "scanJobProgress",
        "scanJobProgressLabel",
        0.12,
        workerBusy ? "Queued — worker busy" : "Queued — waiting for worker",
      );
      const queuedMs = Date.now() - firstPendingAt;
      if (workerBusy) {
        // Healthy queueing behind an in-flight scan — no operator action needed.
        updateActionCenter({
          title: "Scan Queued",
          message:
            "A worker is busy finishing another scan. Yours is next in line and this page will update automatically when it runs.",
          severity: "info",
        });
      } else if (queuedMs > 50_000 && !workerHintShown) {
        workerHintShown = true;
        metaEl.textContent =
          "Still queued — no worker is consuming the \"scan\" queue. Confirm the Celery worker is running and shares the app's REDIS_URL.";
        updateActionCenter({
          title: "Scan waiting for worker",
          message:
            "No worker has picked this up. Ensure a Celery worker is running with: celery -A webapp.tasks worker -Q scan,orders,celery — using the same REDIS_URL as the app.",
          severity: "warn",
        });
      } else {
        updateActionCenter({
          title: "Scan Queued",
          message: "Task is waiting for a worker. This page will update when results are ready.",
          severity: "info",
        });
      }
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }
    firstPendingAt = null;
    if (celeryStatus === "started" || celeryStatus === "retry") {
      unknownStatusStreak = 0;
      metaEl.textContent = "Scan running…";
      setJobProgress("scanJobProgress", "scanJobProgressLabel", 0.55, "Running scan…");
      updateActionCenter({
        title: "Scan Running",
        message: "Scan task is executing. Results will appear below when finished.",
        severity: "info",
      });
      await new Promise((r) => setTimeout(r, 7000));
      continue;
    }
    if (celeryStatus === "success") {
      unknownStatusStreak = 0;
      const result = data.result;
      if (!result || typeof result !== "object") {
        metaEl.textContent = "Scan failed.";
        updateTopStrategyChip(null);
        const raw = typeof result === "string" ? result : "Invalid task result.";
        logEvent({ kind: "scan", severity: "error", message: raw });
        updateActionCenter({ title: "Scan Failed", message: raw, severity: "error" });
        return;
      }
      if (result.ok === false) {
        metaEl.textContent = "Scan failed.";
        updateTopStrategyChip(null);
        const errMsg = safeText(result.error || "Scan task returned error.");
        logEvent({ kind: "scan", severity: "error", message: errMsg });
        updateActionCenter({ title: "Scan Failed", message: errMsg, severity: "error" });
        return;
      }
      const jobId = result.job_id;
      let listOut;
      if (jobId) {
        listOut = await api.get(`/api/scan-results?limit=5000&job_id=${encodeURIComponent(jobId)}`);
      } else {
        listOut = { ok: false, error: "Missing job_id in scan result." };
      }
      if (!listOut.ok) {
        metaEl.textContent = "Scan finished but results could not be loaded.";
        updateTopStrategyChip(null);
        logEvent({ kind: "scan", severity: "error", message: `Scan results failed: ${listOut.error}` });
        updateActionCenter({ title: "Scan Results Failed", message: listOut.error, severity: "error" });
        return;
      }
      const rows = Array.isArray(listOut.data) ? listOut.data : [];
      const signals = rows.map((r) => signalFromScanResultRow(r)).filter((p) => p && typeof p === "object");
      const tableRows = applyScanResponseSignals({
        signals,
        shortlist_signals: result.shortlist_signals,
      });
      const diag = result.diagnostics || {};
      const headline = diagnosticsHeadline(diag);
      const n = safeNum(result.signals_found, signals.length);
      const strat =
        result.strategy_summary && typeof result.strategy_summary === "object"
          ? result.strategy_summary
          : strategySummaryFromSignals(signals);
      metaEl.textContent =
        (headline || buildScanMeta(signals, n)) + formatStrategySummary(strat);
      updateTopStrategyChip(strat);
      renderDiagnostics(diag);
      renderScanRows(tableRows);
      logEvent({
        kind: "scan",
        severity: "info",
        message: `Scan complete (SaaS): ${n} signal(s), task ${safeText(taskId).slice(0, 12)}…`,
      });
      void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_SCAN, {
        transport: "saas_celery",
        signals_found: n,
      });
      updateActionCenter({
        title: "Scan Complete",
        message: `Found ${n} signal(s). Review queue candidates in Scan Results.`,
        severity: "success",
      });
      setJobProgress("scanJobProgress", "scanJobProgressLabel", 1, "Complete");
      return;
    }
    if (celeryStatus === "failure" || celeryStatus === "revoked") {
      unknownStatusStreak = 0;
      metaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      const res = data.result;
      let errMsg = "Scan task failed.";
      if (typeof res === "string") errMsg = res;
      else if (res && typeof res === "object")
        errMsg = safeText(res.error || res.message || res.exc_message || JSON.stringify(res));
      logEvent({ kind: "scan", severity: "error", message: errMsg });
      updateActionCenter({ title: "Scan Failed", message: errMsg, severity: "error" });
      setJobProgress("scanJobProgress", "scanJobProgressLabel", 0, "");
      return;
    }
    if (celeryStatus === "unknown" || !celeryStatus) {
      unknownStatusStreak += 1;
      if (unknownStatusStreak >= 6) {
        const inspectError = safeText((data.worker_queue || {}).inspect_error || "");
        const statusError = safeText(data.status_error || "");
        const details = [statusError, inspectError].filter(Boolean).join(" | ");
        const msg =
          details ||
          "Task status remained unknown. Verify Redis/Celery backend connectivity and refresh once workers are healthy.";
        metaEl.textContent = "Scan status is unavailable.";
        updateTopStrategyChip(null);
        logEvent({ kind: "scan", severity: "error", message: `Scan polling stopped: ${msg}` });
        updateActionCenter({
          title: "Scan Status Unavailable",
          message: msg,
          severity: "error",
        });
        setJobProgress("scanJobProgress", "scanJobProgressLabel", 0, "");
        return;
      }
      metaEl.textContent = "Scan status unavailable… retrying.";
      updateActionCenter({
        title: "Scan Status Pending",
        message: "Status backend is not ready yet. Retrying automatically.",
        severity: "warn",
      });
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }
    unknownStatusStreak = 0;
    await new Promise((r) => setTimeout(r, 5000));
  }
  metaEl.textContent = "Scan still running. Use Refresh to check progress.";
  updateTopStrategyChip(null);
  logEvent({ kind: "scan", severity: "warn", message: "SaaS scan polling window ended." });
  updateActionCenter({
    title: "Scan Still Running",
    message: "Polling window ended. Use Refresh All to check task status.",
    severity: "warn",
  });
}

async function runScan() {
  const btn = document.getElementById("scanBtn");
  const scanMetaEl = document.getElementById("scanMeta");
  const mode = getScanMode();
  const profile = getScanModeProfile(mode);
  btn.disabled = true;
  btn.textContent = "Scanning...";
  setJobProgress("scanJobProgress", "scanJobProgressLabel", 0, "");
  setLoading({ scan: SCAN_START_META });
  setScanStatusLoading(
    "Scan running.",
    `${profile.label} mode · score >= ${profile.minScore} · volume >= ${profile.minVolumeRatio.toFixed(1)}.`,
  );
  trackUiEvent("scan_started", { mode });
  updateActionCenter({
    title: "Scan Running",
    message: `${profile.label} scan running (score >= ${profile.minScore}, vol ratio >= ${profile.minVolumeRatio.toFixed(1)}).`,
    severity: "info",
  });
  const pf = state.entryTimingScanPreflight;
  if (pf?.experiment_recommended && !pf?.experiment_env_ready) {
    logEvent({
      kind: "scan",
      severity: "warn",
      message: (pf.warnings || [])[0] || "Entry-timing experiment env not ready; shadow compare will skip.",
    });
  }
  try {
    if (!readScanOptionsFromForm()) return;
    const baseScanBody =
      state.scanRunOptions && typeof state.scanRunOptions === "object"
        ? state.scanRunOptions
        : {};
    const scanBody = mergeScanRunOptionsWithMode(baseScanBody);
    state.scanRowsExpanded = false;
    state.scanFunnelFilter = null;
    const out = await api.post("/api/scan?async_mode=true", scanBody);
    if (!out.ok) {
      scanMetaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: out.error });
      setScanStatusError("Scan failed.", out.user_message || out.error || "Check API logs and retry.");
      updateActionCenter({ title: "Scan Failed", message: out.error, severity: "error" });
      return;
    }
    const d = out.data || {};
    if (d.task_id) {
      const wq = d.worker_queue || {};
      const qBusy =
        wq.inspect_available && (wq.reserved_total != null || wq.active_total != null)
          ? safeNum(wq.reserved_total, 0) + safeNum(wq.active_total, 0)
          : null;
      const qPart = qBusy !== null ? ` · worker backlog ~${qBusy}` : "";
      const limPart =
        d.daily_scan_limit != null ? ` · daily scan quota ${safeNum(d.daily_scan_limit, 0)}/24h` : "";
      logEvent({
        kind: "scan",
        severity: "info",
        message: `Scan queued (task ${safeText(d.task_id).slice(0, 12)}…)${qPart}${limPart}.`,
      });
      await waitForSaaScanCompletion(d.task_id);
      await refreshStatus();
      return;
    }
    if (d.status === "running") {
      logEvent({
        kind: "scan",
        severity: "info",
        message: d.started ? "Scan started in background." : "Scan already running; monitoring progress.",
      });
      await waitForScanCompletion();
      await refreshStatus();
      return;
    }
    if (d.signals) {
      const tableRows = applyScanResponseSignals(d);
      const headline = diagnosticsHeadline(d.diagnostics_summary || d.diagnostics || {});
      scanMetaEl.textContent =
        (headline || buildScanMeta(state.latestSignals, d.signals_found)) + formatStrategySummary(d.strategy_summary);
      updateTopStrategyChip(d.strategy_summary);
      renderDiagnostics(d.diagnostics || d.diagnostics_summary || {});
      renderScanRows(tableRows);
      logEvent({ kind: "scan", severity: "info", message: `Scan complete: ${d.signals_found} signal(s).` });
      void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_SCAN, {
        transport: state.publicConfig?.saas_mode ? "saas_inline" : "local",
        signals_found: safeNum(d.signals_found, state.latestSignals.length),
      });
      updateActionCenter({
        title: "Scan Complete",
        message: `Found ${d.signals_found} signal(s). Review queue candidates in Scan Results.`,
        severity: "success",
      });
      return;
    }
    scanMetaEl.textContent = "Unexpected scan response; try Refresh or check API version.";
    updateTopStrategyChip(null);
    logEvent({ kind: "scan", severity: "warn", message: "Scan POST returned ok but unrecognized payload." });
    updateActionCenter({
      title: "Scan",
      message: "Unexpected response from server. Try Refresh All.",
      severity: "warn",
    });
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Scan";
    if (scanMetaEl && scanMetaEl.textContent === SCAN_START_META) {
      scanMetaEl.textContent = "No scan run yet.";
      setOperationsStatusStrip(
        "scanStatusStrip",
        "empty",
        "No scan yet.",
        "Run scan to populate candidates, blockers, and data quality.",
      );
      updateActionCenter({
        title: "Scan",
        message: "Scan did not start. Check connection and try again.",
        severity: "warn",
      });
    }
  }
}

async function waitForScanCompletion() {
  if (localScanPollActive) {
    return;
  }
  localScanPollActive = true;
  const maxPolls = 360;
  const metaEl = document.getElementById("scanMeta");
  let unknownStatusStreak = 0;
  let transientFailures = 0;
  try {
  for (let i = 0; i < maxPolls; i++) {
    // Bounded per-poll timeout: a heavy in-process scan can make the single
    // web instance slow to answer status checks. Abort a stuck poll and retry
    // rather than letting the default client timeout surface as a hard failure.
    const status = await api.get("/api/scan-lifecycle", { timeoutMs: 60000 });
    if (!status.ok) {
      // The scan keeps running on the server even when a status poll fails
      // (timeout, gateway blip, transient network). Treat these as transient
      // and keep polling so a busy server doesn't abort the whole scan UX.
      const errText = safeText(status.error || "").toLowerCase();
      const statusCode = Number(status.status || 0);
      const looksTransient =
        errText.includes("timed out") ||
        errText.includes("timeout") ||
        errText.includes("gateway") ||
        errText.includes("failed to fetch") ||
        errText.includes("networkerror") ||
        errText.includes("load failed") ||
        statusCode === 502 ||
        statusCode === 503 ||
        statusCode === 504;
      transientFailures += 1;
      if (looksTransient && transientFailures <= 60) {
        metaEl.textContent = "Scan running… (server busy, status check slow — retrying)";
        updateActionCenter({
          title: "Scan Running",
          message:
            "The server is busy running your scan, so status checks are slow. Still working — this page will update when results are ready.",
          severity: "info",
        });
        await new Promise((r) => setTimeout(r, Math.min(3000 + transientFailures * 1000, 12000)));
        continue;
      }
      metaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: `Scan status failed: ${status.error}` });
      updateActionCenter({ title: "Scan Status Failed", message: status.error, severity: "error" });
      return;
    }
    transientFailures = 0;
    const data = status.data || {};
    if (data.status === "running") {
      unknownStatusStreak = 0;
      updateTopStrategyChip(null);
      const elapsed = data.elapsed_seconds ?? (
        data.started_at ? Math.max(0, Math.floor((Date.now() - Date.parse(data.started_at)) / 1000)) : null
      );
      metaEl.textContent = elapsed !== null ? `Scan running... ${elapsed}s elapsed` : "Scan running...";
      updateActionCenter({
        title: "Scan Running",
        message:
          elapsed !== null
            ? `Local scan in progress (${elapsed}s elapsed). Results will appear when complete.`
            : "Local scan in progress. Results will appear when complete.",
        severity: "info",
      });
      await new Promise((r) => setTimeout(r, 5000));
      continue;
    }
    if (data.status === "completed") {
      unknownStatusStreak = 0;
      const tableRows = applyScanResponseSignals(data);
      const headline = diagnosticsHeadline(data.diagnostics_summary || data.diagnostics || {});
      metaEl.textContent =
        (headline || buildScanMeta(state.latestSignals, data.signals_found ?? state.latestSignals.length))
        + formatStrategySummary(data.strategy_summary);
      updateTopStrategyChip(data.strategy_summary);
      renderDiagnostics(data.diagnostics_summary || data.diagnostics || {});
      renderScanRows(tableRows);
      logEvent({ kind: "scan", severity: "info", message: `Scan complete: ${data.signals_found ?? state.latestSignals.length} signal(s).` });
      void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_SCAN, {
        transport: "local_polling",
        signals_found: safeNum(data.signals_found, state.latestSignals.length),
      });
      updateActionCenter({
        title: "Scan Complete",
        message: `Found ${data.signals_found ?? state.latestSignals.length} signal(s).`,
        severity: "success",
      });
      void refreshDecisionDashboard();
      return;
    }
    if (data.status === "failed") {
      unknownStatusStreak = 0;
      metaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      const errMsg = data.error || "unknown error";
      logEvent({ kind: "scan", severity: "error", message: errMsg });
      updateActionCenter({ title: "Scan Failed", message: errMsg, severity: "error" });
      return;
    }
    if (data.status === "idle" && data.last_scan) {
      unknownStatusStreak = 0;
      metaEl.textContent = `Last scan: ${data.last_scan.signals_found ?? 0} signal(s).`;
      updateTopStrategyChip(data.last_scan.strategy_summary || null);
      updateActionCenter({
        title: "Scan Idle",
        message: `No active scan. Last run: ${data.last_scan.signals_found ?? 0} signal(s).`,
        severity: "info",
      });
      return;
    }
    if (data.status === "unknown" || !safeText(data.status)) {
      unknownStatusStreak += 1;
      if (unknownStatusStreak >= 6) {
        const errMsg = safeText(data.error || data.status_error || "Local scan status remained unknown.");
        metaEl.textContent = "Scan status is unavailable.";
        updateTopStrategyChip(null);
        logEvent({ kind: "scan", severity: "error", message: errMsg });
        updateActionCenter({ title: "Scan Status Unavailable", message: errMsg, severity: "error" });
        return;
      }
      metaEl.textContent = "Scan status unavailable… retrying.";
      updateActionCenter({
        title: "Scan Status Pending",
        message: "Waiting for scan lifecycle status to stabilize.",
        severity: "warn",
      });
      await new Promise((r) => setTimeout(r, 3000));
      continue;
    }
    unknownStatusStreak = 0;
    await new Promise((r) => setTimeout(r, 2000));
  }
  metaEl.textContent = "Scan still running. Use Refresh to check progress.";
  updateTopStrategyChip(null);
  logEvent({ kind: "scan", severity: "warn", message: "Scan still running in background; polling window ended." });
  updateActionCenter({
    title: "Scan Still Running",
    message: "Polling window ended. Use Refresh All to check progress.",
    severity: "warn",
  });
  } finally {
    localScanPollActive = false;
  }
}

function openQueueScanDialog(sig) {
  const dialog = document.getElementById("queueScanDialog");
  const headline = document.getElementById("queueScanHeadline");
  const qty = document.getElementById("queueScanQty");
  const note = document.getElementById("queueScanNote");
  if (!dialog || !sig) return;
  if (!isScanSignalStageable(sig)) {
    const reasons = formatFilterReasons(sig._filter_reasons);
    const hint = reasons.length ? reasons[0] : "Signal did not pass current scan gates.";
    logEvent({ kind: "trade", severity: "warn", message: `Cannot stage filtered candidate: ${hint}` });
    updateActionCenter({
      title: "Cannot stage",
      message: hint,
      severity: "warn",
    });
    return;
  }
  state.queueScanDraft = sig;
  const t = sig.ticker || sig.symbol || "?";
  if (headline) {
    const px = sig.price ?? sig.current_price;
    headline.textContent = `${t} · last price ${px != null ? formatMoney(px) : "—"}`;
  }
  if (qty) qty.value = "";
  if (note) note.value = "Queued from scan table";
  void loadQueueScanChecklist(sig);
  dialog.showModal();
}

async function loadQueueScanChecklist(sig) {
  const host = document.getElementById("queueScanChecklist");
  if (!host) return;
  const ticker = safeText(sig?.ticker || sig?.symbol || "").trim();
  if (!ticker) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = `<p class="muted">Loading pre-stage checklist…</p>`;
  const out = await api.get(`/api/decision-card/${encodeURIComponent(ticker)}`);
  if (!out.ok || !out.data) {
    host.innerHTML = `<p class="muted warn-text">Checklist unavailable: ${escapeHtml(safeText(out.error || "unknown"))}</p>`;
    return;
  }
  const checklist = out.data.checklist || {};
  const blocked = Boolean(checklist.blocked);
  const reasons = Array.isArray(checklist.block_reasons_plain)
    ? checklist.block_reasons_plain
    : Array.isArray(checklist.block_reasons)
      ? checklist.block_reasons
      : [];
  const prov = renderSignalProvenanceChip(sig);
  const stageable = isScanSignalStageable(sig);
  const items = [
    { ok: !sig.used_fallback_data, label: "Using primary market data (not a fallback feed)" },
    { ok: stageable, label: "Passed current scan filters" },
    { ok: !blocked, label: "Pre-trade safety checklist is clear" },
    {
      ok: safeText((sig.advisory || {}).confidence_bucket || "").toLowerCase() !== "low",
      label: "Advisory confidence is not low",
    },
    { ok: true, label: `Data source: ${prov.replace(/<[^>]+>/g, "")}` },
  ];
  host.innerHTML = `
    <div class="queue-scan-checklist ${blocked ? "queue-scan-checklist--blocked" : ""}">
      <strong>Before you queue</strong>
      <ul>${items
        .map(
          (it) =>
            `<li class="${it.ok ? "check-ok" : "check-fail"}">${it.ok ? "✓" : "✗"} ${escapeHtml(it.label)}</li>`,
        )
        .join("")}</ul>
      ${
        reasons.length
          ? `<p class="muted small">Issues to fix first: ${escapeHtml(reasons.slice(0, 3).join("; "))}</p>`
          : ""
      }
    </div>
  `;
  const confirmBtn = document.getElementById("queueScanConfirmBtn");
  if (confirmBtn) {
    const canStage = stageable && !blocked;
    confirmBtn.disabled = !canStage;
    confirmBtn.textContent = canStage ? "Add to queue" : "Can't queue yet";
    if (!stageable) {
      confirmBtn.title = "This candidate did not pass scan filters.";
    } else if (blocked) {
      confirmBtn.title = "Fix the checklist issues above before queuing.";
    } else {
      confirmBtn.title = "Add to Pending approvals for review.";
    }
  }
}

function closeQueueScanDialog() {
  const dialog = document.getElementById("queueScanDialog");
  state.queueScanDraft = null;
  dialog?.close();
}

async function confirmQueueScanDialog() {
  const sig = state.queueScanDraft;
  if (!sig) {
    closeQueueScanDialog();
    return;
  }
  if (!isScanSignalStageable(sig)) {
    logEvent({ kind: "trade", severity: "warn", message: "Cannot stage a filtered scan candidate." });
    updateActionCenter({
      title: "Cannot stage",
      message: "Only tradeable (kept) scan rows can be added to pending.",
      severity: "warn",
    });
    return;
  }
  const qtyRaw = document.getElementById("queueScanQty")?.value?.trim();
  const note = document.getElementById("queueScanNote")?.value?.trim() || "Queued from scan table";
  let qty = null;
  if (qtyRaw) {
    const n = parseInt(qtyRaw, 10);
    if (!Number.isFinite(n) || n < 1) {
      logEvent({ kind: "trade", severity: "warn", message: "Enter a positive whole number for quantity, or leave blank for auto sizing." });
      return;
    }
    qty = n;
  }
  const btn = document.getElementById("queueScanConfirmBtn");
  if (btn) btn.disabled = true;
  const payload = {
    ticker: sig.ticker || sig.symbol,
    price: sig.price ?? sig.current_price ?? null,
    signal: sig,
    note,
  };
  if (qty != null) payload.qty = qty;
  const out = await api.post("/api/pending-trades", payload);
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Queue failed: ${out.error}` });
    updateActionCenter({ title: "Queue failed", message: out.error, severity: "error" });
  } else {
    logEvent({ kind: "trade", severity: "info", message: `Queued ${payload.ticker} (${out.data.id})` });
    trackUiEvent("trade_staged", { source: "queue_scan_dialog" });
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_PENDING_TRADE, {
      source: "queue_scan_dialog",
      ticker: safeText(payload.ticker),
    });
    prefillResearchTicker(payload.ticker, { overwrite: true });
    updateActionCenter({ title: "Staged for approval", message: `${payload.ticker} added to pending queue.`, severity: "success" });
    await refreshPending();
    closeQueueScanDialog();
  }
  if (btn) btn.disabled = false;
}

async function submitManualPendingTrade() {
  const tEl = document.getElementById("manualPendingTicker");
  const qEl = document.getElementById("manualPendingQty");
  const nEl = document.getElementById("manualPendingNote");
  const ticker = (tEl?.value || "").trim().toUpperCase();
  if (!ticker) {
    logEvent({ kind: "trade", severity: "warn", message: "Enter a ticker to stage a trade." });
    return;
  }
  let qty = null;
  const qRaw = (qEl?.value || "").trim();
  if (qRaw) {
    const n = parseInt(qRaw, 10);
    if (!Number.isFinite(n) || n < 1) {
      logEvent({ kind: "trade", severity: "warn", message: "Quantity must be a positive whole number, or leave blank for auto sizing." });
      return;
    }
    qty = n;
  }
  const note = (nEl?.value || "").trim() || "Manual staging from dashboard";
  const btn = document.getElementById("manualPendingBtn");
  if (btn) btn.disabled = true;
  const payload = { ticker, note };
  if (qty != null) payload.qty = qty;
  const out = await api.post("/api/pending-trades", payload);
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Manual queue failed: ${out.error}` });
    updateActionCenter({ title: "Could not stage trade", message: out.error, severity: "error" });
  } else {
    logEvent({ kind: "trade", severity: "info", message: `Queued ${ticker} (${out.data.id})` });
    trackUiEvent("trade_staged", { source: "manual_pending_trade" });
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_PENDING_TRADE, {
      source: "manual_pending_trade",
      ticker: safeText(ticker),
    });
    prefillResearchTicker(ticker, { overwrite: true });
    updateActionCenter({ title: "Staged for approval", message: `${ticker} added to pending.`, severity: "success" });
    if (tEl) tEl.value = "";
    if (qEl) qEl.value = "";
    if (nEl) nEl.value = "";
    await refreshPending();
  }
  if (btn) btn.disabled = false;
}

// Non-lazy refreshers that only matter on one screen. Values are thunks so the
// underlying panel functions are only invoked when their screen is refreshed.
const SCREEN_REFRESH_JOBS = Object.freeze({
  operations: [],
  research: [],
  diagnostics: [
    ["decision_dashboard", () => refreshDecisionDashboard()],
    ["ablation_cycle", () => refreshAblationCycleStatus({ quiet: true })],
  ],
  settings: [["auth_debug", () => refreshAuthDebugPanel()]],
});

let refreshAllInFlight = null;

/**
 * Scoped refresh (Refresh button / `R` shortcut / non-auth boot).
 *
 * Always re-fetches the cheap global segments (status, account, pending) and
 * then only the panels that are actually relevant: the visible screen's
 * sections plus any lazy panels the user has already loaded on other screens
 * (so previously viewed data doesn't go stale). Panels never opened stay
 * deferred to the IntersectionObserver path in `setupLazySectionLoading`.
 *
 * Re-entrant calls (double-click, `R` mash) reuse the in-flight pass instead
 * of firing duplicate requests.
 */
function refreshAll() {
  if (refreshAllInFlight) return refreshAllInFlight;
  // Visible acknowledgment — a silent Refresh looks like a dead button.
  showToast("Refreshing visible panels…", "info", 1800);
  refreshAllInFlight = runScopedRefresh(currentScreenMode)
    .then((result) => {
      showToast("Refresh complete.", "success", 1600);
      return result;
    })
    .finally(() => {
      refreshAllInFlight = null;
    });
  return refreshAllInFlight;
}

async function runScopedRefresh(screen) {
  const jobs = [
    ["status", refreshStatus()],
    ["account", refreshAccountMe()],
    ["pending", refreshPending()],
  ];
  (SCREEN_REFRESH_JOBS[screen] || []).forEach(([name, thunk]) => {
    jobs.push([name, thunk()]);
  });
  const lazyKeys = new Set(SCREEN_LAZY_KEYS[screen] || []);
  Object.keys(lazyLoaded).forEach((key) => {
    if (lazyLoaded[key]) lazyKeys.add(key);
  });
  if (lazyKeys.has("portfolio")) setLoading({ portfolio: "Loading portfolio..." });
  lazyKeys.forEach((key) => {
    // Clear the flag so runLazyApi re-fetches; it re-marks the key loaded.
    lazyLoaded[key] = false;
    jobs.push([key, runLazyApi(key)]);
  });
  const results = await Promise.allSettled(jobs.map(([, promise]) => promise));
  results.forEach((result, idx) => {
    if (result.status === "rejected") {
      const [name] = jobs[idx];
      logEvent({ kind: "system", severity: "error", message: `Refresh segment failed (${name}): ${safeText(result.reason)}` });
    }
  });
}

function shouldAutoRunScanNow() {
  try {
    const lastRaw = Number(localStorage.getItem(AUTO_SCAN_STORAGE_KEY) || 0);
    if (Number.isFinite(lastRaw) && lastRaw > 0 && Date.now() - lastRaw < AUTO_SCAN_COOLDOWN_MS) {
      return false;
    }
  } catch {
    // Ignore storage read failures; fall through to runtime checks.
  }
  const scanBtn = document.getElementById("scanBtn");
  if (!scanBtn || scanBtn.disabled) return false;
  if (state.latestSignals?.length) return false;
  return true;
}

function markAutoScanTriggered() {
  try {
    localStorage.setItem(AUTO_SCAN_STORAGE_KEY, String(Date.now()));
  } catch {
    // Ignore storage write failures.
  }
}

async function maybeAutoRunScanOnLoad() {
  if (!shouldAutoRunScanNow()) return;
  markAutoScanTriggered();
  updateActionCenter({
    title: "Auto Scan",
    message: "Running scan automatically on load. You can rerun anytime with Run Scan.",
    severity: "info",
  });
  await runScan();
}

function markFeatureGuideSeen() {
  try {
    localStorage.setItem(FEATURE_GUIDE_SEEN_KEY, "1");
  } catch {
    // Ignore storage write failures.
  }
}

function hasSeenFeatureGuide() {
  try {
    return localStorage.getItem(FEATURE_GUIDE_SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

function openFeatureGuide({ markSeen = true } = {}) {
  const dialog = document.getElementById("featureGuideDialog");
  if (!dialog) return false;
  if (dialog.open) return true;
  if (markSeen) markFeatureGuideSeen();
  if (typeof dialog.showModal === "function") {
    dialog.showModal();
    return true;
  }
  dialog.setAttribute("open", "open");
  return true;
}

function closeFeatureGuide() {
  const dialog = document.getElementById("featureGuideDialog");
  if (!dialog?.open) return;
  dialog.close();
}

function setupFeatureGuideFirstClick() {
  if (hasSeenFeatureGuide()) return;
  const handler = () => {
    if (hasSeenFeatureGuide()) {
      document.removeEventListener("click", handler, true);
      return;
    }
    const blockingDialog = document.querySelector("dialog[open]:not(#featureGuideDialog)");
    if (blockingDialog) return;
    const opened = openFeatureGuide({ markSeen: true });
    if (opened) document.removeEventListener("click", handler, true);
  };
  document.addEventListener("click", handler, true);
}

/**
 * Safe DOM binder. Logs (but never throws) when an element is missing so a
 * single stale id can't take down the whole bootstrap. Returns the element
 * (or null) for callers that want to do more with it.
 */
function bindEvent(elementId, eventName, handler, options) {
  const el = document.getElementById(elementId);
  if (!el) {
    logEvent({
      kind: "system",
      severity: "warn",
      message: `wireEvents: missing #${elementId} (skipped ${eventName} binding)`,
    });
    return null;
  }
  el.addEventListener(eventName, handler, options);
  return el;
}

/**
 * Constructs the per-screen controllers (static/screens/*) with their shared
 * dependency context. app.js stays the shell (auth/config boot, router,
 * flags, priority feed, shared topbar/drawer wiring); each controller owns
 * the one-time wiring and prime() loading for its screen. The order of the
 * returned map matters: research wiring restores the persisted backtest form
 * first, matching the legacy wireEvents sequence.
 */
function buildScreenControllers() {
  const ctx = {
    // Shared utilities
    bindEvent,
    state,
    api,
    safeText,
    logEvent,
    updateActionCenter,
    runLazyApi,
    // Operations
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
    getScanDetailSignal: () => _scanDetailSignal,
    approveTradeById,
    syncApproveDialogGuardrails,
    // Research
    restoreBacktestFormFromStorage,
    setDefaultBacktestDates,
    syncBtUniverseRow,
    wireBacktestFormPersistence,
    renderStrategyChatMessages,
    switchBacktestHubTab,
    applyBacktestPresetYears,
    sendStrategyChat,
    queueUserBacktest,
    refreshBacktestRuns,
    resetBacktestFormToDefaults,
    quickCheck,
    runReport,
    runResearchDossier,
    downloadResearchDossier,
    downloadResearchFundamentalWorkbook,
    loadResearchDossierPreflight,
    runSecCompare,
    applySecCompareMode,
    resetSecCompareProfileOverride,
    renderSecCompareVisual,
    wireSecCompareActions,
    openTradeDrawer,
    applyReportViewMode,
    mapRecovery,
    refreshPerformance,
    loadPortfolioRiskDashboard,
    wirePortfolioSubtabs,
    renderEvolvePanel,
    renderChallengerPanel,
    runAblationCycle,
    refreshAblationCycleStatus,
    // Diagnostics
    refreshCalibration,
    refreshShadowScoreboard,
    refreshReviewLoop,
    runReviewBackfill,
    refreshDecisionDashboard,
    // Settings
    applyProfile,
    openFeatureGuide,
    closeFeatureGuide,
    markFeatureGuideSeen,
    submitEnableLiveTrading,
    submitTradingHaltSave,
    beginBillingCheckout,
    openBillingPortal,
    loadProfiles,
    setRankExplainMode,
    renderPresetApplyPreview,
    // Cockpit
    initCockpitPanel,
    primeCockpitPanel,
    refreshScanDeltas,
    updateResearchSummaryLanding,
    openResearchForTicker,
  };
  return {
    research: createResearchController(ctx),
    operations: createOperationsController(ctx),
    settings: createSettingsController(ctx),
    diagnostics: createDiagnosticsController(ctx),
    cockpit: createCockpitController(ctx),
  };
}

function wireFeatureGuideChrome() {
  // Global chrome so the first-run guide can always close, even if Settings
  // controller init fails or never primes.
  document.getElementById("featureGuideCloseBtn")?.addEventListener("click", closeFeatureGuide);
  document.getElementById("featureGuideDialog")?.addEventListener("close", markFeatureGuideSeen);
  document.getElementById("featureGuideDialog")?.addEventListener("click", (e) => {
    if (e.target?.id === "featureGuideDialog") closeFeatureGuide();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeFeatureGuide();
  });
}

function wireEvents() {
  wireFeatureGuideChrome();
  setupFeatureGuideFirstClick();
  // Per-screen wiring lives in static/screens/* controllers. Each controller
  // initializes in isolation so one failing screen cannot break the others.
  screenControllers = buildScreenControllers();
  Object.values(screenControllers).forEach((controller) => {
    try {
      controller.init();
    } catch (err) {
      console.error(`[init] screen:${controller.id} wiring failed`, err);
      logEvent({
        kind: "system",
        severity: "error",
        message: `Screen wiring failed (${controller.id}): ${String(err?.message || err)}`,
      });
    }
  });
  document.querySelectorAll("[data-forward-click]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = safeText(btn.getAttribute("data-forward-click"));
      if (!targetId) return;
      document.getElementById(targetId)?.click();
    });
  });
  bindEvent("refreshBtn", "click", refreshAll);
  // Close button + Esc + backdrop are wired inside panels/tradeDrawer.js.
  bindEvent("activityDrawerToggle", "click", () => {
    const body = document.getElementById("activityDrawerBody");
    const toggle = document.getElementById("activityDrawerToggle");
    const drawer = document.getElementById("activityDrawer");
    if (!body || !toggle || !drawer) return;
    const setActivityDrawerOpen = (open) => {
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      body.classList.toggle("open", open);
      drawer.classList.toggle("open", open);
      document.body.classList.toggle("activity-drawer-open", open);
    };
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    setActivityDrawerOpen(!expanded);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const body = document.getElementById("activityDrawerBody");
    const toggle = document.getElementById("activityDrawerToggle");
    const drawer = document.getElementById("activityDrawer");
    if (!body || !toggle || !drawer) return;
    if (!body.classList.contains("open")) return;
    toggle.setAttribute("aria-expanded", "false");
    body.classList.remove("open");
    drawer.classList.remove("open");
    document.body.classList.remove("activity-drawer-open");
  });
  const navLinks = [...document.querySelectorAll(".section-nav a")]
    .filter((a) => String(a.getAttribute("href") || "").startsWith("#"));
  const sections = navLinks
    .map((a) => document.querySelector(a.getAttribute("href")))
    .filter(Boolean);
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const id = entry.target.getAttribute("id");
      navLinks.forEach((a) => {
        const active = a.getAttribute("href") === `#${id}`;
        a.classList.toggle("active", active);
        a.setAttribute("aria-current", active ? "location" : "false");
      });
    });
  }, { rootMargin: "-35% 0px -55% 0px", threshold: 0.01 });
  sections.forEach((section) => observer.observe(section));

  const screenSwitchButtons = [...document.querySelectorAll(".screen-switch-btn[data-screen-mode]")];
  screenSwitchButtons.forEach((btn, idx) => {
    btn.addEventListener("click", () => {
      const mode = btn.getAttribute("data-screen-mode") || "operations";
      applyScreenMode(mode, { updateUrl: true });
    });
    btn.addEventListener("keydown", (e) => {
      const key = e.key;
      if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(key)) return;
      e.preventDefault();
      const total = screenSwitchButtons.length;
      if (!total) return;
      let nextIdx = idx;
      if (key === "ArrowRight") nextIdx = (idx + 1) % total;
      else if (key === "ArrowLeft") nextIdx = (idx - 1 + total) % total;
      else if (key === "Home") nextIdx = 0;
      else if (key === "End") nextIdx = total - 1;
      const nextBtn = screenSwitchButtons[nextIdx];
      if (!nextBtn) return;
      const mode = nextBtn.getAttribute("data-screen-mode") || "operations";
      applyScreenMode(mode, { updateUrl: true });
      nextBtn.focus();
    });
  });

  window.addEventListener("hashchange", () => {
    const inferred = inferScreenFromHash();
    if (inferred) applyScreenMode(inferred, { updateUrl: true });
  });

  document.getElementById("displayModeSelect")?.addEventListener("change", (e) => {
    const v = e.target.value;
    applyDisplayMode(v);
    if (v === "pro" && state.performance) {
      const panel = document.getElementById("performancePanel");
      if (panel) renderPerformancePanel(panel, state.performance);
    }
  });
}

/* ── Scroll-to-top button ─────────────────────── */
/* ── Server-Sent Events ───────────────────────── */
let _sseSource = null;
function buildSseUrl() {
  const u = new URL("/api/events", window.location.origin);
  if (state.publicConfig?.api_key_required) {
    const key = (localStorage.getItem("tradingbot.api_key") || "").trim();
    if (key) u.searchParams.set("api_key", key);
  }
  return `${u.pathname}${u.search}`;
}
function connectSSE() {
  if (!state.sseEnabled) return;
  if (_sseSource) return;
  _sseSource = new EventSource(buildSseUrl());
  _sseSource.addEventListener("connected", () => {
    logEvent({ kind: "system", severity: "info", message: "Live connection established." });
  });
  _sseSource.addEventListener("message", (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      const event = msg.event;
      if (event === "scan_started") {
        const btn = document.getElementById("scanBtn");
        if (btn) { btn.disabled = true; btn.textContent = "Scanning..."; }
        updateActionCenter({ title: "Scan Running", message: "Market scan started. Results will appear automatically.", severity: "info" });
      } else if (event === "scan_completed") {
        const btn = document.getElementById("scanBtn");
        if (btn) { btn.disabled = false; btn.textContent = "Run Scan"; }
        const count = msg.signals_found ?? 0;
        showToast(`Scan complete: ${count} signal(s) found`, "success", 4000);
        addNotification(`Scan complete: ${count} signal(s) found`, "success");
        updateActionCenter({ title: "Scan Complete", message: `Found ${count} signal(s). Refreshing data...`, severity: "success" });
        refreshStatus();
        refreshPending();
      } else if (event === "scan_failed") {
        const btn = document.getElementById("scanBtn");
        if (btn) { btn.disabled = false; btn.textContent = "Run Scan"; }
        showToast("Scan failed: " + (msg.error || "unknown error"), "error", 6000);
        addNotification(`Scan failed: ${msg.error || "unknown"}`, "error");
        updateActionCenter({ title: "Scan Failed", message: msg.error || "Unknown error", severity: "error" });
      } else if (event === "trade_created") {
        showToast(`Trade queued: ${msg.ticker || "?"} (${msg.qty || "?"} shares)`, "info", 3000);
        addNotification(`Trade queued: ${msg.ticker || "?"} (${msg.qty || "?"} shares)`, "info");
        refreshPending();
      } else if (event === "trade_approved") {
        showToast(`Trade executed: ${msg.ticker || "?"}`, "success", 4000);
        addNotification(`Trade executed: ${msg.ticker || "?"}`, "success");
        refreshPending();
      } else if (event === "trade_rejected") {
        showToast(`Trade rejected: ${msg.ticker || "?"}`, "warn", 3000);
        addNotification(`Trade rejected: ${msg.ticker || "?"}`, "info");
        refreshPending();
      } else if (event === "trade_failed") {
        showToast(`Trade failed: ${msg.ticker || "?"} — ${msg.error || ""}`, "error", 5000);
        addNotification(`Trade failed: ${msg.ticker || "?"} — ${msg.error || ""}`, "error");
        refreshPending();
      }
    } catch { /* ignore malformed events */ }
  });
  _sseSource.onerror = () => {
    _sseSource.close();
    _sseSource = null;
    if (state.sseEnabled) setTimeout(connectSSE, 5000);
  };
}

(async () => {
  // Wrap each step so a stale/missing element in one area can't kill all the
  // downstream init (and leave the page looking dead with no buttons working).
  function safeInit(label, fn) {
    try {
      const result = fn();
      return result instanceof Promise
        ? result.catch((err) => {
            console.error(`[init] ${label} failed`, err);
            logEvent({ kind: "system", severity: "error", message: `${label} failed: ${String(err?.message || err)}` });
            try { showToast(`Init step failed: ${label}. Some buttons may not work.`, "error", 6000); } catch { /* ignore */ }
          })
        : result;
    } catch (err) {
      console.error(`[init] ${label} failed`, err);
      logEvent({ kind: "system", severity: "error", message: `${label} failed: ${String(err?.message || err)}` });
      try { showToast(`Init step failed: ${label}. Some buttons may not work.`, "error", 6000); } catch { /* ignore */ }
      return undefined;
    }
  }

  safeInit("initFeatureFlags", () => {
    initFeatureFlags();
  });
  safeInit("applyResearchSlimDefault", () => {
    ["sectorsSection", "moversSection", "cockpitMergedPanel", "researchAdvancedTools"].forEach((id) => {
      const el = document.getElementById(id);
      if (el && el.tagName === "DETAILS") el.open = false;
    });
  });
  safeInit("applySystemSlimDefault", () => {
    ["statusDetailsPanel", "systemDecisionPanel", "systemQualityDiagnostics"].forEach((id) => {
      const el = document.getElementById(id);
      if (el && el.tagName === "DETAILS") el.open = false;
    });
  });
  safeInit("applySettingsSlimDefault", () => {
    ["settingsAccountPanel", "authDebugPanel"].forEach((id) => {
      const el = document.getElementById(id);
      if (el && el.tagName === "DETAILS") el.open = false;
    });
  });
  safeInit("initSettingsSummaryRefresh", () => {
    const refresh = () => updateSettingsSummaryLanding();
    window.addEventListener("settings_summary_refresh", refresh);
    document.getElementById("profileSelect")?.addEventListener("change", refresh);
    refresh();
  });
  safeInit("initResearchSummaryRefresh", () => {
    const refresh = () => updateResearchSummaryLanding();
    window.addEventListener("research_summary_refresh", refresh);
    window.addEventListener("research_tab_change", refresh);
    document.getElementById("tickerInput")?.addEventListener("input", refresh);
    refresh();
  });
  safeInit("initSystemAlertRefresh", () => {
    window.addEventListener("priority_feed_change", () => refreshSystemAlertBanner());
  });
  safeInit("initPriorityFeed", () => {
    if (!isFlagEnabled("priority_feed")) return;
    initPriorityFeed({
      onAction: ({ key, severity }) => trackUiEvent("priority_feed_action_clicked", { item_key: key, severity }),
    });
  });
  safeInit("initResearchTabs", initResearchTabs);
  // Scan-table panel gets its cross-panel callbacks once, up front, so every
  // later renderScanRows() call (scan responses, SSE, sort clicks) can reach
  // the chart panel, hero KPIs, staging dialog, and trade drawer.
  safeInit("configureScanTable", () =>
    configureScanTable({
      renderScanDetail,
      highlightSelectedScanRow,
      updateHeroInfographic,
      renderDiagnostics,
      runScan,
      openQueueScanDialog,
      openTradeDrawer,
    }),
  );
  safeInit("configureApproveDialog", () =>
    configureApproveDialog({
      refreshPending,
      trackUiEvent,
      trackFunnelMilestoneOnce,
      FUNNEL_EVENTS,
    }),
  );
  safeInit("wireEvents", wireEvents);
  safeInit("setupScrollToTop", setupScrollToTop);
  safeInit("setupCommandPalette", () =>
    setupCommandPalette({ runLazyApi, applyDisplayMode, applyScreenMode, openTradeDrawer }),
  );
  safeInit("setupKeyboardShortcuts", () =>
    setupKeyboardShortcuts({
      openCommandPalette,
      closeCommandPalette,
      showToast,
      applyDisplayMode,
      applyScreenMode,
    }),
  );
  safeInit("setupNotifications", setupNotifications);
  safeInit("installRouter", () => {
    installRouter();
    // installRouter rewrites ?section= deep links into a #hash via
    // history.replaceState, which does NOT fire hashchange. Run the route
    // handler once here before applyScreenMode writes any ?screen= param.
    // Skip Supabase magic-link / OAuth hashes — those are not section ids.
    if (window.location.hash && !isSupabaseAuthCallbackHash(window.location.hash)) {
      handleRouteHash();
    }
  });
  safeInit("applyDisplayMode", () => applyDisplayMode(consumeDisplayModeFromUrl() || getDisplayMode()));
  safeInit("applyScreenMode", () => applyScreenMode(getScreenModeFromUrl(), { updateUrl: true }));
  safeInit("applyReportViewMode", applyReportViewMode);
  safeInit("applySecCompareMode", applySecCompareMode);
  safeInit("updateScanModeHelperText", updateScanModeHelperText);
  safeInit("applyRankExplainModeSelection", applyRankExplainModeSelection);
  await safeInit("loadConfig", loadConfig);
  if (state.sseEnabled) safeInit("connectSSE", connectSSE);
  await authSessionReady;
  const token = await getApiAccessToken();
  if (token) {
    scheduleRetainedSessionTracking();
    await safeInit("refreshCritical", refreshCritical);
    await safeInit("maybeAutoRunScanOnLoad", maybeAutoRunScanOnLoad);
    safeInit("markDeferredDataPlaceholders", markDeferredDataPlaceholders);
    safeInit("setupLazySectionLoading", setupLazySectionLoading);
  } else if (state.config?.auth_mode === "supabase") {
    const returning = hasVerifiedEmailOnce();
    updateActionCenter({
      title: returning ? "Sign in to continue" : "Verify your email to get started",
      message: returning
        ? "Your session expired. Sign in with your email link to load portfolio, pending trades, and billing-protected actions."
        : "Verify your email once with the link we send to load portfolio, pending trades, and billing-protected actions. You only do this once.",
      severity: "info",
    });
    safeInit("setupLazySectionLoading", setupLazySectionLoading);
  } else {
    await safeInit("refreshAll", refreshAll);
    await safeInit("maybeAutoRunScanOnLoad", maybeAutoRunScanOnLoad);
    safeInit("markDeferredDataPlaceholders", markDeferredDataPlaceholders);
    safeInit("setupLazySectionLoading", setupLazySectionLoading);
  }
  safeInit("updateActivityBadge", updateActivityBadge);
  try {
    if (!localStorage.getItem("tradingbot.keyboard_hint_shown")) {
      localStorage.setItem("tradingbot.keyboard_hint_shown", "1");
      showToast("Tip: press ? for shortcuts · Ctrl+K command palette · Ctrl+1..4 switch tabs", "info", 6500);
    }
  } catch {
    /* ignore storage failures */
  }
  logEvent({ kind: "system", severity: "info", message: "Dashboard loaded." });
})();

