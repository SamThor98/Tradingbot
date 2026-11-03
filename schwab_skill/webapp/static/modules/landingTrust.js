/**
 * Shared trust chips for the tab landing frame.
 *
 * Every screen's chips must answer three operator questions:
 *   1. What needs action?
 *   2. Can I trust this screen's data?
 *   3. Is anything blocking work?
 *
 * Labels stay short; source / as-of / budget live in `title` for hover.
 * Tones: healthy | attention | blocked | neutral.
 */

import { safeText, safeNum, formatCount } from "./format.js";
import { humanizeApiSource, humanizeKey } from "./humanize.js";
import { formatDataQualityReason } from "./filterReasons.js";
import { getPortfolioSource } from "../panels/portfolioManual.js";

const VALID_TONES = new Set(["healthy", "attention", "blocked", "neutral"]);

/** Empty-safe string. `safeText(null)` is "—", which must not count as a ticker. */
function raw(value) {
  if (value === null || value === undefined) return "";
  const s = String(value).trim();
  if (!s || s === "—" || s === "-" || s === "null" || s === "undefined") return "";
  return s;
}

export function dataQualityTone(dq) {
  const v = raw(dq).toLowerCase();
  if (["ok", "fresh", "healthy", "good"].includes(v)) return "healthy";
  if (["stale", "conflict", "blocked", "failed", "fail"].includes(v)) return "blocked";
  if (["degraded", "partial", "unknown", "warning", "warn"].includes(v)) return "attention";
  return "neutral";
}

/** Ordinary Stage A/B filters. They stay on the scan funnel, not the P2 banner. */
const SCAN_FUNNEL_BANNER_KEYS = new Set([
  "stage2_fail",
  "vcp_fail",
  "breakout_not_confirmed",
  "pead_primary_fail",
  "quality_gates_filtered",
  "pts_52w_cap_blocked",
  "entry_timing_blocked",
  "forensic_filtered",
]);

/** Missing or thin history. A handful of names is normal on a 1,500-name book. */
const PRICE_HISTORY_GAPS = {
  df_empty: "had no price history",
  too_few_candles: "had too little price history",
  bars_stale: "had stale price bars",
};

const HARD_SCAN_FAILURES = new Set([
  "exceptions",
  "stage_a_timeouts",
  "stage_a_task_exceptions",
  "stage_b_exceptions",
  "stage_b_timeouts",
]);

const MATERIAL_GAP_SHARE = 0.05;

function dataQualityHealthy(dq) {
  const v = raw(dq).toLowerCase();
  return !v || ["ok", "fresh", "healthy", "good"].includes(v);
}

/**
 * Action-center copy for the last scan.
 * Returns null when the diagnostic is funnel noise on a finished scan.
 * A few empty price frames must not read as "the scan was blocked."
 */
export function selectScanBlockerBanner(input = {}) {
  const blockers = Array.isArray(input.blockers) ? input.blockers : [];
  const headline = raw(input.headline);
  const scanBlocked = Boolean(input.scanBlocked);
  const signals = safeNum(input.signalsFound, 0);
  const watch = safeNum(input.watchlistSize, 0);
  const dataOk = dataQualityHealthy(input.dataQuality);

  if (scanBlocked) {
    return {
      title: "P2: Scan Blocked",
      message: headline || "The last scan was blocked before it finished.",
      severity: "warn",
    };
  }

  for (const row of blockers) {
    const key = raw(row?.key);
    if (!key || SCAN_FUNNEL_BANNER_KEYS.has(key) || /_ms$|scan_stage_/i.test(key)) continue;
    const count = safeNum(row?.value, 0);
    if (count <= 0) continue;

    if (Object.prototype.hasOwnProperty.call(PRICE_HISTORY_GAPS, key)) {
      const share = watch > 0 ? count / watch : count >= 25 ? 1 : 0;
      const material = !dataOk || share >= MATERIAL_GAP_SHARE;
      if (!material) continue;
      const noun = count === 1 ? "name" : "names";
      const scope = watch > 0 ? `${count} of ${watch}` : String(count);
      const finished =
        signals > 0
          ? ` The scan still finished with ${signals} signals.`
          : " The scan finished with no signals.";
      return {
        title: "P2: Price history gap",
        message: `${scope} ${noun} ${PRICE_HISTORY_GAPS[key]}.${finished}`,
        severity: "warn",
      };
    }

    if (HARD_SCAN_FAILURES.has(key)) {
      const label = humanizeKey(key);
      const finished =
        signals > 0
          ? ` The scan still finished with ${signals} signals.`
          : " The scan finished with no signals.";
      return {
        title: "P2: Scan Blocker Identified",
        message: `${label}: ${count}.${finished}`,
        severity: row?.severity === "error" ? "error" : "warn",
      };
    }
  }
  return null;
}

