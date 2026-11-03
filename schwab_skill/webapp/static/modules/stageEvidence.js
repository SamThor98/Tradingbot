/**
 * Evidence that must sit on the Stage control.
 * A row can be executable and still be a bad queue: a sub-1 analog book
 * or a listing-rule 8-K.
 */

import { escapeHtml, safeText } from "./format.js";
import { advisoryStop } from "./ticketFacts.js";
import { isScanSignalStageable } from "./signalProvenance.js";
import { getAnalogN, getAnalogNeighborhoodPf, getAnalogThin } from "./signalScores.js";

const LISTING_RULE = /delist|listing rule|item 3\.01|\b3\.01\b/i;

function finite(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** 7% advisory band and the stop just under the 200-day average. */
export function queueStops(sig = {}) {
  const price = finite(sig.price ?? sig.current_price);
  const sma200 = finite(sig.sma_200);
  const band = price !== null && price > 0 ? Math.round(price * 0.93 * 100) / 100 : null;
  const far = sma200 !== null && sma200 > 0 ? Math.round(sma200 * 0.97 * 100) / 100 : null;
  return { band, far };
}

function money(value) {
  if (value === null) return "—";
  return `$${value.toFixed(2)}`;
}

/** Human reasons this executable row should not be queued. */
export function stageHoldReasons(sig = {}) {
  const reasons = [];
  const analog = getAnalogNeighborhoodPf(sig);
  if (analog !== null && analog < 1) {
    const n = getAnalogN(sig);
    const thin = getAnalogThin(sig) ? ", thin sample" : "";
    const nBit = n !== null ? `, n=${n}` : "";
    reasons.push(`analog profit factor ${analog.toFixed(2)}${thin}${nBit}`);
  }
  const secBits = [
    ...(Array.isArray(sig.sec_risk_reasons) ? sig.sec_risk_reasons : []),
    safeText(sig.sec_risk_tag || ""),
  ];
  if (LISTING_RULE.test(secBits.join(" "))) {
    reasons.push("8-K listing-rule notice");
  }
  return reasons;
}

/** Executable and free of the hold reasons above. */
export function clearToQueue(sig = {}) {
  return isScanSignalStageable(sig) && stageHoldReasons(sig).length === 0;
}

function stopTile(sig) {
  const price = finite(sig.price ?? sig.current_price);
  const sma200 = finite(sig.sma_200);
  const hasSma = sma200 !== null && sma200 > 0;
  const stop = advisoryStop(price, hasSma ? sma200 : null);
  if (stop === null) return tile("Stop", "—", "warn", "not on this row");
  if (hasSma) return tile("Stop", money(stop), "", "3% under the 200-day");
  return tile("Stop", money(stop), "", "7% under price, 200-day missing");
}

function tile(label, value, tone, sub) {
  const cls = tone ? ` decision-gate-tile--${tone}` : "";
  return `<div class="decision-gate-tile${cls}"><span class="decision-gate-label">${escapeHtml(label)}</span><strong class="decision-gate-value mono-nums">${escapeHtml(value)}</strong>${sub ? `<small class="decision-gate-sub muted">${escapeHtml(sub)}</small>` : ""}</div>`;
}

export function renderStageEvidenceHtml(sig = {}) {
  const holds = stageHoldReasons(sig);
  const stageable = isScanSignalStageable(sig);
  const analog = getAnalogNeighborhoodPf(sig);
  const headline = !stageable ? "Not the live book" : holds.length ? "Do not queue" : "Clear to queue";
  const analogValue = analog === null ? "—" : analog.toFixed(2);
  const analogSub = getAnalogThin(sig) ? "thin sample" : "neighborhood";
  const filingHold = holds.some((reason) => reason.includes("listing-rule"));
  const message = !stageable
    ? "Research sleeve or filtered row. Open Research or Intel."
    : holds.length
      ? holds.join(" · ")
      : "Analog and filings are clear.";
  return `
    <section class="scan-evidence" aria-label="Queue evidence">
      <p class="decision-gate-message"><strong>${escapeHtml(headline)}</strong></p>
      <div class="decision-gate-tiles">
        ${tile("Analog profit factor", analogValue, analog !== null && analog < 1 ? "bad" : analog === null ? "warn" : "good", analogSub)}
        ${tile("Filing", filingHold ? "Listing rule" : "Clear", filingHold ? "bad" : "good")}
        ${stopTile(sig)}
      </div>
      <p class="decision-gate-message">${escapeHtml(message)}</p>
    </section>
  `;
}
