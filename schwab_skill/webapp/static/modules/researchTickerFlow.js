/**
 * Research ticker identity helpers.
 *
 * The Quick check / dossier / SEC rail shares one symbol. User input is the
 * *requested* ticker; last successful fetch is the *shown* ticker. Panels
 * must never label shown data as the requested symbol when they diverge.
 */

export const RESEARCH_TICKER_RAIL_IDS = Object.freeze([
  "tickerInput",
  "reportTickerInput",
  "secCompareTickerA",
  "intelTickerInput",
]);

export function getResearchRailTicker() {
  for (const id of RESEARCH_TICKER_RAIL_IDS) {
    const v = document.getElementById(id)?.value?.trim()?.toUpperCase() || "";
    if (v) return v;
  }
  return "";
}

export function payloadTicker(data, fallback = "") {
  return String(data?.ticker || fallback || "").trim().toUpperCase();
}

export function providerLabel(provider, usedFallback) {
  const p = String(provider || "").trim().toLowerCase();
  if (p === "yfinance" || usedFallback) return "yfinance fallback";
  if (p === "schwab") return "Schwab";
  return p || "market";
}

export function formatAsOf(asOf) {
  const s = String(asOf || "").trim();
  if (s.length >= 10 && s[4] === "-") return s.slice(0, 10);
  return "";
}

export function syncResearchTickerRail(ticker, { sourceId } = {}) {
  const sym = String(ticker || "").trim().toUpperCase();
  RESEARCH_TICKER_RAIL_IDS.forEach((id) => {
    if (id === sourceId) return;
    const el = document.getElementById(id);
    if (el && el.value !== sym) el.value = sym;
  });
  emitResearchTickerChange(sym, sourceId);
}

export function emitResearchTickerChange(ticker, sourceId) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("research_ticker_change", {
    detail: { ticker: String(ticker || "").trim().toUpperCase(), sourceId: sourceId || "" },
  }));
}
