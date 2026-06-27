/**
 * Plain-English labels for code-adjacent UI copy (snake_case keys, API paths, env names).
 */

import { safeText } from "./format.js";

const API_SOURCE_LABELS = {
  "/api/me": "account check",
  "/api/status": "connection status",
  "/api/health/deep": "quote health",
  "/api/decision-dashboard": "decision summary",
  "/api/pending-trades": "pending trades",
  "/api/scan": "last scan",
  "/api/auth/session": "sign-in status",
  "/api/ablation/status": "strategy test status",
};

const KEY_LABELS = {
  stage_2: "In uptrend",
  is_stage_2: "In uptrend",
  vcp: "Volatility pattern",
  vcp_detected: "Volatility pattern",
  signal_score: "Signal score",
  score: "Score",
  sector: "Sector",
  sector_etf: "Sector fund",
  price: "Price",
  current_price: "Price",
  last_price: "Price",
  ticker: "Ticker",
  title: "Title",
  description: "Description",
  timestamp: "Updated",
  schwab_auth: "Schwab login",
  market_data: "Market data",
  execution: "Order execution",
  skip_mirofish: "Skip AI sentiment",
  skip_edgar: "Skip SEC filing analysis",
  ticker_over_time: "One company over time",
  ticker_vs_ticker: "Compare two companies",
  auto_detect: "Auto-detect",
  early_growth: "Early growth",
  scaled_growth: "Scaled growth",
  mature_compounder: "Mature compounder",
  cyclical: "Cyclical",
  tech: "Technical analysis",
  dcf: "Valuation (DCF)",
  comps: "Peer comparison",
  health: "Financial health",
  edgar: "SEC filings",
  mirofish: "AI sentiment",
  metadata_fallback: "Filing summary only",
  full_text: "Full filing text",
  challenger_better: "New settings look better",
  champion_better: "Current settings look better",
  stage2_fail: "Did not pass uptrend check",
  stage_a_candidates: "Passed quick filter",
  SIGNAL_UNIVERSE_MODE: "Universe mode",
  TOP_N: "Rank limit",
  SIGNAL_TOP_N: "Rank limit",
};

const ENV_PARAM_LABELS = {
  SIGNAL_UNIVERSE_MODE: "Universe mode",
  SIGNAL_TOP_N: "Rank limit",
  QUALITY_GATES_MODE: "Quality filters",
  VCP_GATE_MODE: "Pattern gate",
  SECTOR_GATE_MODE: "Sector gate",
  REGIME_V2_MODE: "Market regime filter",
  EXIT_MANAGER_MODE: "Exit rules",
  CORRELATION_GUARD_MODE: "Correlation guard",
  PEAD_MODE: "Earnings drift filter",
};

const ROLLOUT_MODE_LABELS = {
  off: "Off",
  shadow: "Observe only",
  live: "Enforced",
};

const SCAN_STAGE_LABELS = {
  stage2: "Passed uptrend check",
  stage_a: "Passed quick filter",
};

/**
 * Turn snake_case / env keys into readable labels.
 * @param {string} key
 * @returns {string}
 */
export function humanizeKey(key) {
  const raw = safeText(key).trim();
  if (!raw) return "";
  if (KEY_LABELS[raw]) return KEY_LABELS[raw];
  if (ENV_PARAM_LABELS[raw]) return ENV_PARAM_LABELS[raw];
  return raw
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bApi\b/g, "API")
    .replace(/\bEtf\b/g, "ETF")
    .replace(/\bSec\b/g, "SEC")
    .replace(/\bDcf\b/g, "DCF");
}

/**
 * Friendly label for API endpoint provenance strings.
 * @param {string} source
 * @returns {string}
 */
export function humanizeApiSource(source) {
  const raw = safeText(source).trim();
  if (!raw) return "";
  if (API_SOURCE_LABELS[raw]) return API_SOURCE_LABELS[raw];
  if (raw.startsWith("/api/")) {
    const tail = raw.replace(/^\/api\//, "").replace(/\//g, " · ");
    return humanizeKey(tail.replace(/-/g, "_"));
  }
  return raw;
}

/**
 * Plugin rollout mode (off / shadow / live).
 * @param {string} mode
 * @returns {string}
 */
export function humanizeRolloutMode(mode) {
  const m = String(mode || "off").toLowerCase();
  return ROLLOUT_MODE_LABELS[m] || humanizeKey(m);
}

/**
 * Challenger / ablation verdict codes.
 * @param {string} verdict
 * @returns {string}
 */
export function humanizeVerdict(verdict) {
  const v = safeText(verdict).trim().toLowerCase();
  if (KEY_LABELS[v]) return KEY_LABELS[v];
  return humanizeKey(v);
}

/**
 * SEC compare / analysis mode ids.
 * @param {string} mode
 * @returns {string}
 */
export function humanizeAnalysisMode(mode) {
  const m = safeText(mode).trim();
  if (KEY_LABELS[m]) return KEY_LABELS[m];
  return humanizeKey(m);
}

/**
 * Scan funnel stage keys.
 * @param {string} key
 * @param {string} [fallbackLabel]
 * @returns {string}
 */
export function humanizeScanStageLabel(key, fallbackLabel = "") {
  const k = safeText(key).trim();
  if (SCAN_STAGE_LABELS[k]) return SCAN_STAGE_LABELS[k];
  if (fallbackLabel) return fallbackLabel;
  return humanizeKey(k);
}

/**
 * Known quick-check / signal field labels.
 * @param {string} key
 * @returns {string}
 */
export function humanizeFieldLabel(key) {
  return humanizeKey(key);
}

/**
 * Environment override key for performance / challenger panels.
 * @param {string} envKey
 * @returns {string}
 */
export function humanizeEnvParam(envKey) {
  const k = safeText(envKey).trim();
  return ENV_PARAM_LABELS[k] || humanizeKey(k);
}
