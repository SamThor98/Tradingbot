/**
 * The selected-name headline: last price, one stop that names its rule,
 * and size from the decision card. Missing size stays "Size unavailable."
 * This module does not invent a share count.
 */

import { advisoryStop } from "./ticketFacts.js";

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function money(value) {
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function tradeHeadlineLines({ ticker, price, sma200, qty, sizeUsd } = {}) {
  const sym = String(ticker || "").trim();
  if (!sym) {
    return { empty: true, entry: "Select a name from the list.", stop: "", size: "" };
  }
  const px = num(price);
  const sma = num(sma200);
  const hasPx = px !== null && px > 0;
  const hasSma = sma !== null && sma > 0;
  const stop = advisoryStop(hasPx ? px : null, hasSma ? sma : null);
  const entry = hasPx ? `Entry ${px.toFixed(2)}` : "Entry unavailable.";
  let stopLine = "Stop unavailable.";
  if (stop !== null && hasSma) stopLine = `Stop ${stop.toFixed(2)} — 3% under the 200-day`;
  else if (stop !== null && hasPx) stopLine = `Stop ${stop.toFixed(2)} — 7% under price, 200-day missing`;
  const shares = num(qty);
  const usd = num(sizeUsd);
  const size = shares !== null && shares > 0 && usd !== null && usd > 0
    ? `${Math.round(shares)} shares · ${money(usd)}`
    : "Size unavailable.";
  return { empty: false, entry, stop: stopLine, size };
}
