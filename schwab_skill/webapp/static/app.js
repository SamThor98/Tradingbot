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
  prettyJson,
  formatMoney,
  formatDecimal,
  pct,
  formatPercentPoints,
  clampPct,
  verdictFromScore,
  timeAgo,
  durationSec,
  formatCount,
} from "./modules/format.js";
import { api } from "./modules/api.js";
import {
  applyFreshness,
  markUnavailable,
  clearUnavailable,
  FRESHNESS_BUDGETS_SEC,
} from "./modules/freshness.js";
import {
  setAsyncState,
  busyButton,
  retryGet,
  ASYNC_LOADING,
  ASYNC_EMPTY,
  ASYNC_ERROR,
  ASYNC_SUCCESS,
} from "./modules/asyncState.js";
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
} from "./modules/auth.js";
import { showToast, addNotification, setupNotifications } from "./modules/notifications.js";
import { setupScrollToTop } from "./modules/scrollToTop.js";
import {
  clearOAuthQueryParams,
  installRouter,
} from "./modules/router.js";
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
  startOnboarding as _startOnboardingPanel,
  runOnboardingStep as _runOnboardingStepPanel,
  triggerSchwabAccountOAuth,
  triggerSchwabMarketOAuth,
} from "./panels/onboarding.js";
import {
  renderCalibrationPanel,
  refreshCalibration,
  submitTradingHaltSave as _submitTradingHaltSavePanel,
} from "./panels/calibration.js";
import {
  loadDecisionCard,
  mapRecovery,
  openTradeDrawer,
  openTradeDrawerForTrade,
} from "./panels/tradeDrawer.js";
import { refreshSectors } from "./panels/sectors.js";
import {
  renderQuickCheckCard,
  quickCheck,
  renderTickerChart,
} from "./panels/quickCheck.js";
// Quick-view, decision-card, and recovery have been merged into the
// unified slide-in trade drawer (see imports above).
import {
  refreshPortfolio as _refreshPortfolioPanel,
  loadPortfolioRisk,
} from "./panels/portfolio.js";
import {
  applySecCompareMode,
  renderSecAnalysisCard,
  toReadableDeltaLabel,
  buildNarrativeSummary,
  renderSecCompareEmpty,
  renderSecCompareVisual as _renderSecCompareVisualPanel,
  buildFallbackSecCompare,
  runSecCompare as _runSecComparePanel,
} from "./panels/sec.js";
import {
  renderReportTabs,
  renderReportVisual,
  applyReportViewMode,
  runReport,
  runResearchDossier,
  downloadResearchDossier,
} from "./panels/report.js";
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
import { renderDecisionDashboard } from "./panels/decisionDashboard.js";

// Thin wrappers preserve the call signatures used by `wireEvents`,
// `connectSSE`, `runLazyApi`, etc. without leaking the panel-module
// dependency-injection contract into every call site.
const submitEnableLiveTrading = () =>
  _submitEnableLiveTradingPanel({ refreshAccountMe, refreshPending });
const refreshOnboarding = () => _refreshOnboardingPanel({ runLazyApi });
const startOnboarding = () => _startOnboardingPanel({ runLazyApi });
const runOnboardingStep = (step) => _runOnboardingStepPanel(step, { runLazyApi });
const submitTradingHaltSave = () =>
  _submitTradingHaltSavePanel({ refreshAccountMe });
const refreshPortfolio = () => _refreshPortfolioPanel({ runScan });
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

const lazyLoaded = {
  portfolio: false,
  sectors: false,
  performance: false,
  backtest: false,
  onboarding: false,
  profiles: false,
  calibration: false,
};

