/**
 * Health ribbon rendering (Diagnostics screen) — badge/tile states, the
 * plain-language summary line, and the action-center escalation derived
 * from broker/market/validation health.
 *
 * Extracted from app.js per the module decomposition policy in
 * docs/FRONTEND_DESIGN_SYSTEM.md ("Next Planned Splits").
 */

import { safeText, safeNum } from "../modules/format.js";
import {
  applyFreshness,
  markUnavailable,
  clearUnavailable,
} from "../modules/freshness.js";
import { updateActionCenter } from "../modules/logger.js";
import { setSystemStatusStrip } from "../modules/systemStatus.js";

export function setHealthRibbonUnavailable(reason) {
  const rawReason = safeText(reason || "").trim();
  const lower = rawReason.toLowerCase();
  let uiReason = rawReason;
  if (
    lower.includes("missing authentication") ||
    lower.includes("authorization: bearer") ||
    lower.includes("auth session cookie")
  ) {
    uiReason = "Verify your email session, then connect Schwab to unlock live health checks.";
  } else if (lower.includes("expired") && lower.includes("token")) {
    uiReason = "Session expired. Sign in again to restore health checks.";
  }
  if (!uiReason) uiReason = "status fetch failed";

  const ribbon = document.getElementById("healthRibbon");
  if (ribbon) ribbon.setAttribute("data-async-state", "error");
  setSystemStatusStrip(
    "healthStatusStrip",
    "error",
    "Health check unavailable.",
    uiReason,
  );
  ["ribbonAuth", "ribbonQuotes", "ribbonApiErrorRate", "ribbonValidation"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = "health-badge bg-slate-900";
    el.textContent = "Unknown";
    markUnavailable(el, uiReason);
  });
  ["healthTileAuth", "healthTileQuotes", "healthTileApi", "healthTileValidation"].forEach((id) => {
    const tile = document.getElementById(id);
    if (!tile) return;
    tile.dataset.state = "unknown";
    tile.style.setProperty("--gauge", "0");
  });
  ["ribbonAuthFresh", "ribbonQuotesFresh", "ribbonApiErrorRateFresh", "ribbonValidationFresh"].forEach((id) => {
    applyFreshness(document.getElementById(id), {
      asOf: null,
      source: "/api/status",
      surface: "health_ribbon",
      unavailable: `unavailable: ${uiReason}`,
    });
  });
}

export function setHealthRibbonTiles(authState, quoteOk, errRate, validation) {
  const setTile = (id, stateName, gauge) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.dataset.state = stateName;
    el.style.setProperty("--gauge", String(gauge));
  };
  // authState may be a tri-state string ("connected"/"unverified"/"disconnected")
  // or a legacy boolean. Map to the tile's good/warn/bad states.
  const authTileState =
    authState === "connected" || authState === true
      ? "good"
      : authState === "unverified"
        ? "warn"
        : "bad";
  const authGauge = authTileState === "good" ? 1 : authTileState === "warn" ? 0.55 : 0;
  setTile("healthTileAuth", authTileState, authGauge);
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

// Plain-language one-liner rolling up the broker, market data, and scan state.
// Keeps the diagnostics page understandable at a glance without reading tiles.
export function renderHealthRibbonSummary({ authState, quoteOk, deepReachable, lastScan }) {
  const el = document.getElementById("healthRibbonSummary");
  const compact = document.getElementById("systemStatusCompact");
  const broker =
    authState === "connected"
      ? "Broker connected"
      : authState === "unverified"
        ? "Broker verifying"
        : "Broker disconnected";
  const market = !deepReachable ? "market data unknown" : quoteOk ? "market data live" : "market data degraded";
  let scan = "no scan yet this session";
  const scanAt = lastScan?.at;
  if (scanAt) {
    const ts = new Date(scanAt).getTime();
    if (Number.isFinite(ts)) {
      const mins = Math.max(0, Math.round((Date.now() - ts) / 60000));
      scan = mins < 1 ? "last scan just now" : mins < 60 ? `last scan ${mins}m ago` : `last scan ${Math.round(mins / 60)}h ago`;
    }
  }
  const line = `System status: ${broker}, ${market}, ${scan}.`;
  const state =
    authState === "connected" && quoteOk
      ? "success"
      : authState === "disconnected" || (!deepReachable && !quoteOk)
        ? "error"
        : "partial";
  setSystemStatusStrip("healthStatusStrip", state, "System health summary ready.", line);
  if (el) {
    clearUnavailable(el);
    el.textContent = line;
  }
  if (compact) {
    compact.textContent = line;
    compact.classList.remove("hidden");
  }
}

export function prioritizeActionCenterFromHealth({ authState, quoteOk, errRate, validation, topBlocker, quoteHealth }) {
  const runStatus = safeText(validation?.run_status || "").toLowerCase();
  const blocker = safeText(topBlocker || "").trim();
  if (authState === "disconnected") {
    updateActionCenter({
      title: "P0: Broker Authentication Blocked",
      message: "Reconnect Schwab account and market sessions before running scans or approving orders.",
      severity: "error",
    });
    return;
  }
  if (authState === "unverified") {
    updateActionCenter({
      title: "P1: Broker Connection Unverified",
      message:
        "Schwab tokens are saved but the live API hasn't confirmed a response yet. Run a quick health check, and reconnect if this persists.",
      severity: "warn",
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
