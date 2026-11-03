/**
 * Event risk panel — prediction-market event probabilities mapped to the
 * active research ticker, and what they do to its modeled value.
 *
 * Loads `/api/event-probability/<ticker>` in parallel with quick check rather
 * than inside it, so a valuation build can never slow the fast symbol check.
 *
 * In-flight requests are keyed by `_gen` so a slower NVDA response cannot
 * overwrite a later AVGO load.
 *
 * This is a research signal only: it does not change scan scores, ranking,
 * position sizing, or advisory probabilities. That is the separate Stage B
 * prediction-market overlay.
 */

import { api } from "../modules/api.js";
import { escapeHtml, formatSignedPercentPoints, pct } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import { setAsyncState, ASYNC_ERROR, ASYNC_LOADING, ASYNC_EMPTY, ASYNC_SUCCESS } from "../modules/asyncState.js";

const SECTION_ID = "eventProbabilitySection";
const BODY_ID = "eventProbabilityBody";

let _gen = 0;
let _lastTicker = "";

function body() {
  return document.getElementById(BODY_ID);
}

function isCurrentGen(gen) {
  return gen === _gen;
}

function fmtMoney(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function fmtSignedMoney(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : "-"}$${Math.abs(n).toFixed(2)}`;
}

function fmtPct(value, digits = 2) {
  // percent points from event_probability/service.valuation_adjustment_pct / ems_gap_pct
  return formatSignedPercentPoints(value, digits);
}

function fmtProb(value) {
  // ratio 0–1 from event.adjusted_probability
  return pct(value, 1);
}

function fmtProbDelta(value) {
  // ratio delta (probability_change_7d) → percentage points
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const pp = n * 100;
  return `${pp >= 0 ? "+" : "-"}${Math.abs(pp).toFixed(1)}pp`;
}

function toneFor(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "good" : "bad";
}

function kpiTile(label, value, tone = "", sub = "") {
  const toneClass = tone ? ` event-risk-kpi--${tone}` : "";
  return `<div class="event-risk-kpi${toneClass}">
    <span class="event-risk-kpi-label">${escapeHtml(label)}</span>
    <strong class="event-risk-kpi-value">${escapeHtml(value)}</strong>
    ${sub ? `<span class="event-risk-kpi-sub muted">${escapeHtml(sub)}</span>` : ""}
  </div>`;
}

function noticeHtml(headline, detail) {
  return `<div class="event-risk-notice">
    <strong>${escapeHtml(headline)}</strong>
    ${detail ? `<span class="muted">${escapeHtml(detail)}</span>` : ""}
  </div>`;
}

function eventRowHtml(entry) {
  const ev = entry.event || {};
  const mapping = entry.mapping || {};
  const sens = entry.sensitivity || null;
  const change = ev.probability_change_7d;
  const impact = sens ? sens.modeled_valuation_impact : null;
  return `<tr>
    <td>
      <div class="event-risk-name">${escapeHtml(ev.event_name || ev.event_id || "—")}</div>
      <div class="muted event-risk-meta">
        ${escapeHtml(String(ev.event_category || "other").replace(/_/g, " "))}
        · ${escapeHtml(mapping.fundamental_variable || "—")}
        · materiality ${escapeHtml(Number(mapping.materiality ?? 0).toFixed(2))}
      </div>
    </td>
    <td class="event-risk-num">${escapeHtml(fmtProb(ev.adjusted_probability))}</td>
    <td class="event-risk-num">
      <span class="pill ${toneFor(change)} small">${escapeHtml(fmtProbDelta(change))}</span>
    </td>
    <td class="event-risk-num">${escapeHtml(sens ? fmtSignedMoney(sens.dollar_per_pp) : "—")}</td>
    <td class="event-risk-num">
      <span class="pill ${toneFor(impact)} small">${escapeHtml(impact === null ? "—" : fmtSignedMoney(impact))}</span>
    </td>
  </tr>`;
}

/**
 * Panel HTML for one payload. Pure so the status branches can be unit tested
 * without a DOM (see tests/js/eventProbability.test.mjs).
 */
export function eventRiskHtml(payload) {
  const status = String(payload?.status || "");
  const ticker = escapeHtml(payload?.ticker || "");

  if (status === "disabled") {
    return noticeHtml(
      "Event risk layer is off.",
      "Set EVENT_PROB_ENABLED=true and EVENT_PROB_MODE=shadow or live to load event probabilities.",
    );
  }
  if (status === "no_exposure") {
    return noticeHtml(
      `No mapped events for ${payload?.ticker || "this ticker"}.`,
      "Exposures are operator-authored in research/event_exposure_catalog.json.",
    );
  }
  if (status === "no_data") {
    return noticeHtml(
      "Events are mapped but have no observations yet.",
      "Run POST /api/event-probability/ingest, or wait for the hourly ingest job.",
    );
  }

  const summary = payload?.summary || {};
  const events = Array.isArray(payload?.events) ? payload.events : [];
  const warnings = Array.isArray(payload?.warnings) ? payload.warnings : [];

  if (status === "valuation_unavailable" || status === "error") {
    const reason = payload?.reason || "unknown";
    return `
      ${noticeHtml(
        "Probabilities loaded, valuation unavailable.",
        `Sensitivities need a usable DCF for ${payload?.ticker || "this ticker"} (${reason}).`,
      )}
      ${eventsTableHtml(events)}
    `;
  }

  const dist = summary.distribution || {};
  const worst = summary.largest_negative_exposure;
  const best = summary.largest_positive_exposure;
  const emsTone = toneFor(summary.event_mispricing_score);

  return `
    <div class="event-risk-kpis">
      ${kpiTile("Event-adjusted value", fmtMoney(summary.event_adjusted_expected_value), "", `base ${fmtMoney(summary.base_value)}`)}
      ${kpiTile(
        "Valuation adjustment",
        fmtSignedMoney(summary.valuation_adjustment),
        toneFor(summary.valuation_adjustment),
        `${fmtPct(summary.valuation_adjustment_pct)} of price`,
      )}
      ${kpiTile(
        "Largest exposure",
        worst ? fmtSignedMoney(worst.modeled_valuation_impact) : best ? fmtSignedMoney(best.modeled_valuation_impact) : "—",
        worst ? "bad" : best ? "good" : "",
        worst ? worst.fundamental_variable : best ? best.fundamental_variable : "no exposure",
      )}
      ${kpiTile("Mispricing gap", fmtPct(summary.event_mispricing_score), emsTone, `implied vs realized, ${escapeHtml(String(payload?.config?.ems_lookback_days ?? 7))}d`)}
    </div>
    ${warnings.length ? noticeHtml(`${warnings.length} event warning${warnings.length === 1 ? "" : "s"}.`, warnings.map((w) => `${w.event_id}: ${w.warning}`).join(" · ")) : ""}
    <details class="event-risk-details">
      <summary>Event detail (${events.length}) — probabilities, sensitivity, valuation impact</summary>
      ${eventsTableHtml(events)}
      ${distributionHtml(dist)}
      ${assumptionsHtml(payload)}
    </details>
    <p class="muted event-risk-footnote">
      Research signal for ${ticker}. Does not change scan scores, ranking, sizing, or advisory probabilities.
    </p>
  `;
}

function renderPayload(payload) {
  const host = body();
  if (!host) return;
  host.innerHTML = eventRiskHtml(payload);
  setAsyncState(host, ASYNC_SUCCESS);
}

function eventsTableHtml(events) {
  if (!events.length) return `<p class="muted">No events to show.</p>`;
  return `<div class="event-risk-table-wrap">
    <table class="event-risk-table">
      <thead>
        <tr>
          <th>Event</th>
          <th class="event-risk-num">Prob (adj)</th>
          <th class="event-risk-num">7d change</th>
          <th class="event-risk-num">Per +1pp</th>
          <th class="event-risk-num">Value impact</th>
        </tr>
      </thead>
      <tbody>${events.map(eventRowHtml).join("")}</tbody>
    </table>
  </div>`;
}

function distributionHtml(dist) {
  if (!dist || dist.expected_value === undefined) return "";
  const rows = [
    ["Expected value", fmtMoney(dist.expected_value)],
    ["Median", fmtMoney(dist.median_value)],
    ["p10 / p90", `${fmtMoney(dist.p10)} / ${fmtMoney(dist.p90)}`],
    ["P(above price)", fmtProb(dist.prob_positive_return)],
    ["P(>25% above)", fmtProb(dist.prob_return_over_25)],
    ["P(below price)", fmtProb(dist.prob_loss)],
  ];
  return `<div class="event-risk-subgrid">
    ${rows.map(([label, value]) => `<div><span class="muted">${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
  </div>`;
}

function assumptionsHtml(payload) {
  const items = Array.isArray(payload?.assumptions) ? payload.assumptions : [];
  if (!items.length) return "";
  return `<div class="event-risk-assumptions">
    <span class="muted">Assumptions</span>
    <ul>${items.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}</ul>
  </div>`;
}

/** Mark the panel stale when the ticker rail moves to a different symbol. */
export function applyEventProbabilityTickerStale(ticker) {
  const sym = String(ticker || "").trim().toUpperCase();
  if (!sym || sym === _lastTicker) return;
  const host = body();
  if (host && _lastTicker) {
    host.innerHTML = noticeHtml(`Ticker changed to ${sym}.`, "Run a quick check to reload event risk.");
  }
  _lastTicker = "";
}

export async function loadEventProbability(ticker) {
  const host = body();
  if (!host) return;
  const symbol = String(ticker || "").trim().toUpperCase();
  if (!symbol) {
    setAsyncState(host, ASYNC_EMPTY, {
      headline: "No ticker selected.",
      message: "Enter a ticker and press Check.",
    });
    return;
  }

  const gen = ++_gen;
  setAsyncState(host, ASYNC_LOADING, { message: `Loading event risk for ${symbol}…` });

  let out;
  try {
    out = await api.get(`/api/event-probability/${encodeURIComponent(symbol)}`);
  } catch (err) {
    if (!isCurrentGen(gen)) return;
    setAsyncState(host, ASYNC_ERROR, {
      headline: "Event risk unavailable.",
      message: String(err),
      onRetry: () => void loadEventProbability(symbol),
    });
    return;
  }
  if (!isCurrentGen(gen)) return;

  if (!out?.ok) {
    const reason = out?.error || out?.data?.recovery?.summary || "Request failed.";
    setAsyncState(host, ASYNC_ERROR, {
      headline: "Event risk unavailable.",
      message: String(reason),
      onRetry: () => void loadEventProbability(symbol),
    });
    logEvent({ kind: "system", severity: "warn", message: `Event risk ${symbol} failed: ${reason}` });
    return;
  }

  const payload = out.data || {};
  const payloadTickerSym = String(payload.ticker || "").toUpperCase();
  if (payloadTickerSym && payloadTickerSym !== symbol) return;

  _lastTicker = symbol;
  renderPayload(payload);
}

export function initEventProbabilityPanel() {
  const section = document.getElementById(SECTION_ID);
  if (!section) return;
  section.addEventListener("toggle", () => {
    if (!section.open || _lastTicker) return;
    const railTicker = document.getElementById("tickerInput")?.value || "";
    if (railTicker.trim()) void loadEventProbability(railTicker);
  });
}