export function scanQualityFromDiagnostics(diag = {}) {
  const dq = raw(diag?.data_quality).toLowerCase();
  const reasons = Array.isArray(diag?.data_quality_reasons)
    ? diag.data_quality_reasons.map((r) => safeText(r)).filter(Boolean)
    : [];
  const fallback = safeNum(diag?.data_provider_fallback_count, 0);
  const primary = safeNum(diag?.data_provider_primary_count, 0);
  return { dq, reasons, fallback, primary };
}

export function relativeAgeLabel(iso) {
  if (!iso) return "";
  const ts = Date.parse(iso);
  if (!Number.isFinite(ts)) return "";
  const mins = Math.max(0, Math.round((Date.now() - ts) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function chip(label, tone, title = "") {
  const t = VALID_TONES.has(tone) ? tone : "neutral";
  return { label: safeText(label), tone: t, title: safeText(title) };
}

function sourceTitle(path, extra = "") {
  const label = humanizeApiSource(path);
  const tail = extra ? ` — ${extra}` : "";
  return label ? `Source: ${label}${tail}` : extra;
}

function authChip(health = {}) {
  const auth = raw(health.authState).toLowerCase();
  if (health.probeFailed) {
    return chip(
      "Health check failed",
      "blocked",
      health.probeError || "Connection status did not load. Press Refresh.",
    );
  }
  if (auth === "connected") {
    return chip("Schwab connected", "healthy", sourceTitle("/api/status", "plus quote health"));
  }
  if (auth === "disconnected") {
    return chip("Schwab disconnected", "blocked", "No usable Schwab session — reconnect before staging.");
  }
  if (auth === "unverified") {
    return chip("Schwab verifying", "attention", "Tokens saved but live API has not confirmed yet.");
  }
  return chip("Checking connection", "neutral", "Waiting for first health probe.");
}

function operationsChips(ctx) {
  const pending = ctx.pending;
  const pendingError = raw(ctx.pendingError);
  const hasPending = pending !== null && pending > 0;
  const diag = scanQualityFromDiagnostics(ctx.lastScanDiagnostics || {});
  const scanAt = ctx.lastScanAt;
  const age = relativeAgeLabel(scanAt);

  const pendingChip = pendingError
    ? chip("Queue unavailable", "blocked", pendingError)
    : pending === null
      ? chip("Loading queue", "neutral", sourceTitle("/api/pending-trades", "waiting for first load"))
      : hasPending
        ? chip(
            `${formatCount(pending)} pending`,
            "attention",
            sourceTitle("/api/pending-trades", "approve or reject on Today"),
          )
        : chip("Queue clear", "healthy", sourceTitle("/api/pending-trades", "nothing staged"));

  let scanChip;
  if (!scanAt) {
    scanChip = chip("No scan yet", "neutral", sourceTitle("/api/scan", "run a scan to load candidates"));
  } else if (diag.dq) {
    const fallbackBit = diag.fallback > 0 ? ` · ${diag.fallback} fallback bars` : "";
    let reasonBit = "";
    if (diag.reasons.length) {
      const rawReason = diag.reasons[0];
      const english = formatDataQualityReason(rawReason);
      reasonBit =
        english && english !== rawReason ? ` · ${english} (${rawReason})` : ` · ${english || rawReason}`;
    }
    scanChip = chip(
      `Scan · ${diag.dq}`,
      dataQualityTone(diag.dq),
      `Source: ${humanizeApiSource("/api/scan")} · ${age} · data quality ${diag.dq}${fallbackBit}${reasonBit}`,
    );
  } else {
    scanChip = chip(`Last scan ${age}`, "neutral", sourceTitle("/api/scan", "data quality not reported"));
  }

  return [pendingChip, scanChip, authChip(ctx.health)];
}

function researchChips(ctx) {
  const pdata = ctx.portfolio;
  const source = ctx.portfolioSource || getPortfolioSource();
  const sourceLabel = source === "manual" ? "Manual book" : "Schwab book";
  let positionCount = null;
  if (pdata && typeof pdata.positions_count === "number") {
    positionCount = pdata.positions_count;
  } else if (Array.isArray(pdata?.positions)) {
    positionCount = pdata.positions.length;
  }

  const bookChip =
    positionCount === null
      ? chip("Book not loaded", "neutral", "Open Portfolio to fetch positions from Schwab or the manual book")
      : positionCount === 0
        ? chip("No positions", "neutral", `${sourceLabel} · ${humanizeApiSource("/api/portfolio")}`)
        : chip(
            `${formatCount(positionCount)} positions`,
            "healthy",
            `${sourceLabel} · Source: ${humanizeApiSource("/api/portfolio")}`,
          );

  const sourceChip =
    positionCount === null
      ? chip(sourceLabel, "neutral", source === "manual" ? "Browser-local book" : `Waiting for ${humanizeApiSource("/api/portfolio")}`)
      : chip(sourceLabel, source === "manual" ? "attention" : "healthy", source === "manual"
          ? "Manual book lives in this browser only"
          : "Positions from Schwab account status");

  const ticker = raw(ctx.quickCheckTicker).toUpperCase();
  const qq = raw(ctx.quickCheckQuality).toLowerCase();
  let checkChip;
  if (!ticker) {
    checkChip = chip("No check yet", "neutral", "Quick check waits for a ticker — paste a symbol and press Check");
  } else if (qq && qq !== "ok") {
    checkChip = chip(
      `Checked ${ticker} · ${qq}`,
      dataQualityTone(qq),
      `Source: ${humanizeApiSource("/api/check")} · ${ticker} · data quality ${qq}`,
    );
  } else {
    checkChip = chip(`Checked ${ticker}`, "healthy", `Source: ${humanizeApiSource("/api/check")} · ${ticker}`);
  }

  return [bookChip, sourceChip, checkChip];
}

function intelChips(ctx) {
  const intel = ctx.intel || {};
  const notes = Array.isArray(intel.notes) ? intel.notes.filter(Boolean) : [];
  const summary = intel.summary || {};
  const err = raw(intel.error);
  const at = intel.at;
  const age = relativeAgeLabel(at);
  const cacheAge = Number.isFinite(intel.cacheAgeSec) ? intel.cacheAgeSec : null;
  const positions =
    typeof summary.positions === "number"
      ? summary.positions
      : typeof intel.positions === "number"
        ? intel.positions
        : null;

  if (err && positions === null && !at) {
    return [
      chip("Intel unavailable", "blocked", err),
      chip("Data unavailable", "blocked", err),
      chip("Not loaded", "neutral", sourceTitle("/api/position-intel")),
    ];
  }

  const posChip =
    positions === null
      ? chip("Intel not loaded", "neutral", sourceTitle("/api/position-intel"))
      : positions === 0
        ? chip("No positions", "neutral", "Link Schwab and hold equity, or look up a ticker")
        : chip(`${formatCount(positions)} scored`, "healthy", "Conviction tables for open equity positions");

  const dqChip = notes.length
    ? chip(
        `${notes.length} data note${notes.length === 1 ? "" : "s"}`,
        "attention",
        notes.slice(0, 3).join(" · "),
      )
    : positions === null
      ? chip("No intel yet", "neutral", "Look up a ticker or wait for the first compute")
      : chip("Data ok", "healthy", "No data_quality notes on this payload");

  let freshChip;
  if (!at) {
    freshChip = chip("Not loaded", "neutral", sourceTitle("/api/position-intel", "5 min cache"));
  } else if (cacheAge !== null && cacheAge > 300) {
    freshChip = chip("Cache stale", "attention", `cache ${Math.floor(cacheAge / 60)}m · budget 5 min · ${humanizeApiSource("/api/position-intel")}`);
  } else if (intel.cacheHit && cacheAge !== null) {
    const mins = Math.floor(cacheAge / 60);
    freshChip = chip(
      mins > 0 ? `Cache ${mins}m` : "Cache <1m",
      "healthy",
      `as of ${at} · ${humanizeApiSource("/api/position-intel")}`,
    );
  } else {
    freshChip = chip(age ? `Fresh ${age}` : "Fresh", "healthy", `as of ${at} · ${humanizeApiSource("/api/position-intel")}`);
  }

  return [posChip, dqChip, freshChip];
}

function systemChips(ctx) {
  const blockers = Array.isArray(ctx.blockers) ? ctx.blockers : [];
  const blocked = blockers.length > 0;
  const health = ctx.health || {};
  const quoteOk = health.quoteOk;
  const errRate = typeof health.errRate === "number" ? health.errRate : null;
  const healthSrc = humanizeApiSource("/api/health/deep");

  const auth = raw(health.authState).toLowerCase();
  const tradeChip = health.probeFailed
    ? chip("Health unknown", "blocked", health.probeError || "Health check did not load. Press Refresh.")
    : blocked
      ? chip("Cannot trade", "blocked", blockers[0] || "Fix blockers on System before staging")
      : auth === "connected" && quoteOk !== false
        ? chip("Can trade", "healthy", "No current System blockers")
        : auth === "disconnected"
          ? chip("Cannot trade", "blocked", "Schwab is disconnected — reconnect before staging")
          : chip("Checking safety", "neutral", "Waiting for health probes before a trade-ready verdict");

  const quoteChip = health.probeFailed
    ? chip("Quotes unknown", "blocked", "Quote probe did not run. Press Refresh.")
    : quoteOk === false
      ? chip("Quotes degraded", "attention", `Source: ${healthSrc} — fail closed for new risk`)
      : quoteOk === true
        ? chip("Quotes live", "healthy", `Source: ${healthSrc}`)
        : chip("Checking quotes", "neutral", "Waiting for quote health probe");

  let apiChip;
  if (health.probeFailed) {
    apiChip = chip("API unknown", "blocked", "Health check did not load. Press Refresh.");
  } else if (errRate === null) {
    apiChip = chip("Checking API", "neutral", `Waiting for ${healthSrc} metrics`);
  } else if (errRate >= 8) {
    apiChip = chip(`API ${errRate.toFixed(1)}%`, "blocked", `Server error rate from ${healthSrc}`);
  } else if (errRate >= 2) {
    apiChip = chip(`API ${errRate.toFixed(1)}%`, "attention", `Server error rate from ${healthSrc}`);
  } else {
    apiChip = chip(`API ${errRate.toFixed(1)}%`, "healthy", `Server error rate from ${healthSrc}`);
  }

  return [tradeChip, quoteChip, apiChip];
}

/**
 * @param {string} mode
 * @param {object} ctx
 * @returns {{subtitle:string,ctaLabel:string,ctaHref:string,altCtaLabel:string,altCtaHref:string,altCta2Label:string,altCta2Href:string,chips:Array<{label:string,tone:string,title?:string}>}}
 */
export function resolveLandingFrameState(mode, ctx = {}) {
  const cfg = ctx.cfg || {};
  const pending =
    ctx.pending === null || ctx.pending === undefined ? null : Number(ctx.pending) || 0;
  const bag = { ...ctx, pending };

  if (mode === "operations") {
    const hasPending = pending !== null && pending > 0;
    return {
      subtitle: cfg.subtitle || "Scan → review → approve",
      // Topbar Run Scan is the one primary. Landing CTA is Review pending
      // when the queue has work; otherwise a secondary table jump.
      ctaLabel: hasPending ? "Review pending" : "",
      ctaHref: hasPending ? "#pendingSection" : "#scanSection",
      altCtaLabel: "Open scan table",
      altCtaHref: "#scanSection",
      altCta2Label: "",
      altCta2Href: "",
      chips: operationsChips(bag),
    };
  }

  if (mode === "research") {
    return {
      subtitle: cfg.subtitle || "Portfolio and diligence",
      ctaLabel: "Open Portfolio",
      ctaHref: "#portfolioSection",
      altCtaLabel: "Quick check",
      altCtaHref: "#quickCheckSection",
      altCta2Label: "Backtest",
      altCta2Href: "#backtestSummaryLanding",
      chips: researchChips(bag),
    };
  }

  if (mode === "backtest") {
    const running = Number(ctx.backtestRunning || 0);
    const queued = Number(ctx.backtestQueued || 0);
    const queueChip = running
      ? chip("Run in progress", "attention", "Local dashboard runs one backtest at a time")
      : queued
        ? chip(`${queued} queued`, "attention", "Wait for the current job, then the next starts")
        : chip("Queue idle", "healthy", "Ready to queue a historical replay");
    return {
      subtitle: cfg.subtitle || "Results, queue, next steps",
      ctaLabel: cfg.ctaLabel || "Queue a backtest",
      ctaHref: cfg.ctaHref || "#backtestSection",
      altCtaLabel: cfg.altCtaLabel || "Recent runs",
      altCtaHref: cfg.altCtaHref || "#backtestQueuePanel",
      altCta2Label: cfg.altCta2Label || "Related evidence",
      altCta2Href: cfg.altCta2Href || "#backtestRelatedPanel",
      chips: [queueChip, chip("One at a time", "neutral", "This computer runs one backtest at a time"), authChip(ctx.health)],
    };
  }

  if (mode === "intel") {
    return {
      subtitle: cfg.subtitle || "Conviction, volatility, options",
      ctaLabel: "Look up ticker",
      ctaHref: "#intelTickerInput",
      altCtaLabel: "Open tables",
      altCtaHref: "#positionIntelSection",
      altCta2Label: "",
      altCta2Href: "",
      chips: intelChips(bag),
    };
  }

  if (mode === "diagnostics") {
    const blockers = Array.isArray(ctx.blockers) ? ctx.blockers : [];
    const blocked = blockers.length > 0;
    return {
      subtitle: cfg.subtitle || "Health first, then connect",
      ctaLabel: blocked ? "View blockers" : "Health summary",
      ctaHref: blocked ? "#systemZoneBlockers" : "#systemSummaryLanding",
      altCtaLabel: "Connect Schwab",
      altCtaHref: "#onboardingSection",
      altCta2Label: "",
      altCta2Href: "",
      chips: systemChips({ ...bag, blockers }),
    };
  }

  return {
    subtitle: cfg.subtitle || "",
    ctaLabel: cfg.ctaLabel || "",
    ctaHref: cfg.ctaHref || "#",
    altCtaLabel: cfg.altCtaLabel || "",
    altCtaHref: cfg.altCtaHref || "#",
    altCta2Label: cfg.altCta2Label || "",
    altCta2Href: cfg.altCta2Href || "",
    chips: [],
  };
}