const SCREEN_MODES = Object.freeze(["operations", "research", "diagnostics", "settings"]);
const SCREEN_CONTEXT = Object.freeze({
  operations: {
    title: "Today",
    text: "Run a scan, look over the candidates, and approve only the trades you like.",
    ctaLabel: "Run a scan",
    ctaHref: "#scanSection",
    altCtaLabel: "Review pending",
    altCtaHref: "#pendingSection",
  },
  research: {
    title: "Analyze",
    text: "Review pending approvals, then run research and diligence before you size up.",
    ctaLabel: "Quick check",
    ctaHref: "#quickCheckSection",
    altCtaLabel: "Open backtests",
    altCtaHref: "#backtestSection",
  },
  diagnostics: {
    title: "Health",
    text: "How the system is doing right now \u2014 connections, data quality, and recent runs.",
    ctaLabel: "Health ribbon",
    ctaHref: "#healthRibbon",
    altCtaLabel: "Detailed status",
    altCtaHref: "#statusDetailsPanel",
  },
  settings: {
    title: "Settings",
    text: "Connect your Schwab account, choose a strategy preset, and manage live-trading guardrails.",
    ctaLabel: "Connections",
    ctaHref: "#onboardingSection",
    altCtaLabel: "Trading controls",
    altCtaHref: "#settingsSection",
  },
});
const SCREEN_NUDGE_KEY_PREFIX = "tradingbot.ui.screen_seen.";
const SCREEN_SECTIONS = Object.freeze({
  operations: [
    "dashboardToday",
    "operationsWorkspaceIntro",
    "workflowPrimary",
    "scanSection",
    "scanDetailPanel",
    "pendingSection",
  ],
  research: [
    "researchWorkspaceIntro",
    "researchWorkflowStrip",
    "quickCheckSection",
    "toolsSection",
    "recoverySection",
    "learningSection",
    "backtestSection",
    "reportSectionCard",
    "secCompareSection",
    "activitySection",
    "portfolioSection",
    "sectorsSection",
    "performanceSection",
  ],
  diagnostics: [
    "dashboardToday",
    "diagnosticsWorkspaceIntro",
    "diagnosticsWorkflowStrip",
    "healthRibbon",
    "decisionDashboardCard",
    "blockersAlertSection",
    "statusDetailsPanel",
    "calibrationSection",
  ],
  settings: ["settingsWorkspaceIntro", "settingsWorkflowStrip", "onboardingSection", "settingsSection"],
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

function resetLazyLoaded() {
  Object.keys(lazyLoaded).forEach((k) => {
    lazyLoaded[k] = false;
  });
}

function getDisplayMode() {
  const m = localStorage.getItem(UI_VIEW_MODE_KEY) || "simple";
  return ["simple", "standard", "pro"].includes(m) ? m : "simple";
}

function normalizeScreenMode(raw) {
  const mode = safeText(raw).toLowerCase();
  return SCREEN_MODES.includes(mode) ? mode : "operations";
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
  if (hintEl) hintEl.textContent = "Press Ctrl/Cmd + 1 Trade, 2 Analyze, 3 Health, 4 Settings.";
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
  if (mode === "settings") {
    void runLazyApi("onboarding");
    void runLazyApi("profiles");
  } else if (mode === "diagnostics") {
    void runLazyApi("calibration");
  } else if (mode === "research") {
    void runLazyApi("backtest");
    void runLazyApi("portfolio");
    void runLazyApi("sectors");
    void runLazyApi("performance");
  }
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
    research: "Use reports, SEC compare, backtests, and portfolio context to validate trades.",
    diagnostics: "Use this screen to troubleshoot without interrupting trade flow.",
  };
  const hint = nudgeMap[mode] || "Use the context actions to jump into this screen.";
  showToast(`${cfg.title}: ${hint}`, "info", 2800);
}

function applyScreenMode(mode, { updateUrl = false } = {}) {
  const m = normalizeScreenMode(mode);
  currentScreenMode = m;
  document.body.classList.add("ui-screen-switching");
  if (screenSwitchTimer) clearTimeout(screenSwitchTimer);
  screenSwitchTimer = window.setTimeout(() => {
    document.body.classList.remove("ui-screen-switching");
    screenSwitchTimer = null;
  }, 170);
  document.body.classList.remove("ui-screen-operations", "ui-screen-research", "ui-screen-diagnostics", "ui-screen-settings");
  document.body.classList.add(`ui-screen-${m}`);
  refreshScreenSwitchUi(m);
  refreshSectionNavForScreen(m);
  renderScreenContext(m);
  maybePrimeScreenData(m);
  maybeShowScreenNudge(m);
  if (updateUrl) writeScreenModeToUrl(m);
}

function applyDisplayMode(mode) {
  const m = ["simple", "standard", "pro"].includes(mode) ? mode : "standard";
  localStorage.setItem(UI_VIEW_MODE_KEY, m);
  document.body.classList.remove("ui-simple", "ui-standard", "ui-pro");
  document.body.classList.add(`ui-${m}`);
  const sel = document.getElementById("displayModeSelect");
  if (sel) sel.value = m;
  const pro = m === "pro";
  const scanDiag = document.getElementById("scanDiagnosticsPanel");
  const statusDet = document.getElementById("statusDetailsPanel");
  const secDeep = document.getElementById("secCompareDeepPanel");
  if (scanDiag) scanDiag.open = pro;
  if (statusDet) statusDet.open = pro;
  if (secDeep) secDeep.open = pro;
  const blockerAlert = document.getElementById("blockersAlertSection");
  if (blockerAlert && !pro) blockerAlert.classList.add("hidden");
  const perfRaw = document.getElementById("performanceRawDetails");
  if (perfRaw && !pro) perfRaw.open = false;
}

async function runLazyApi(key) {
  if (!key || lazyLoaded[key]) return;
  lazyLoaded[key] = true;
  try {
    if (key === "portfolio") await refreshPortfolio();
    else if (key === "sectors") await refreshSectors();
    else if (key === "performance") await refreshPerformance();
    else if (key === "backtest") await refreshBacktestRuns();
    else if (key === "onboarding") await refreshOnboarding();
    else if (key === "profiles") {
      await loadProfiles();
    } else if (key === "calibration") {
      await refreshCalibration();
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
        const k = e.target.getAttribute("data-lazy-api");
        if (k) void runLazyApi(k);
      });
    },
    { rootMargin: "120px 0px", threshold: 0.04 }
  );
  nodes.forEach((n) => io.observe(n));
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
    } else {
      killBanner.classList.add("hidden");
    }
    // Stamp freshness whenever we re-evaluate, even if hidden — so toggling
    // the banner on/off carries a "verified at" label.
    applyFreshness(freshEl, {
      asOf: new Date().toISOString(),
      source: "/api/public-config",
      surface: "status_details",
      unavailable: "config not loaded",
    });
  }
  if (!block) return;
  if (!state.publicConfig.saas_mode) {
    block.classList.add("hidden");
    return;
  }
  block.classList.remove("hidden");
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
  const line = document.getElementById("billingStatusLine");
  const checkoutBtn = document.getElementById("billingCheckoutBtn");
  const portalBtn = document.getElementById("billingPortalBtn");
  if (!card || !line || !checkoutBtn || !portalBtn) return;
  if (!state.publicConfig?.saas_mode || !state.accountMe) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");
  const billingEnforced = Boolean(state.accountMe.billing_enforced);
  const active = Boolean(state.accountMe.subscription_active);
  const status = safeText(state.accountMe.subscription_status || "none").toLowerCase();
  const hasStripeCustomer = Boolean(state.accountMe.has_stripe_customer);
  line.textContent = billingEnforced
    ? (active
      ? `Subscription active (${status}). Premium routes are unlocked.`
      : "No active subscription. Start checkout to unlock protected scan and trade flows.")
    : `Billing enforcement is off (status: ${status}). You can still open checkout/portal for production readiness.`;
  checkoutBtn.disabled = false;
  portalBtn.disabled = !hasStripeCustomer;
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
    return;
  }
  const token = await getApiAccessToken();
  if (!token) {
    state.accountMe = null;
    renderLiveTradingSaasPanel();
    renderBillingPanel();
    return;
  }
  const out = await api.get("/api/me");
  state.accountMe = out.ok ? out.data : null;
  renderLiveTradingSaasPanel();
  renderBillingPanel();
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
  updateSupabaseAuthUI(session);

  sb.auth.onAuthStateChange((_event, nextSession) => {
    persistApiJwtFromSession(nextSession);
    updateSupabaseAuthUI(nextSession);
    if (nextSession?.access_token) scheduleRetainedSessionTracking();
    void refreshAccountMe();
  });

  document.getElementById("supabaseSignInBtn")?.addEventListener("click", async () => {
    const email = document.getElementById("supabaseEmail")?.value?.trim() || "";
    const password = document.getElementById("supabasePassword")?.value || "";
    if (!email || !password) {
      logEvent({ kind: "system", severity: "warn", message: "Enter email and password." });
      return;
    }
    const { error } = await sb.auth.signInWithPassword({ email, password });
    if (error) logEvent({ kind: "system", severity: "error", message: error.message });
    else logEvent({ kind: "system", severity: "info", message: "Signed in." });
  });

  document.getElementById("supabaseSignUpBtn")?.addEventListener("click", async () => {
    const email = document.getElementById("supabaseEmail")?.value?.trim() || "";
    const password = document.getElementById("supabasePassword")?.value || "";
    if (!email || !password) {
      logEvent({ kind: "system", severity: "warn", message: "Enter email and password to sign up." });
      return;
    }
    const { error } = await sb.auth.signUp({ email, password });
    if (error) logEvent({ kind: "system", severity: "error", message: error.message });
    else {
      logEvent({
        kind: "system",
        severity: "info",
        message: "Sign-up sent. Check email if confirmation is required, then sign in.",
      });
      void trackFunnelMilestoneOnce(FUNNEL_EVENTS.SIGNUP, {
        source: "supabase_password_signup",
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

function buildScanMeta(signals = [], count = null) {
  const total = count ?? signals.length;
  const high = signals.filter((s) => (s?.advisory?.confidence_bucket || "").toLowerCase() === "high").length;
  if (high > 0) return `Found ${total} signal(s). High-confidence: ${high}.`;
  return `Found ${total} signal(s).`;
}

function diagnosticsHeadline(diagOrSummary = null) {
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

function formatStrategyLabel(value) {
  const raw = safeText(value || "").trim();
  if (!raw || raw === "—") return "—";
  return raw
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b[a-z]/g, (ch) => ch.toUpperCase());
}

function optionalNum(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed || trimmed === "—") return null;
  }
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function normalizeProbability(value) {
  const n = optionalNum(value);
  if (n === null) return null;
  // Backward compatibility for older payloads that persisted percent points (e.g. 62.4).
  const ratio = n > 1 && n <= 100 ? n / 100 : n;
  return Math.max(0, Math.min(1, ratio));
}

function formatConfidenceLabel(value) {
  const raw = safeText(value || "").trim();
  if (!raw || raw === "—") return "—";
  const lowered = raw.toLowerCase();
  if (lowered === "unknown" || lowered === "none" || lowered === "null") return "—";
  return raw.replace(/[_-]+/g, " ").toUpperCase();
}

function asObject(value) {
  if (!value) return null;
  if (typeof value === "object" && !Array.isArray(value)) return value;
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function normalizeScanSignal(rawSignal) {
  const base = asObject(rawSignal) || {};
  const nested = asObject(base.signal) || {};
  const signal = { ...base, ...nested };
  signal.advisory = asObject(signal.advisory) || {};
  signal.mirofish_result = asObject(signal.mirofish_result) || {};
  signal.strategy_attribution = asObject(signal.strategy_attribution) || {};
  signal.prediction_market = asObject(signal.prediction_market) || {};
  return signal;
}

function signalFromScanResultRow(row) {
  const rec = asObject(row) || {};
  const payload = asObject(rec.payload) || {};
  const signal = normalizeScanSignal(payload);
  if (!signal.ticker && rec.ticker) signal.ticker = rec.ticker;
  if (!signal.symbol && rec.ticker) signal.symbol = rec.ticker;
  if (signal.signal_score == null && rec.signal_score != null) signal.signal_score = rec.signal_score;
  if (!signal.job_id && rec.job_id) signal.job_id = rec.job_id;
  if (signal.flagged_days == null && rec.flagged_days != null) signal.flagged_days = rec.flagged_days;
  return signal;
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

function setHealthRibbonUnavailable(reason) {
  const ribbon = document.getElementById("healthRibbon");
  if (ribbon) ribbon.setAttribute("data-async-state", "error");
  ["ribbonAuth", "ribbonQuotes", "ribbonApiErrorRate", "ribbonValidation"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = "health-badge bg-slate-900";
    el.textContent = "Unknown";
    markUnavailable(el, reason || "status fetch failed");
  });
  ["healthTileAuth", "healthTileQuotes", "healthTileApi", "healthTileValidation"].forEach((id) => {
    const tile = document.getElementById(id);
    if (!tile) return;
    tile.dataset.state = "unknown";
    tile.style.setProperty("--gauge", "0");
  });
  const noteIso = new Date().toISOString();
  ["ribbonAuthFresh", "ribbonQuotesFresh", "ribbonApiErrorRateFresh", "ribbonValidationFresh"].forEach((id) => {
    applyFreshness(document.getElementById(id), {
      asOf: null,
      source: "/api/status",
      surface: "health_ribbon",
      unavailable: reason ? `unavailable: ${reason}` : "unavailable",
    });
  });
  // Reference noteIso so eslint stays quiet; the timestamp is unused but kept
  // for future "last failure at" labels.
  void noteIso;
}

function setHealthRibbonTiles(authOk, quoteOk, errRate, validation) {
  const setTile = (id, stateName, gauge) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.dataset.state = stateName;
    el.style.setProperty("--gauge", String(gauge));
  };
  setTile("healthTileAuth", authOk ? "good" : "bad", authOk ? 1 : 0);
  setTile("healthTileQuotes", quoteOk ? "good" : "bad", quoteOk ? 1 : 0);
  const er = safeNum(errRate, 0);
  const apiGaugeHealth = Math.max(0, Math.min(1, 1 - er / 18));
  const apiState = er < 2 ? "good" : er < 8 ? "warn" : "bad";
  setTile("healthTileApi", apiState, apiGaugeHealth);

  const v = validation || {};
  const runStatus = safeText(v.run_status || "").toLowerCase();
  let vState = "neutral";
  let vGauge = 0.35;
  if (v.exists && v.passed === true) {
    vState = "good";
    vGauge = 1;
  } else if (v.exists && v.passed === false) {
    vState = "bad";
    vGauge = 0.12;
  } else if (runStatus === "running") {
    vState = "warn";
    const pct = safeNum(v.progress_pct, 0);
    vGauge = Math.max(0.25, Math.min(0.92, pct > 0 ? pct / 100 : 0.55));
  } else if (v.exists) {
    vState = "warn";
    vGauge = 0.55;
  }
  setTile("healthTileValidation", vState, vGauge);
}

function prioritizeActionCenterFromHealth({ authOk, quoteOk, errRate, validation, topBlocker, quoteHealth }) {
  const runStatus = safeText(validation?.run_status || "").toLowerCase();
  const blocker = safeText(topBlocker || "").trim();
  if (!authOk) {
    updateActionCenter({
      title: "P0: Broker Authentication Blocked",
      message: "Reconnect Schwab account and market sessions before running scans or approving orders.",
      severity: "error",
    });
    return;
  }
  if (!quoteOk || errRate >= 3.0) {
    const qh = quoteHealth && typeof quoteHealth === "object" ? quoteHealth : {};
    const quoteReason = safeText(qh.reason || "").trim();
    const quoteHint = safeText(qh.operator_hint || "").trim();
    const quoteMsg = quoteOk
      ? ""
      : `Quotes unhealthy${quoteReason ? ` (${quoteReason})` : ""}${quoteHint ? `: ${quoteHint}` : "."}`;
    const apiMsg = `API server error rate is ${errRate.toFixed(1)}%.`;
    const message =
      !quoteOk && errRate >= 3.0
        ? `${quoteMsg} ${apiMsg} Check provider status and fallback readiness.`
        : !quoteOk
          ? `${quoteMsg} Check provider status and fallback readiness.`
          : `${apiMsg} Check provider status and fallback readiness.`;
    updateActionCenter({
      title: "P1: Market Data Reliability Degraded",
      message,
      severity: "warn",
    });
    return;
  }
  if (runStatus === "running") {
    updateActionCenter({
      title: "P2: Validation In Progress",
      message: "Validation pipeline is running; monitor progress before trusting new model outputs.",
      severity: "info",
    });
    return;
  }
  if (blocker) {
    updateActionCenter({
      title: "P2: Scan Blocker Identified",
      message: blocker,
      severity: "warn",
    });
  }
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
      markUnavailable(pendEl, "/api/pending-trades not loaded");
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
      markUnavailable(wlEl, "scan diagnostics have not reported watchlist_size yet");
    } else {
      clearUnavailable(wlEl);
      wlEl.textContent = formatCount(n);
    }
  }
  applyFreshness(wlFreshEl, {
    asOf: state.lastScanAt,
    source: state.lastScanAt ? "/api/scan diagnostics" : "SP1500 default",
    surface: "scan_results",
    unavailable: "scan to populate",
  });
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
      title: "Runtime Contract Mismatch",
      message,
      severity: "error",
    });
  }
}

