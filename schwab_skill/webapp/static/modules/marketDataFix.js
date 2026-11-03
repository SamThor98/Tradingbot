/**
 * Schwab market-data panel copy.
 *
 * The live quote probe and the last scan's data_quality flag are different
 * facts. This module says which one failed and the concrete next step.
 * It does not fetch.
 */

import { formatDataQualityReason } from "./filterReasons.js";

function text(value) {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

function count(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function formatCount(n) {
  return n.toLocaleString("en-US");
}

function formatHourLimit(n) {
  if (!Number.isFinite(n)) return "";
  const rounded = Math.round(n * 10) / 10;
  return Math.abs(rounded - Math.round(rounded)) < 0.05 ? String(Math.round(rounded)) : String(rounded);
}

/**
 * @param {string} code
 * @returns {{ kind: "stale", age: number, limit: number } | { kind: "empty" } | null}
 */
export function parseSecCacheReason(code) {
  const raw = text(code);
  const stale = /sec_cache_stale_age_h_([0-9.]+)_gt_([0-9.]+)/i.exec(raw);
  if (stale) {
    const age = Number(stale[1]);
    const limit = Number(stale[2]);
    if (Number.isFinite(age) && Number.isFinite(limit)) return { kind: "stale", age, limit };
  }
  if (/sec_cache_empty|sec_cache_missing/i.test(raw)) return { kind: "empty" };
  if (/sec_cache/i.test(raw)) return { kind: "empty" };
  return null;
}

function secStaleSentence(age, limit) {
  const limitLabel = formatHourLimit(limit);
  const over = age - limit;
  if (!Number.isFinite(over) || over <= 0) {
    return `The SEC filing cache is past the ${limitLabel}-hour limit.`;
  }
  if (over < 1) {
    const mins = Math.max(1, Math.round(over * 60));
    const unit = mins === 1 ? "minute" : "minutes";
    return `The SEC filing cache is ${mins} ${unit} past the ${limitLabel}-hour limit.`;
  }
  const hours = over >= 10 ? String(Math.round(over)) : String(Math.round(over * 10) / 10);
  const unit = hours === "1" ? "hour" : "hours";
  return `The SEC filing cache is ${hours} ${unit} past the ${limitLabel}-hour limit.`;
}

function reasonList(diag) {
  const raw = Array.isArray(diag?.data_quality_reasons) ? diag.data_quality_reasons : [];
  return raw.map(text).filter(Boolean);
}

function schwabScanProof(diag) {
  const primary = count(diag?.data_provider_primary_count);
  const fallback = count(diag?.data_provider_fallback_count);
  const regime = text(diag?.regime_history_provider).toLowerCase();
  const sentences = [];
  if (primary !== null || fallback !== null) {
    const priced = primary === null ? "an uncounted set of" : formatCount(primary);
    const fell = fallback === null ? "an uncounted set of" : formatCount(fallback);
    sentences.push(
      `The last scan priced ${priced} names with Schwab and ${fell} with the yfinance fallback.`,
    );
  }
  if (regime === "schwab") sentences.push("Regime history came from Schwab.");
  else if (regime) sentences.push(`Regime history came from ${regime}.`);
  return sentences.join(" ");
}

const REFRESH_STEP = {
  text: "In the schwab_skill folder, run ",
  command: "python scripts/refresh_sec_cache.py",
  tail: ". That refreshes SEC filings. It does not change the Schwab login.",
};

const RESCAN_STEPS = [
  "Run a new scan. This card reads the last scan, so a refresh alone leaves the flag up.",
  "Return to System. The card clears when that scan reports data quality ok.",
];

function secCard({ quotesLive, proof, problem }) {
  const liveBit = quotesLive
    ? "Quotes are live. The scan flag is the SEC cache, not a disconnect."
    : "The degraded flag on the last scan is the SEC filing cache, not a missing Schwab login.";
  const why = [proof, problem, "Reconnecting Schwab will not clear this flag."].filter(Boolean).join(" ");
  return {
    state: "fix",
    chip: { label: "SEC cache stale", tone: "warn", title: problem },
    blocker: `${problem} Refresh that cache, then scan again.`,
    card: {
      decision: "fix",
      eyebrow: "Schwab market data",
      title: quotesLive ? "Schwab market data is live." : "Schwab is not the thing to reconnect.",
      status: liveBit,
      marketState: quotesLive ? "live" : "scan",
      why,
      steps: [REFRESH_STEP, ...RESCAN_STEPS],
      ctaLabel: "Run a new scan",
      ctaHref: "#scanSection",
      ctaLane: "",
      ctaPrimary: true,
    },
  };
}

function quoteDownCard(quoteHint) {
  const hintRaw = text(quoteHint);
  const hint = hintRaw === "—" ? "" : hintRaw;
  const entitlement = /401|entitlement|market data production/i.test(hint);
  const why = hint
    ? hint
    : "The live quote check failed. Account link and market-data link are separate Schwab approvals.";
  const steps = [
    "Open Connect and press Connect Market Data. Connecting the account does not turn quotes on.",
  ];
  if (entitlement) {
    steps.push(
      "This failure is an entitlement, so signing in again will not fix it. In the Schwab developer portal, set Market Data Production to Ready For Use, then connect market data once.",
    );
  } else {
    steps.push("Press Refresh on this page. The quote line should change to live.");
  }
  steps.push("Run a new scan after the quote check says live.");
  return {
    state: "down",
    chip: null,
    blocker: "",
    card: {
      decision: "fix",
      eyebrow: "Schwab market data",
      title: "Schwab market data is not live.",
      status: "Quote check failed.",
      marketState: "down",
      why,
      steps,
      ctaLabel: "Connect market data",
      ctaHref: "#onboardingSection",
      ctaLane: "connect",
      ctaPrimary: true,
    },
  };
}

function disconnectedCard() {
  return {
    state: "down",
    chip: null,
    blocker: "",
    card: {
      decision: "fix",
      eyebrow: "Schwab market data",
      title: "Schwab market data is not connected.",
      status: "No usable Schwab session.",
      marketState: "down",
      why: "Live quotes need two Schwab approvals: the account link and the market-data link. Either one missing leaves the quote check failed.",
      steps: [
        "Open Connect.",
        "Press Connect Account if that step is still pending.",
        "Press Connect Market Data. Account access does not include quotes.",
        "Refresh this page. The card clears when the quote check says live.",
      ],
      ctaLabel: "Connect market data",
      ctaHref: "#onboardingSection",
      ctaLane: "connect",
      ctaPrimary: true,
    },
  };
}

function genericScanCard({ dq, reasons, quotesLive, proof }) {
  const labels = reasons.map((code) => formatDataQualityReason(code)).filter(Boolean);
  const problem = labels.length
    ? `Last scan data quality is ${dq}: ${labels.join("; ")}.`
    : `Last scan data quality is ${dq}.`;
  const title = quotesLive ? "Schwab quotes are live. The last scan is not." : "Last scan data is not clear to stage.";
  const steps = [];
  if (reasons.some((code) => /provider_fallback|fallback/i.test(code))) {
    steps.push("Open Connect and press Connect Market Data, then run a new scan.");
  } else if (reasons.some((code) => /quote_|bars_/i.test(code))) {
    steps.push("Run a new scan while the market is open. Weekend and holiday bars often clear on the next session.");
    steps.push("If the quote line on this page says degraded, press Connect Market Data before that scan.");
  } else {
    steps.push("Clear the cause above, then run a new scan. Reconnecting Schwab only helps when the quote check itself has failed.");
  }
  return {
    state: "fix",
    chip: null,
    blocker: problem,
    card: {
      decision: "fix",
      eyebrow: "Schwab market data",
      title,
      status: quotesLive ? "Quotes are live. The flag is on the last scan." : "See the cause below before staging.",
      marketState: quotesLive ? "live" : "scan",
      why: [proof, problem].filter(Boolean).join(" "),
      steps,
      ctaLabel: "Run a new scan",
      ctaHref: "#scanSection",
      ctaLane: "",
      ctaPrimary: true,
    },
  };
}

/**
 * @param {{ authState?: string, quoteOk?: boolean|null, quoteHint?: string, marketTokenOk?: boolean|null, diag?: object }} input
 */
export function buildMarketDataFix(input = {}) {
  const auth = text(input.authState).toLowerCase();
  const quoteOk = input.quoteOk;
  const quotesLive = quoteOk === true;
  const diag = input.diag && typeof input.diag === "object" ? input.diag : {};
  const dq = text(diag.data_quality).toLowerCase();
  const reasons = reasonList(diag);
  const proof = schwabScanProof(diag);

  if (auth === "disconnected" || (input.marketTokenOk === false && quoteOk !== true)) {
    return disconnectedCard();
  }
  if (quoteOk === false) {
    return quoteDownCard(input.quoteHint);
  }

  const sec = reasons.map(parseSecCacheReason).find(Boolean) || null;
  const onlySec = reasons.length > 0 && reasons.every((code) => parseSecCacheReason(code));
  if (dq && dq !== "ok" && sec && onlySec) {
    const problem =
      sec.kind === "stale"
        ? secStaleSentence(sec.age, sec.limit)
        : "The SEC filing cache is empty.";
    return secCard({ quotesLive, proof, problem });
  }

  if (dq && !["ok", "fresh", "healthy", "good"].includes(dq)) {
    return genericScanCard({ dq, reasons, quotesLive, proof });
  }

  return { state: "ok", chip: null, blocker: "", card: null };
}

/**
 * One-line blocker for the last scan's data_quality flag.
 * Pass scan diagnostics only — the live quote probe has its own blocker.
 * @param {object|null|undefined} diag
 * @returns {string}
 */
export function scanQualityBlocker(diag) {
  const view = buildMarketDataFix({ diag, authState: "connected", quoteOk: true });
  return view.state === "fix" ? view.blocker : "";
}

/**
 * Chip that replaces the generic "Data degraded" pill when the cause is the SEC cache.
 * @param {object|null|undefined} diag
 */
export function dataQualityChip(diag) {
  const view = buildMarketDataFix({ diag, authState: "connected", quoteOk: true });
  return view.chip || null;
}

const HIGHER_PRIORITY_BLOCKER = /kill switch|trading pause|api error rate|deep health/i;

/**
 * Market-data card wins unless a higher-priority blocker is first.
 * @param {string[]} blockers
 * @param {{ state?: string, card?: object|null }} market
 */
export function marketDataCardOrNull(blockers, market) {
  if (!market || (market.state !== "down" && market.state !== "fix") || !market.card) return null;
  const first = Array.isArray(blockers) && blockers.length ? text(blockers[0]) : "";
  if (first && HIGHER_PRIORITY_BLOCKER.test(first)) return null;
  return market.card;
}
