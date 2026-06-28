/**
 * Central UI state singleton plus the localStorage key constants used by
 * the rest of the dashboard modules.
 *
 * The `state` object is intentionally a single mutable singleton — every
 * panel reads and writes the same instance. Keep it shallow and JSON-ish so
 * a future migration to a proper store stays tractable.
 */

export const state = {
  latestSignals: [],
  /** Full Stage-B shortlist from the most recent scan, each signal tagged
   *  with `_filter_status` describing whether it was kept or filtered (and
   *  why). Populated from /api/scan responses when the backend supplies
   *  `shortlist_signals`. The candidate table prefers this over
   *  `latestSignals` so operators can see filtered candidates with their
   *  disposition; trade-staging still uses the kept subset only. */
  latestShortlistSignals: [],
  /** Last watchlist size from scan diagnostics. Defaults to SP1500 (the
   *  canonical universe) so the hero KPI is meaningful before the first
   *  scan reports diagnostics. Real per-scan values overwrite this in
   *  `renderDiagnostics`; an explicit ticker override will show the
   *  smaller count. */
  lastWatchlistSize: 1500,
  /** ISO timestamp of the most recent scan response that populated
   *  `latestSignals` / `lastWatchlistSize`. Used for freshness labels. */
  lastScanAt: null,
  /** Most recent integer pending-trades count from /api/pending-trades.
   *  `null` means we have not loaded yet — render "—". */
  lastPendingCount: null,
  /** ISO timestamp from the most recent successful pending-trades fetch. */
  lastPendingAt: null,
  /** ISO timestamp of the last health/status response (for ribbon freshness). */
  lastStatusAt: null,
  /** ISO timestamp of the last decision-dashboard response. */
  lastDecisionDashboardAt: null,
  /** P0 entry-timing experiment scan preflight (decision dashboard / scan-lifecycle). */
  entryTimingScanPreflight: null,
  approvingTradeId: null,
  approvingChecklist: null,
  /** Scan signal snapshot from the pending row being approved (for filter guard). */
  approvingScanSignal: null,
  pendingFilter: "pending",
  pendingSort: "newest",
  config: { auth_mode: "jwt" },
  allowManualJwt: true,
  publicConfig: {
    supabase: null,
    saas_mode: false,
    runtime_mode: "local",
    schwab_oauth: false,
    schwab_market_oauth: false,
    platform_live_trading_kill_switch: false,
  },
  runtimeContract: null,
  accountMe: null,
  twoFaStatus: null,
  reportRawView: false,
  lastReportData: null,
  lastResearchDossier: null,
  activeReportTab: "summary",
  secCompareResult: null,
  secManagementDashboard: null,
  secRuthlessMode: false,
  onboarding: null,
  profile: null,
  presetCatalog: null,
  savedUiSettings: null,
  performance: null,
  lastPortfolioData: null,
  lastPortfolioRiskData: null,
  calibration: null,
  strategyChatMessages: [],
  strategyChatBusy: false,
  backtestQueueBusy: false,
  lastQuoteHealthLogSig: null,
  queueScanDraft: null,
  selectedScanTicker: "",
  /** Optional scan body: strategy_overrides, universe_mode, tickers (see /api/scan). */
  scanRunOptions: null,
  /** UI-only expanded/collapsed state for Qualified breakouts table. */
  scanRowsExpanded: false,
  /** Active sort for the scan candidates table. `field` is one of the
   *  `data-sort-key` values declared on the scan table headers (e.g.
   *  "ticker", "score", "p_up_10d", "conviction"); `dir` is "asc" or
   *  "desc". `field=null` means use natural backend ordering. Sort state
   *  is intentionally session-scoped — it survives re-renders within the
   *  same session but is not persisted to localStorage. */
  scanSort: { field: null, dir: "desc" },
  /** Presentation mode for rank explainers in scan tables:
   *  - tooltip: compact score + "?" hover details
   *  - inline: score with short inline rationale text */
  scanRankExplainMode: "tooltip",
  sseEnabled: false,
  funnelMilestonesSent: {},
  retainedSessionSent: false,
};

/** localStorage keys used across modules. Centralised here to keep namespacing
 * consistent and to make grep/refactoring easier. */
export const UI_VIEW_MODE_KEY = "tradingbot.ui.view_mode";
export const AUTH_TOKEN_KEY = "tradingbot.jwt";
export const LEGACY_AUTH_TOKEN_KEYS = ["supabasetoken", "supabaseToken", "supabase_token"];
export const BACKTEST_PREFS_KEY = "tradingbot.backtest.preferences";
export const NOTIF_STORAGE_KEY = "tradingbot.notifications";