function buildDiagnosticsSummary(diag = {}) {
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
function assertScanDeltasReconcile(diag, funnel, signals) {
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

function buildFunnelStages(diag, watchlistOverride, finalCount) {
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

function renderDiagnostics(diag = {}) {
  const chipWrap = document.getElementById("scanDiagnostics");
  const blockersEl = document.getElementById("scanBlockers");
  const funnelEl = document.getElementById("scanFunnel");
  const alertWrap = document.getElementById("blockersAlertSection");
  const alertList = document.getElementById("blockersAlertList");
  chipWrap.innerHTML = "";
  blockersEl.innerHTML = "";
  funnelEl.innerHTML = "";
  if (alertList) alertList.innerHTML = "";

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
  const showBlockerAlert = getDisplayMode() === "pro";
  const headerChip = document.getElementById("scanBlockersChip");
  const headerChipCount = document.getElementById("scanBlockersChipCount");
  if (!summary.blockers.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No major blockers detected.";
    blockersEl.appendChild(empty);
    if (alertWrap) alertWrap.classList.add("hidden");
    if (headerChip) headerChip.classList.add("hidden");
    if (headerChipCount) headerChipCount.textContent = "0";
  } else {
    summary.blockers.forEach((b) => {
      const li = document.createElement("li");
      li.innerHTML = `${b.label}: <strong>${b.value}</strong> <span class="${statusClass(b.severity)}">${b.severity}</span>`;
      blockersEl.appendChild(li);
      if (showBlockerAlert && alertList) {
        const alertLi = document.createElement("li");
        alertLi.innerHTML = `${b.label}: <strong>${b.value}</strong>`;
        alertList.appendChild(alertLi);
      }
    });
    if (alertWrap) alertWrap.classList.toggle("hidden", !showBlockerAlert);
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
  updateHeroInfographic();
  const diagPanel = document.getElementById("scanDiagnosticsPanel");
  if (diagPanel && getDisplayMode() === "pro") diagPanel.open = true;
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

let _scanDetailChart = null;
let _scanDetailResizeObserver = null;
let _scanDetailSignal = null;

function syncScanDetailStageButton(signal) {
  const btn = document.getElementById("scanDetailStageBtn");
  if (!btn) return;
  const sig = normalizeScanSignal(signal || {});
  const ticker = safeText(sig.ticker || sig.symbol || "");
  if (!ticker) {
    btn.disabled = true;
    btn.textContent = "Stage selected trade";
    btn.title = "Select a candidate first.";
    return;
  }
  const status = safeText(sig._filter_status || "kept").toLowerCase();
  const stageable = status === "kept";
  btn.disabled = !stageable;
  btn.textContent = stageable ? `Stage ${ticker}` : `${ticker} filtered`;
  btn.title = stageable
    ? `Stage ${ticker} into pending approvals.`
    : "Filtered candidates cannot be staged. Adjust gates or scan options to include this setup.";
}

function renderScanDetailChartMessage(message) {
  const container = document.getElementById("scanDetailChartContainer");
  if (!container) return;
  container.innerHTML = `<p class="muted">${safeText(message || "Chart unavailable.")}</p>`;
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
  if (_scanDetailChart) {
    try {
      _scanDetailChart.remove();
    } catch {
      // ignore chart cleanup failures
    }
    _scanDetailChart = null;
  }
  if (!ticker) {
    renderScanDetailChartMessage("Select a ticker to load chart data.");
    return;
  }
  if (typeof LightweightCharts === "undefined") {
    renderScanDetailChartMessage("Chart library unavailable.");
    return;
  }

  container.innerHTML = "";
  const out = await api.get(`/api/chart/${encodeURIComponent(ticker)}`);
  if (!out.ok || !out.data?.candles?.length) {
    renderScanDetailChartMessage(`No chart data available for ${ticker}.`);
    return;
  }

  const chart = LightweightCharts.createChart(container, {
    width: getScanDetailChartWidth(container),
    height: 240,
    layout: { background: { type: "solid", color: "transparent" }, textColor: "#9ca3b8" },
    grid: {
      vertLines: { color: "rgba(99,120,200,0.06)" },
      horzLines: { color: "rgba(99,120,200,0.06)" },
    },
    rightPriceScale: { borderColor: "rgba(99,120,200,0.15)" },
    timeScale: { borderColor: "rgba(99,120,200,0.15)", timeVisible: false },
  });
  const candleSeries = chart.addCandlestickSeries({
    upColor: "#34d399",
    downColor: "#fb7185",
    borderUpColor: "#34d399",
    borderDownColor: "#fb7185",
    wickUpColor: "#34d399",
    wickDownColor: "#fb7185",
  });
  candleSeries.setData(out.data.candles);
  chart.timeScale().fitContent();
  _scanDetailChart = chart;
  _scanDetailResizeObserver = new ResizeObserver(() => {
    if (_scanDetailChart) _scanDetailChart.applyOptions({ width: getScanDetailChartWidth(container) });
  });
  _scanDetailResizeObserver.observe(container);
}

async function renderScanDetail(sig) {
  const row = normalizeScanSignal(sig || {});
  const ticker = safeText(row.ticker || row.symbol || "");
  _scanDetailSignal = ticker ? row : null;
  state.selectedScanTicker = ticker;
  highlightSelectedScanRow(ticker);
  const advisory = row.advisory || {};
  const score = optionalNum(row.signal_score ?? row.score);
  const conviction = optionalNum(row.mirofish_conviction ?? row.conviction_score ?? row?.mirofish_result?.conviction_score);
  const pUp = normalizeProbability(advisory.p_up_10d ?? advisory.p_up_10d_raw ?? row.p_up_10d ?? row.advisory_p_up);
  const confidence = formatConfidenceLabel(advisory.confidence_bucket ?? row.confidence_bucket ?? row.advisory_confidence);
  const strategy = formatStrategyLabel(row?.strategy_attribution?.top_live || "—");

  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  setText("scanDetailTicker", ticker || "Select a ticker");
  setText("scanDetailStrategy", ticker ? `Top strategy: ${strategy}` : "Choose a scan row to review chart and scoring context.");
  setText("scanDetailPrice", row.price || row.current_price ? formatMoney(row.price || row.current_price) : "—");
  setText("scanDetailScore", score === null ? "—" : formatDecimal(score, 1));
  setText("scanDetailPup", pUp === null ? "—" : pct(pUp, 1));
  setText("scanDetailConfidence", confidence || "—");
  setText("scanDetailConviction", conviction === null ? "—" : formatDecimal(conviction, 1));
  setText("scanDetailSector", safeText(row.sector_etf || "—"));
  syncScanDetailStageButton(_scanDetailSignal);
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

// Map a raw `_filter_status` value from the scanner shortlist into a
// human-readable label, a CSS pill class, and a tooltip explaining the
// disposition. Unknown values fall through as "—" so the table still
// renders cleanly even if the scanner emits a new status we haven't
// taught the dashboard about yet.
function formatScanStatusBadge(status, reasons) {
  const safeStatus = safeText(status || "").toLowerCase();
  const reasonText = Array.isArray(reasons) && reasons.length ? reasons.join(", ") : "";
  switch (safeStatus) {
    case "kept":
      return {
        label: "Kept",
        cls: "pill success",
        title: "Survived all filters and is eligible for trade staging.",
      };
    case "filtered_quality_gates":
      return {
        label: "Quality gate",
        cls: "pill warn",
        title: reasonText
          ? `Dropped by quality gates. Reasons: ${reasonText}.`
          : "Dropped by quality gates (forensic / breakout-volume / etc).",
      };
    case "filtered_self_study":
      return {
        label: "Self-study",
        cls: "pill warn",
        title: "Dropped by self-study learned minimum conviction.",
      };
    case "filtered_event_risk":
      return {
        label: "Event risk",
        cls: "pill warn",
        title: "Suppressed by event-risk policy (earnings, FOMC, etc).",
      };
    case "filtered_meta_policy":
      return {
        label: "Meta-policy",
        cls: "pill warn",
        title: "Suppressed by the meta-policy / uncertainty combiner.",
      };
    case "filtered_ensemble":
      return {
        label: "Ensemble",
        cls: "pill warn",
        title: "Removed by the strategy ensemble step.",
      };
    case "trimmed_top_n":
      return {
        label: "Top-N trim",
        cls: "pill info",
        title: "Survived gates but ranked below SIGNAL_TOP_N — kept for review.",
      };
    default:
      return { label: "—", cls: "pill", title: "No disposition reported." };
  }
}

function renderScanRows(signals = []) {
  const body = document.getElementById("scanTableBody");
  body.innerHTML = "";
  if (!signals.length) {
    body.innerHTML = `
      <tr>
        <td colspan="11" class="muted">
          <div class="empty-state-cell">
            <svg class="empty-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 8h16M6 12h12M9 16h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
              <rect x="3" y="4" width="18" height="16" rx="2.5" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            <div>No signal candidates yet.</div>
            <button id="scanEmptyCtaBtn" class="btn small secondary" type="button">Run Scan to Begin</button>
          </div>
        </td>
      </tr>
    `;
    const cta = document.getElementById("scanEmptyCtaBtn");
    if (cta) cta.addEventListener("click", runScan);
    void renderScanDetail(null);
    updateHeroInfographic();
    return;
  }

  let pupCount = 0;
  let confCount = 0;
  let convictionCount = 0;
  signals.forEach((sig, idx) => {
    const row = normalizeScanSignal(sig);
    const ticker = row.ticker || row.symbol || "?";
    const flaggedDays = Math.max(0, safeNum(row.flagged_days ?? row.days_flagged, 0));
    const topLive = formatStrategyLabel(row?.strategy_attribution?.top_live || "—");
    const score = safeNum(row.signal_score ?? row.score, null);
    const advisory = row.advisory;
    const conviction = optionalNum(row.mirofish_conviction ?? row.conviction_score ?? row?.mirofish_result?.conviction_score);
    const pUp = normalizeProbability(advisory.p_up_10d ?? advisory.p_up_10d_raw ?? row.p_up_10d ?? row.advisory_p_up);
    const conf = formatConfidenceLabel(advisory.confidence_bucket ?? row.confidence_bucket ?? row.advisory_confidence);
    const convictionText = conviction === null ? "—" : formatDecimal(conviction, 1);
    // `_filter_status` is set by the scanner shortlist; falls back to "kept"
    // for legacy responses that don't include it (e.g. older API versions).
    const filterStatus = safeText(sig?._filter_status || "kept");
    const isKept = filterStatus === "kept";
    const filterReasons = Array.isArray(sig?._filter_reasons) ? sig._filter_reasons : null;
    const badge = formatScanStatusBadge(filterStatus, filterReasons);
    if (pUp !== null) pupCount += 1;
    if (conf !== "—") confCount += 1;
    if (conviction !== null) convictionCount += 1;
    const tr = document.createElement("tr");
    tr.setAttribute("data-scan-ticker", ticker);
    tr.setAttribute("data-scan-row-index", String(idx));
    tr.setAttribute("data-filter-status", filterStatus);
    if (!isKept) tr.classList.add("scan-row--filtered");
    tr.tabIndex = 0;
    const stageBtn = isKept
      ? `<button type="button" class="btn small secondary" data-idx="${idx}" title="Stage ${safeText(ticker)} as a pending trade">Stage</button>`
      : `<button type="button" class="btn small secondary" disabled title="Filtered candidates cannot be staged. Adjust gates if you want this signal in the trade queue.">Stage</button>`;
    tr.innerHTML = `
      <td><strong>${safeText(ticker)}</strong></td>
      <td><span class="${badge.cls}" title="${escapeHtml(badge.title)}">${escapeHtml(badge.label)}</span></td>
      <td class="scan-col-advanced">${flaggedDays || "—"}</td>
      <td><span class="pill info strategy-badge">${topLive}</span></td>
      <td class="scan-col-secondary">${row.price || row.current_price ? formatMoney(row.price || row.current_price) : "—"}</td>
      <td>${score !== null ? `${score.toFixed(1)}` : "—"}</td>
      <td class="scan-col-advanced">${pUp !== null ? pct(pUp, 1) : "—"}</td>
      <td>${conf}</td>
      <td class="scan-col-advanced">${convictionText}</td>
      <td class="scan-col-advanced">${safeText(row.sector_etf || "—")}</td>
      <td class="scan-actions-cell">
        <button type="button" class="btn small secondary" data-scan-view="${idx}" title="Open chart and scoring detail for ${safeText(ticker)}">Chart</button>
        ${stageBtn}
      </td>
    `;
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
      highlightSelectedScanRow(state.selectedScanTicker);
    } else {
      state.selectedScanTicker = "";
      void renderScanDetail(null);
    }
  } else {
    void renderScanDetail(null);
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
      if (!raw) return;
      openQueueScanDialog(normalizeScanSignal(raw));
    });
  });
  body.querySelectorAll("button[data-scan-view]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const idx = Number(e.currentTarget.getAttribute("data-scan-view"));
      const raw = lookupRowSignal(idx, e.currentTarget);
      if (!raw) return;
      void renderScanDetail(normalizeScanSignal(raw));
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
      if (sig) void renderScanDetail(sig);
    };
    const justSelect = () => {
      const sig = resolveSignal();
      if (!sig) return;
      const ticker = safeText(sig?.ticker || sig?.symbol || "");
      state.selectedScanTicker = ticker;
      highlightSelectedScanRow(ticker);
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
  updateHeroInfographic();
}

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

function renderPendingContext(row) {
  const sig = row.signal || {};
  const score = sig.signal_score ?? sig.score;
  const sector = sig.sector_etf;
  const conviction = sig.mirofish_conviction;
  const advisory = sig.advisory || {};
  const pUp = normalizeProbability(advisory.p_up_10d ?? advisory.p_up_10d_raw ?? sig.p_up_10d ?? sig.advisory_p_up);
  const confidence = formatConfidenceLabel(advisory.confidence_bucket ?? sig.confidence_bucket ?? sig.advisory_confidence);
  return `score: ${score !== undefined ? safeNum(score).toFixed(0) : "—"}<br/>
    sector: ${safeText(sector || "—")}<br/>
    confidence: ${safeText(confidence || "—")} · P(up 10d): ${pUp === null ? "—" : pct(pUp, 1)}<br/>
    conviction: ${conviction !== undefined ? safeText(conviction) : "—"}`;
}

function getPendingRiskProfile(row) {
  const sig = row?.signal || {};
  const score = safeNum(sig.signal_score ?? sig.score, 0);
  const advisory = sig.advisory || {};
  const confidence = formatConfidenceLabel(advisory.confidence_bucket ?? sig.confidence_bucket ?? sig.advisory_confidence);
  const hasSector = Boolean(safeText(sig.sector_etf || "").trim());
  const lowConfidence = ["low", "unknown", "—"].includes(String(confidence || "—").toLowerCase());
  if (!hasSector || score < 60 || lowConfidence) return { label: "Requires extra review", severity: "high" };
  if (score < 72) return { label: "Moderate confidence", severity: "medium" };
  return { label: "Ready to review", severity: "low" };
}

function renderTimeline(row) {
  const status = (row.status || "").toLowerCase();
  if (status === "pending") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> waiting action</span>`;
  if (status === "executed") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> approved -> executed</span>`;
  if (status === "rejected") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> rejected</span>`;
  if (status === "failed") return `<span class="timeline-badge"><span class="timeline-dot"></span>queued -> approve attempted -> failed</span>`;
  return `<span class="timeline-badge"><span class="timeline-dot"></span>${safeText(status)}</span>`;
}

function formatPreflightChecklistHtml(c) {
  if (!c || typeof c !== "object") return "";
  const lines = Array.isArray(c.checklist_lines) ? c.checklist_lines : [];
  const plainItems = lines
    .map((line) => {
      if (!line || typeof line !== "object") return "";
      const lb = safeText(line.label);
      const vl = safeText(line.value_plain);
      return `<li><strong>${lb}:</strong> ${vl}</li>`;
    })
    .filter(Boolean)
    .join("");
  let blockSection = "";
  if (c.blocked) {
    const br = Array.isArray(c.block_reasons_plain) ? c.block_reasons_plain : [];
    const brHtml = br.length ? br.map((t) => `<li>${safeText(t)}</li>`).join("") : "";
    const fallback = brHtml || "<li>Policy blocked this order.</li>";
    blockSection = `<p class="approve-blocked"><strong>Cannot send yet</strong></p><ul>${fallback}</ul>`;
  }
  const techJson = safeText(prettyJson(c));
  const tech = `<details class="approve-checklist-details"><summary>Technical checklist</summary><pre class="code-block code-block--tight">${techJson}</pre></details>`;
  return `<div class="approve-preflight"><strong>Pre-trade summary</strong><ul>${plainItems || "<li>No extra checklist rows.</li>"}</ul>${blockSection}${tech}</div>`;
}

async function openApproveDialog(row) {
  const dialog = document.getElementById("approveDialog");
  const summary = document.getElementById("approveSummary");
  const est = safeNum(row.price, 0) * safeNum(row.qty, 0);
  const sig = row.signal || {};
  const expectedTicker = safeText(row.ticker).toUpperCase();
  state.approvingExpectedTicker = expectedTicker;
  const riskHint = (!sig.sector_etf || safeNum(sig.signal_score, 0) < 60)
    ? "Caution: missing sector or lower-confidence setup."
    : "Setup context looks complete.";
  let checklistText = "";
  const preflight = await api.get(`/api/trades/${row.id}/preflight`);
  if (preflight.ok) {
    state.approvingChecklist = preflight.data?.checklist || null;
    const c = state.approvingChecklist || {};
    const hv = preflight.data?.high_value_2fa || {};
    checklistText = formatPreflightChecklistHtml(c);
    if (hv.required) {
      checklistText += `<p class="muted"><strong>High-value guardrail:</strong> 2FA code required for this approval.</p>`;
    }
  } else {
    checklistText = `<div class="approve-preflight muted">Checklist unavailable: ${safeText(preflight.error)}</div>`;
  }
  summary.innerHTML = `
    Approve BUY ${row.qty} ${row.ticker} @ ${row.price ? formatMoney(row.price) : "market"}?<br/>
    Est. value: <strong>${formatMoney(est)}</strong><br/>
    <span class="muted">${riskHint}</span>
    ${checklistText}
  `;
  const tickerInput = document.getElementById("approveTickerInput");
  const otpInput = document.getElementById("approveOtpInput");
  const riskAck = document.getElementById("approveRiskAck");
  if (tickerInput) {
    tickerInput.value = "";
    tickerInput.placeholder = expectedTicker || "TICKER";
  }
  if (otpInput) otpInput.value = "";
  if (riskAck) riskAck.checked = false;
  state.approvingTradeId = row.id;
  syncApproveDialogGuardrails();
  dialog.showModal();
}

function syncApproveDialogGuardrails() {
  const typed = (document.getElementById("approveTickerInput")?.value || "").trim().toUpperCase();
  const expected = safeText(state.approvingExpectedTicker || "").toUpperCase();
  const ack = Boolean(document.getElementById("approveRiskAck")?.checked);
  const hint = document.getElementById("approveConfirmHint");
  const btn = document.getElementById("confirmApproveBtn");
  const tickerMatch = expected && typed === expected;
  const canSubmit = Boolean(state.approvingTradeId) && tickerMatch && ack;
  if (btn) btn.disabled = !canSubmit;
  if (hint) {
    if (!typed) {
      hint.textContent = expected
        ? `Type ${expected} and confirm risk to enable live submit.`
        : "Type the ticker and confirm risk to enable live submit.";
    } else if (!tickerMatch) {
      hint.textContent = `Ticker mismatch. Enter ${expected} exactly.`;
    } else if (!ack) {
      hint.textContent = "Confirm the risk acknowledgement to enable submit.";
    } else {
      hint.textContent = "Ready to submit this live order.";
    }
    hint.className = `approve-confirm-hint ${canSubmit ? "good" : "warn"}`;
  }
}

function applySchwabConnectButtonVisibility() {
  const pc = state.publicConfig || {};
  document.getElementById("onboardingSchwabBtn")?.classList.toggle("hidden", !pc.schwab_oauth);
  document.getElementById("onboardingSchwabMarketBtn")?.classList.toggle("hidden", !pc.schwab_market_oauth);
  document.getElementById("onboardingSchwabLink")?.classList.toggle("hidden", !pc.schwab_oauth);
  document.getElementById("onboardingSchwabMarketLink")?.classList.toggle("hidden", !pc.schwab_market_oauth);
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
  if (publicCfg.api_key_required && !localStorage.getItem("tradingbot.api_key")) {
    const key = prompt("This server requires an API key for write operations.\nEnter your WEB_API_KEY:");
    if (key) localStorage.setItem("tradingbot.api_key", key.trim());
  }
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

  if (tokenInput) {
    tokenInput.value = manualJwtAllowed ? readStoredApiJwt() : "";
    tokenInput.disabled = !manualJwtAllowed;
  }
  if (saveBtn) {
    saveBtn.disabled = !manualJwtAllowed;
    saveBtn.addEventListener("click", () => {
      if (!manualJwtAllowed) return;
      const val = normalizeUserJwt(tokenInput?.value);
      if (val) {
        if (!isProbablyAccessJwt(val)) {
          logEvent({ kind: "system", severity: "error", message: JWT_BAD_SHAPE_HINT });
          return;
        }
        localStorage.setItem(AUTH_TOKEN_KEY, val);
        clearLegacyApiJwtKeys();
        void createCookieAuthSession(val);
        logEvent({ kind: "system", severity: "info", message: "JWT token saved locally." });
      } else {
        clearStoredApiJwt();
        void clearCookieAuthSession();
        logEvent({ kind: "system", severity: "warn", message: "JWT token cleared." });
      }
    });
  }
  if (copyBtn) {
    copyBtn.disabled = !manualJwtAllowed;
    copyBtn.addEventListener("click", async () => {
      if (!manualJwtAllowed) return;
      const token = normalizeUserJwt(tokenInput?.value || readStoredApiJwt());
      if (!token) {
        logEvent({ kind: "system", severity: "warn", message: "No JWT token found to copy." });
        return;
      }
      try {
        const ok = await copyTextToClipboard(token);
        logEvent({
          kind: "system",
          severity: ok ? "info" : "warn",
          message: ok ? "JWT token copied to clipboard." : "Copy was blocked by this browser.",
        });
      } catch {
        logEvent({ kind: "system", severity: "error", message: "Copy failed. Browser denied clipboard access." });
      }
    });
  }
  state.config = { auth_mode: hasSupabaseUi ? "supabase" : "jwt" };
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
        ? "Sign in with Supabase to access protected APIs. Your session token is used automatically."
        : `This server did not expose Supabase browser sign-in (set SUPABASE_URL and SUPABASE_ANON_KEY in Render to match your local .env). In Supabase → Authentication → URL configuration, add ${originHint} to Site URL and Redirect URLs.`,
      severity: "warn",
    });
  } else {
    updateActionCenter({
      title: "Authentication Required",
      message: hasSupabaseUi
        ? "Sign in with Supabase to access protected APIs. Your session token is used automatically."
        : "Sign in with Supabase to access protected APIs.",
      severity: "warn",
    });
  }

  const params = new URLSearchParams(window.location.search);
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
  // In SaaS mode the Schwab quote probe inside /api/status already populates
  // status.api_health (quote_ok + quote_health). Calling /api/health/deep on
  // top would trigger a SECOND probe per dashboard refresh, doubling Schwab
  // load and racing on the rotating refresh token. Synthesize deepRes from
  // the status payload when available. If api_health is missing (mixed-version
  // deployments), fall back to /api/health/deep to avoid false "Degraded"
  // state in the health ribbon. (Local mode keeps the legacy split because
  // /api/health/deep there also surfaces server-wide metrics counters.)
  const statusRes = await api.get("/api/status");
  let deepRes;
  if (saasMode) {
    if (statusRes.ok) {
      const ah = statusRes.data?.api_health || {};
      const hasEmbeddedApiHealth =
        Object.prototype.hasOwnProperty.call(ah, "quote_ok") ||
        Object.prototype.hasOwnProperty.call(ah, "quote_health") ||
        Object.prototype.hasOwnProperty.call(ah, "market_token_ok") ||
        Object.prototype.hasOwnProperty.call(ah, "account_token_ok");
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
    const quoteEl = document.getElementById("quoteHealth");
    const errEl = document.getElementById("apiErrorRate");
    const validationEl = document.getElementById("validationHealth");
    // Mark each detail pill unavailable. Honest "—" beats a confident "Unknown"
    // because "Unknown" can read as "passed Unknown check" to a tired user.
    [
      "marketToken",
      "accountToken",
      "quoteHealth",
      "validationHealth",
      "lastScan",
      "apiErrorRate",
    ].forEach((id) => markUnavailable(document.getElementById(id), statusRes.error || "/api/status failed"));
    // Reset ribbon to honest unknown.
    setHealthRibbonUnavailable(statusRes.error || "/api/status failed");
    updateActionCenter({ title: "Status unavailable", message: statusRes.error, severity: "error" });
    return;
  }

  const status = statusRes.data || {};
  try {
    await hydrateScanTableFromStatus(status);
  } catch (e) {
    console.warn("hydrateScanTableFromStatus", e);
  }
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
    validationAgeEl.textContent = validation.exists
      ? `Updated ${timeAgo(validation.generated_at)}${failHint}`
      : "No validation artifact yet.";
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
    setStatusPill(quoteEl, deepRes.data.quote_ok ? "Connected" : "Degraded");
    const qh = deepRes.data.quote_health;
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

  const authOk = Boolean(status.market_token_ok && status.account_token_ok);
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
    ribbonAuth.className = healthBadgeClass(authOk);
    ribbonAuth.textContent = authOk ? "Connected" : "Disconnected";
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
      markUnavailable(ribbonQuotes, deepRes.error || "/api/health/deep failed");
      ribbonQuotes.className = "health-badge bg-slate-900";
      ribbonQuotes.textContent = "Unknown";
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
      markUnavailable(ribbonValidation, "no validation artifact yet");
      ribbonValidation.className = "health-badge bg-slate-900";
      ribbonValidation.textContent = "Unknown";
    }
  }
  applyFreshness(document.getElementById("ribbonValidationFresh"), {
    asOf: validation.generated_at || null,
    source: "validation_status.generated_at",
    surface: "health_ribbon",
    budgetSec: 24 * 3600,
    unavailable: "no validation artifact",
  });
  setHealthRibbonTiles(authOk, quoteOk, errRate, validation);
  // Mark the ribbon container as success now that it has rendered real data.
  const ribbonContainer = document.getElementById("healthRibbon");
  if (ribbonContainer) ribbonContainer.setAttribute("data-async-state", "success");
  const topBlocker =
    status?.last_scan?.diagnostics_summary?.top_blockers?.[0]?.key ||
    status?.last_scan?.diagnostics_summary?.headline ||
    "";
  prioritizeActionCenterFromHealth({
    authOk,
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
}

async function refreshDecisionDashboard() {
  const card = document.getElementById("decisionDashboardCard");
  const freshEl = document.getElementById("decisionDashboardFresh");
  const out = await api.get("/api/decision-dashboard");
  if (!out.ok) {
    if (card) card.setAttribute("data-async-state", "error");
    const msg = safeText(out.user_message || out.error || "Decision dashboard unavailable.");
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
      "decisionLatestPromotion",
    ].forEach((id) => markUnavailable(document.getElementById(id), msg));
    applyFreshness(freshEl, {
      asOf: null,
      source: "/api/decision-dashboard",
      surface: "decision_dashboard",
      unavailable: msg,
    });
    return;
  }
  if (card) card.setAttribute("data-async-state", "success");
  // Clear any prior unavailable styling before the panel paints.
  [
    "decisionReliabilityState",
    "decisionPromotionState",
    "decisionValidationStatus",
    "decisionSloStatus",
    "decisionLastScan",
    "decisionSignalsFound",
    "decisionStrategyLead",
    "decisionDataQuality",
    "decisionLatestPromotion",
  ].forEach((id) => clearUnavailable(document.getElementById(id)));
  state.lastDecisionDashboardAt = new Date().toISOString();
  renderDecisionDashboard(out.data || {});
  applyFreshness(freshEl, {
    asOf: state.lastDecisionDashboardAt,
    source: "/api/decision-dashboard",
    surface: "decision_dashboard",
  });
}

const SCAN_START_META = "Scanning SP1500 market candidates...";

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
      metaEl.textContent = "Scan queued… waiting for worker.";
      setJobProgress("scanJobProgress", "scanJobProgressLabel", 0.12, "Queued — waiting for worker");
      const queuedMs = Date.now() - firstPendingAt;
      if (queuedMs > 50_000 && !workerHintShown) {
        workerHintShown = true;
        metaEl.textContent =
          "Still queued — no worker yet. Confirm Celery is running with queue \"scan\" and REDIS_URL matches the API.";
        updateActionCenter({
          title: "Scan waiting for worker",
          message:
            "If this stays queued, start workers with: celery -A webapp.tasks worker -Q scan,orders,celery — and use the same REDIS_URL as the app.",
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
  btn.disabled = true;
  btn.textContent = "Scanning...";
  setJobProgress("scanJobProgress", "scanJobProgressLabel", 0, "");
  setLoading({ scan: SCAN_START_META });
  updateActionCenter({
    title: "Scan Running",
    message: "SP1500 default scan is running. Results will stream into this page.",
    severity: "info",
  });
  try {
    if (!readScanOptionsFromForm()) return;
    const scanBody =
      state.scanRunOptions && typeof state.scanRunOptions === "object"
        ? state.scanRunOptions
        : {};
    const out = await api.post("/api/scan?async_mode=true", scanBody);
    if (!out.ok) {
      scanMetaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: out.error });
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
      updateActionCenter({
        title: "Scan",
        message: "Scan did not start. Check connection and try again.",
        severity: "warn",
      });
    }
  }
}

async function waitForScanCompletion() {
  const maxPolls = 360;
  const metaEl = document.getElementById("scanMeta");
  let unknownStatusStreak = 0;
  for (let i = 0; i < maxPolls; i++) {
    const status = await api.get("/api/scan-lifecycle");
    if (!status.ok) {
      metaEl.textContent = "Scan failed.";
      updateTopStrategyChip(null);
      logEvent({ kind: "scan", severity: "error", message: `Scan status failed: ${status.error}` });
      updateActionCenter({ title: "Scan Status Failed", message: status.error, severity: "error" });
      return;
    }
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
}

async function refreshPending() {
  const filter = document.getElementById("pendingFilter")?.value || state.pendingFilter;
  const sort = document.getElementById("pendingSort")?.value || state.pendingSort;
  state.pendingFilter = filter;
  state.pendingSort = sort;
  const board = document.getElementById("pendingBoard");
  if (board) {
    board.innerHTML = `<div class="task-empty muted">Loading pending trades...</div>`;
  }
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
        onRetry: () => void refreshPending(),
      });
    }
    // Honest "unavailable" for the count badge — never silently render 0.
    const pcEl = document.getElementById("pendingCount");
    if (pcEl) markUnavailable(pcEl, msg || "fetch failed");
    state.lastPendingCount = null;
    updateHeroInfographic();
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
  if (pendingN > 0) {
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_PENDING_TRADE, {
      source: "pending_queue_refresh",
      pending_count: pendingN,
    });
  }
  const clearBtn = document.getElementById("clearPendingBtn");
  if (clearBtn) clearBtn.disabled = pendingN === 0;
  updateHeroInfographic();

  board.innerHTML = "";
  if (!rows.length) {
    board.innerHTML = `<div class="task-empty muted">No trades match current filter.</div>`;
    return;
  }

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
      const score = meterFromScore(row?.signal?.signal_score ?? row?.signal?.score);
      const conviction = meterFromConviction(row?.signal?.mirofish_conviction);
      const liveBlocked =
        state.publicConfig.saas_mode &&
        (!state.accountMe || !state.accountMe.live_execution_enabled);
      const approveTitle = liveBlocked
        ? "Live trading is off — enable in Strategy Presets after reviewing risk."
        : "";
      const card = document.createElement("article");
      const risk = getPendingRiskProfile(row);
      card.className = `task-card task-card--risk-${risk.severity}`;
      card.innerHTML = `
        <div class="task-card-head">
          <div>
            <strong>${safeText(row.ticker)}</strong>
            <span class="muted">#${safeText(row.id)} • Qty ${safeText(row.qty)}</span>
          </div>
          <div class="task-card-badges">
            <span class="risk-chip ${risk.severity}">${safeText(risk.label)}</span>
            <span class="${statusClass(row.status)}">${safeText(row.status)}</span>
          </div>
        </div>
        <div class="task-meters">
          <div>
            <span class="meter-label">Score ${safeNum(row?.signal?.signal_score ?? row?.signal?.score, 0).toFixed(0)}</span>
            <div class="meter"><span style="width:${score}%"></span></div>
          </div>
          <div>
            <span class="meter-label">Conviction ${safeNum(row?.signal?.mirofish_conviction, 0).toFixed(0)}</span>
            <div class="meter conviction"><span style="width:${conviction}%"></span></div>
          </div>
        </div>
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
      openApproveDialog(row);
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
        await refreshPending();
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
        await refreshPending();
      } finally {
        release();
      }
    });
  });

  const strip = document.getElementById("pendingSummaryStrip");
  const stripText = document.getElementById("pendingSummaryText");
  if (strip && stripText) {
    if (pendingN > 0) {
      strip.classList.remove("hidden");
      stripText.textContent = `${pendingN} pending trade(s) need a decision.`;
    } else {
      strip.classList.add("hidden");
    }
  }
}

async function approveTradeById(id) {
  const typed = document.getElementById("approveTickerInput")?.value?.trim().toUpperCase() || "";
  const otpCode = document.getElementById("approveOtpInput")?.value?.trim() || "";
  const expected = safeText(state.approvingExpectedTicker || "").toUpperCase();
  const ack = Boolean(document.getElementById("approveRiskAck")?.checked);
  if (!typed) {
    updateActionCenter({
      title: "Confirm ticker",
      message: "Type the trade ticker in the box to confirm this live order.",
      severity: "warn",
    });
    return false;
  }
  if (expected && typed !== expected) {
    updateActionCenter({
      title: "Ticker mismatch",
      message: `Enter ${expected} exactly before approving this live order.`,
      severity: "warn",
    });
    return false;
  }
  if (!ack) {
    updateActionCenter({
      title: "Risk acknowledgement required",
      message: "Confirm the risk acknowledgement before submitting a live order.",
      severity: "warn",
    });
    return false;
  }
  const out = await api.post(`/api/trades/${id}/approve?confirm_live=true`, { typed_ticker: typed, otp_code: otpCode });
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Approve ${id} failed: ${out.error}` });
    updateActionCenter({ title: "Approval Failed", message: out.error, severity: "error" });
    return false;
  } else {
    logEvent({ kind: "trade", severity: "info", message: `Approved ${id}: order submitted.` });
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_APPROVED_TRADE, {
      source: "approve_dialog",
      trade_id: id,
    });
    updateActionCenter({ title: "Trade Approved", message: `Trade ${id} approved and submitted.`, severity: "success" });
    await refreshPending();
    return true;
  }
}

function openQueueScanDialog(sig) {
  const dialog = document.getElementById("queueScanDialog");
  const headline = document.getElementById("queueScanHeadline");
  const qty = document.getElementById("queueScanQty");
  const note = document.getElementById("queueScanNote");
  if (!dialog || !sig) return;
  state.queueScanDraft = sig;
  const t = sig.ticker || sig.symbol || "?";
  if (headline) {
    const px = sig.price ?? sig.current_price;
    headline.innerHTML = `${escapeHtml(t)} · last ${px != null ? escapeHtml(formatMoney(px)) : "—"}`;
  }
  if (qty) qty.value = "";
  if (note) note.value = "Queued from scan table";
  dialog.showModal();
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
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_PENDING_TRADE, {
      source: "queue_scan_dialog",
      ticker: safeText(payload.ticker),
    });
    updateActionCenter({ title: "Staged for approval", message: `${payload.ticker} added to pending.`, severity: "success" });
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
    void trackFunnelMilestoneOnce(FUNNEL_EVENTS.FIRST_PENDING_TRADE, {
      source: "manual_pending_trade",
      ticker: safeText(ticker),
    });
    updateActionCenter({ title: "Staged for approval", message: `${ticker} added to pending.`, severity: "success" });
    if (tEl) tEl.value = "";
    if (qEl) qEl.value = "";
    if (nEl) nEl.value = "";
    await refreshPending();
  }
  if (btn) btn.disabled = false;
}

async function refreshAll() {
  resetLazyLoaded();
  setLoading({ portfolio: "Loading portfolio..." });
  const jobs = [
    ["status", refreshStatus()],
    ["decision_dashboard", refreshDecisionDashboard()],
    ["account", refreshAccountMe()],
    ["pending", refreshPending()],
    ["portfolio", refreshPortfolio()],
    ["sectors", refreshSectors()],
    ["onboarding", refreshOnboarding()],
    ["profiles", loadProfiles()],
    ["performance", refreshPerformance()],
    ["calibration", refreshCalibration()],
    ["backtest", refreshBacktestRuns()],
  ];
  const results = await Promise.allSettled(jobs.map(([, promise]) => promise));
  results.forEach((result, idx) => {
    if (result.status === "rejected") {
      const [name] = jobs[idx];
      logEvent({ kind: "system", severity: "error", message: `Refresh segment failed (${name}): ${safeText(result.reason)}` });
    }
  });
  Object.keys(lazyLoaded).forEach((k) => {
    lazyLoaded[k] = true;
  });
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

function wireEvents() {
  restoreBacktestFormFromStorage();
  setDefaultBacktestDates();
  syncBtUniverseRow();
  wireBacktestFormPersistence();
  renderStrategyChatMessages();
  document.getElementById("btHubTabForm")?.addEventListener("click", () => switchBacktestHubTab("form"));
  document.getElementById("btHubTabChat")?.addEventListener("click", () => switchBacktestHubTab("chat"));
  document.getElementById("btUniverse")?.addEventListener("change", syncBtUniverseRow);
  document.querySelectorAll(".bt-preset").forEach((btn) => {
    btn.addEventListener("click", () => {
      const y = btn.getAttribute("data-years");
      if (y) applyBacktestPresetYears(y);
    });
  });
  document.querySelectorAll(".sc-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      const t = btn.getAttribute("data-text") || "";
      const input = document.getElementById("scInput");
      if (input) input.value = t;
      input?.focus();
    });
  });
  document.getElementById("scInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendStrategyChat();
    }
  });
  document.getElementById("btQueueBtn")?.addEventListener("click", queueUserBacktest);
  document.getElementById("btRefreshListBtn")?.addEventListener("click", refreshBacktestRuns);
  document.getElementById("btResetFormBtn")?.addEventListener("click", resetBacktestFormToDefaults);
  document.getElementById("queueScanCancelBtn")?.addEventListener("click", closeQueueScanDialog);
  document.getElementById("queueScanConfirmBtn")?.addEventListener("click", () => void confirmQueueScanDialog());
  document.getElementById("manualPendingBtn")?.addEventListener("click", () => void submitManualPendingTrade());
  document.getElementById("scanDetailStageBtn")?.addEventListener("click", () => {
    if (!_scanDetailSignal) return;
    const normalized = normalizeScanSignal(_scanDetailSignal);
    const status = safeText(normalized._filter_status || "kept").toLowerCase();
    if (status !== "kept") return;
    openQueueScanDialog(normalized);
  });
  document.getElementById("queueScanDialog")?.addEventListener("click", (e) => {
    if (e.target?.id === "queueScanDialog") closeQueueScanDialog();
  });
  document.getElementById("scSendBtn")?.addEventListener("click", sendStrategyChat);
  bindEvent("scanBtn", "click", runScan);
  document.querySelectorAll("[data-forward-click]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = safeText(btn.getAttribute("data-forward-click"));
      if (!targetId) return;
      document.getElementById(targetId)?.click();
    });
  });
  document.getElementById("scanApplyBacktestSpecBtn")?.addEventListener("click", () => void fillScanOptionsFromLatestBacktest());
  document.getElementById("scanClearOptionsBtn")?.addEventListener("click", () => {
    const ta = document.getElementById("scanOptionsJson");
    if (ta) ta.value = "";
    state.scanRunOptions = null;
  });
  bindEvent("refreshBtn", "click", refreshAll);
  document.getElementById("onboardingStartBtn")?.addEventListener("click", startOnboarding);
  document.getElementById("onboardingConnectBtn")?.addEventListener("click", () => runOnboardingStep("connect"));
  document.getElementById("onboardingVerifyBtn")?.addEventListener("click", () => runOnboardingStep("verify_token_health"));
  document.getElementById("onboardingScanBtn")?.addEventListener("click", () => runOnboardingStep("test_scan"));
  document.getElementById("onboardingPaperBtn")?.addEventListener("click", () => runOnboardingStep("test_paper_order"));
  document.getElementById("onboardingSchwabBtn")?.addEventListener("click", () => triggerSchwabAccountOAuth());
  document.getElementById("onboardingSchwabMarketBtn")?.addEventListener("click", () => triggerSchwabMarketOAuth());
  document.getElementById("onboardingSchwabLink")?.addEventListener("click", (e) => {
    e.preventDefault();
    void triggerSchwabAccountOAuth();
  });
  document.getElementById("onboardingSchwabMarketLink")?.addEventListener("click", (e) => {
    e.preventDefault();
    void triggerSchwabMarketOAuth();
  });
  bindEvent("applyProfileBtn", "click", applyProfile);
  document.getElementById("enableLiveTradingBtn")?.addEventListener("click", () => void submitEnableLiveTrading());
  document.getElementById("saveTradingHaltBtn")?.addEventListener("click", () => void submitTradingHaltSave());
  document.getElementById("billingCheckoutBtn")?.addEventListener("click", () => void beginBillingCheckout());
  document.getElementById("billingPortalBtn")?.addEventListener("click", () => void openBillingPortal());
  document.getElementById("calibrationRefreshBtn")?.addEventListener("click", () => void refreshCalibration());
  document.getElementById("portfolioRiskPanel")?.addEventListener("toggle", (e) => {
    if (e.target.open) void loadPortfolioRisk();
  });
  bindEvent("settingsModeSelect", "change", loadProfiles);
  document.getElementById("profileSelect")?.addEventListener("change", renderPresetApplyPreview);
  document.getElementById("automationOptIn")?.addEventListener("change", renderPresetApplyPreview);
  bindEvent("decisionBtn", "click", loadDecisionCard);
  bindEvent("recoveryBtn", "click", mapRecovery);
  bindEvent("performanceRefreshBtn", "click", refreshPerformance);
  document.getElementById("evolveBtn")?.addEventListener("click", async () => {
    const btn = document.getElementById("evolveBtn");
    const panel = document.getElementById("learningPanel");
    if (btn) { btn.disabled = true; btn.textContent = "Analyzing..."; }
    try {
      const out = await api.post("/api/evolve/run");
      if (out.ok) {
        renderEvolvePanel(panel, out.data);
      } else {
        if (panel) panel.innerHTML = `<div class="panel-error">${safeText(out.error || "Analysis failed")}</div>`;
      }
    } catch (e) {
      if (panel) panel.innerHTML = `<div class="panel-error">Error: ${safeText(String(e))}</div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Run Post-Mortem Analysis"; }
    }
  });
  document.getElementById("challengerBtn")?.addEventListener("click", async () => {
    const btn = document.getElementById("challengerBtn");
    const panel = document.getElementById("challengerPanel");
    if (btn) { btn.disabled = true; btn.textContent = "Running scans..."; }
    try {
      const out = await api.post("/api/challenger/run");
      if (out.ok && out.data && out.data.comparison) {
        renderChallengerPanel(panel, { available: true, latest: out.data.comparison, win_rate: out.data.win_rate || {} });
      } else {
        if (panel) panel.innerHTML = `<div class="panel-error">${safeText((out.data && out.data.message) || out.error || "Challenger scan failed")}</div>`;
      }
    } catch (e) {
      if (panel) panel.innerHTML = `<div class="panel-error">Error: ${safeText(String(e))}</div>`;
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Run Challenger Scan"; }
    }
  });
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
  bindEvent("checkBtn", "click", quickCheck);
  bindEvent("reportBtn", "click", runReport);
  bindEvent("dossierBtn", "click", runResearchDossier);
  bindEvent("dossierDownloadJsonBtn", "click", () => downloadResearchDossier("json"));
  bindEvent("dossierDownloadMdBtn", "click", () => downloadResearchDossier("md"));
  bindEvent("dossierDownloadPdfBtn", "click", () => downloadResearchDossier("pdf"));
  bindEvent("secCompareBtn", "click", runSecCompare);
  bindEvent("secCompareMode", "change", applySecCompareMode);
  bindEvent("secCompareRuthlessMode", "change", () => {
    state.secRuthlessMode = Boolean(document.getElementById("secCompareRuthlessMode")?.checked);
    if (state.secCompareResult) renderSecCompareVisual(state.secCompareResult);
  });
  document.querySelectorAll("#secComparePresetButtons button[data-a]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const node = e.currentTarget;
      const mode = node.getAttribute("data-mode") || "ticker_vs_ticker";
      const a = node.getAttribute("data-a") || "";
      const b = node.getAttribute("data-b") || "";
      document.getElementById("secCompareMode").value = mode;
      document.getElementById("secCompareTickerA").value = a;
      document.getElementById("secCompareTickerB").value = b;
      applySecCompareMode();
      updateActionCenter({
        title: "Preset Loaded",
        message: `${a}${b ? ` vs ${b}` : " over time"} template loaded. Click Run SEC Compare.`,
        severity: "info",
      });
    });
  });
  bindEvent("toggleReportViewBtn", "click", () => {
    state.reportRawView = !state.reportRawView;
    applyReportViewMode();
  });
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
  safeInit("applyDisplayMode", () => applyDisplayMode(getDisplayMode()));
  safeInit("applyScreenMode", () => applyScreenMode(getScreenModeFromUrl(), { updateUrl: true }));
  safeInit("applyReportViewMode", applyReportViewMode);
  safeInit("applySecCompareMode", applySecCompareMode);
  await safeInit("loadConfig", loadConfig);
  if (state.sseEnabled) safeInit("connectSSE", connectSSE);
  await authSessionReady;
  const token = await getApiAccessToken();
  if (token) {
    scheduleRetainedSessionTracking();
    await safeInit("refreshCritical", refreshCritical);
    safeInit("markDeferredDataPlaceholders", markDeferredDataPlaceholders);
    safeInit("setupLazySectionLoading", setupLazySectionLoading);
  } else if (state.config?.auth_mode === "supabase") {
    updateActionCenter({
      title: "Sign in",
      message: "Sign in with Supabase to load portfolio, pending trades, and billing-protected actions.",
      severity: "warn",
    });
    safeInit("setupLazySectionLoading", setupLazySectionLoading);
  } else {
    await safeInit("refreshAll", refreshAll);
    safeInit("setupLazySectionLoading", setupLazySectionLoading);
  }
  safeInit("installRouter", installRouter);
  safeInit("updateActivityBadge", updateActivityBadge);
  logEvent({ kind: "system", severity: "info", message: "Dashboard loaded." });
})();

