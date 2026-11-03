/**
 * Pricing model panel — SEC companyfacts DCF with local assumption overlays.
 *
 * Loads `/api/research/pricing/<ticker>` in parallel with quick check so a
 * first-time ingest cannot block the fast symbol check. Slider changes POST
 * `/recompute` (CPU only; no EDGAR/Yahoo).
 *
 * Research only: does not change scan scores, ranking, sizing, or advisory.
 */

import { api } from "../modules/api.js";
import { escapeHtml, formatPercentPoints, pct } from "../modules/format.js";
import { humanizeDataQualityLabel } from "../modules/humanize.js";
import { logEvent } from "../modules/logger.js";
import { setAsyncState, ASYNC_ERROR, ASYNC_LOADING, ASYNC_EMPTY, ASYNC_SUCCESS } from "../modules/asyncState.js";

const SECTION_ID = "pricingModelSection";
const BODY_ID = "pricingModelBody";

let _gen = 0;
let _lastTicker = "";
let _lastInputs = null;
let _lastSharadar = null;
let _recomputeTimer = 0;

function body() {
  return document.getElementById(BODY_ID);
}

function isCurrentGen(gen) {
  return gen === _gen;
}

function finiteNum(value) {
  if (value == null || value === "") return Number.NaN;
  const n = Number(value);
  return Number.isFinite(n) ? n : Number.NaN;
}

function fmtMoney(value) {
  const n = finiteNum(value);
  if (!Number.isFinite(n)) return "—";
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}


function fmtBillions(value) {
  const n = finiteNum(value);
  if (!Number.isFinite(n)) return "—";
  return `$${(n / 1e9).toFixed(2)}B`;
}

function toneForMos(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "good" : "bad";
}

function kpiTile(label, value, tone = "", sub = "") {
  const toneClass = tone ? ` pricing-model-kpi--${tone}` : "";
  return `<div class="pricing-model-kpi${toneClass}">
    <span class="pricing-model-kpi-label">${escapeHtml(label)}</span>
    <strong class="pricing-model-kpi-value">${escapeHtml(value)}</strong>
    ${sub ? `<span class="pricing-model-kpi-sub muted">${escapeHtml(sub)}</span>` : ""}
  </div>`;
}

function noticeHtml(headline, detail) {
  return `<div class="pricing-model-notice">
    <strong>${escapeHtml(headline)}</strong>
    ${detail ? `<span class="muted">${escapeHtml(detail)}</span>` : ""}
  </div>`;
}

function pctInput(id, label, ratio) {
  const pct = Number.isFinite(Number(ratio)) ? (Number(ratio) * 100).toFixed(1) : "";
  return `<label class="pricing-model-field">
    <span>${escapeHtml(label)}</span>
    <input id="${id}" type="number" step="0.1" value="${escapeHtml(pct)}" data-pricing-field="${id}" />
  </label>`;
}

function fmtShares(value) {
  const n = finiteNum(value);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  return String(Math.round(n));
}

function sharadarColumnHtml(cross) {
  if (!cross || cross.available === false || cross.status === "disabled" || cross.enabled === false) {
    const needsKey = Array.isArray(cross?.errors)
      ? cross.errors.some((e) => e === "missing_sharadar_api_key" || e === "sharadar_disabled")
      : true;
    return `<div class="pricing-model-column">
      <h4 class="pricing-model-col-title">Sharadar Bundle</h4>
      ${noticeHtml(
        "Sharadar column unavailable.",
        needsKey
          ? "Set SHARADAR_API_KEY. This column does not fall back to Yahoo."
          : cross?.data_quality || cross?.errors?.join(" · ") || "Enable Sharadar to load EV / FCF.",
      )}
    </div>`;
  }
  const asOf = cross.as_of || cross.daily_date || "n/a";
  const line = `Sharadar Bundle · as-of ${asOf} · Data quality: ${humanizeDataQualityLabel(cross.data_quality || cross.status || "n/a")}`;
  return `<div class="pricing-model-column">
    <h4 class="pricing-model-col-title">Sharadar Bundle</h4>
    <p class="muted pricing-model-provenance">${escapeHtml(line)}</p>
    <div class="pricing-model-subgrid">
      <div><span class="muted">Enterprise value</span><strong>${escapeHtml(fmtBillions(cross.ev))}</strong></div>
      <div><span class="muted">EV / EBITDA</span><strong>${escapeHtml(Number.isFinite(Number(cross.evebitda)) ? Number(cross.evebitda).toFixed(2) : "—")}</strong></div>
      <div><span class="muted">ART FCF</span><strong>${escapeHtml(fmtBillions(cross.fcf))}</strong></div>
      <div><span class="muted">Shares</span><strong>${escapeHtml(fmtShares(cross.shares))}</strong></div>
    </div>
    <p class="muted small">Display only — not mixed into the companyfacts DCF.</p>
  </div>`;
}

/**
 * Panel HTML for one payload. Pure so status branches can be unit tested.
 */
export function pricingModelHtml(payload) {
  const status = String(payload?.status || "");
  const ticker = payload?.ticker || "";

  if (status === "disabled") {
    return noticeHtml(
      "Companyfacts pricing is off.",
      "Set COMPANYFACTS_ENABLED=true to load SEC XBRL statements into this panel.",
    );
  }
  if (status === "not_ingested") {
    return `${noticeHtml(
      `No cached companyfacts for ${ticker || "this ticker"}.`,
      payload?.reason || "Refresh fundamentals to fetch the SEC XBRL document once.",
    )}<p class="muted">Click Refresh fundamentals — sliders stay local after that.</p>`;
  }
  if (status === "error") {
    return noticeHtml("Pricing model unavailable.", payload?.reason || "SEC companyfacts request failed.");
  }

  const dcf = payload?.dcf || {};
  const inputs = payload?.inputs || {};
  const provenance = payload?.provenance || {};
  const warnings = Array.isArray(payload?.warnings) ? payload.warnings : [];
  const mos = dcf.margin_of_safety;
  const asOf = provenance.as_of || "n/a";
  const sourceLine = `${provenance.source || "sec.companyfacts"} · CIK ${provenance.cik || "—"} · as-of ${asOf}${
    provenance.from_cache ? " · cached" : ""
  }`;

  const fcfRows = (dcf.fcf_history || [])
    .slice(-4)
    .map((row) => `<tr><td>${escapeHtml(String(row.year || ""))}</td><td class="pricing-model-num">${escapeHtml(fmtBillions(row.fcf))}</td></tr>`)
    .join("");
  const projRows = (dcf.projected_fcf || [])
    .map(
      (row) =>
        `<tr><td>Y${escapeHtml(String(row.year || ""))}</td><td class="pricing-model-num">${escapeHtml(fmtBillions(row.fcf))}</td><td class="pricing-model-num">${escapeHtml(fmtBillions(row.pv))}</td></tr>`,
    )
    .join("");

  const heat = (dcf.sensitivity || [])
    .map((row) => {
      const cells = [0.02, 0.025, 0.03]
        .map((tg) => `<td class="pricing-model-num">${escapeHtml(fmtMoney(row[`tg_${tg}`]))}</td>`)
        .join("");
      return `<tr><td class="pricing-model-num">${escapeHtml(pct(row.wacc, 1))}</td>${cells}</tr>`; // ratio 0–1 from companyfacts SENSITIVITY_WACC
    })
    .join("");

  return `
    <div class="pricing-model-columns">
      <div class="pricing-model-column">
        <h4 class="pricing-model-col-title">SEC companyfacts</h4>
        <p class="muted pricing-model-provenance">${escapeHtml(sourceLine)}</p>
        ${warnings.length ? noticeHtml("Statement gaps", warnings.join(" · ")) : ""}
        <div class="pricing-model-kpis">
          ${kpiTile("Intrinsic value", fmtMoney(dcf.intrinsic_value), "", "per share")}
          ${kpiTile("Price", fmtMoney(dcf.current_price), "", payload?.price_source || "quote")}
          ${kpiTile("Margin of safety", formatPercentPoints(mos, 1), toneForMos(mos), "vs live price")}
          ${kpiTile("Implied growth", pct(payload?.implied_growth, 1), "", "reverse DCF at current price")}
        </div>
      </div>
      ${sharadarColumnHtml(payload?.sharadar_cross_check)}
    </div>
    <form id="pricingModelForm" class="pricing-model-form" data-pricing-ticker="${escapeHtml(ticker)}">
      ${pctInput("pricingGrowth", "FCF growth %", inputs.growth)}
      ${pctInput("pricingWacc", "WACC %", inputs.wacc)}
      ${pctInput("pricingTerminal", "Terminal growth %", inputs.terminal_growth)}
      <button class="btn small secondary" type="button" id="pricingRecomputeBtn">Recompute</button>
    </form>
    <details class="pricing-model-details">
      <summary>Bridge, history, and WACC × terminal heatmap</summary>
      <div class="pricing-model-subgrid">
        <div><span class="muted">Last FCF</span><strong>${escapeHtml(fmtBillions(inputs.last_fcf))}</strong></div>
        <div><span class="muted">Net debt</span><strong>${escapeHtml(fmtBillions(inputs.net_debt))}</strong></div>
        <div><span class="muted">Shares</span><strong>${escapeHtml(Number(inputs.shares) ? `${(Number(inputs.shares) / 1e9).toFixed(2)}B` : "—")}</strong></div>
        <div><span class="muted">Implied WACC</span><strong>${escapeHtml(pct(payload?.implied_wacc, 1))}</strong></div>
      </div>
      <div class="pricing-model-tables">
        <table class="pricing-model-table">
          <thead><tr><th>Year</th><th class="pricing-model-num">FCF</th></tr></thead>
          <tbody>${fcfRows || "<tr><td colspan='2' class='muted'>No FCF history.</td></tr>"}</tbody>
        </table>
        <table class="pricing-model-table">
          <thead><tr><th>Proj</th><th class="pricing-model-num">FCF</th><th class="pricing-model-num">PV</th></tr></thead>
          <tbody>${projRows || "<tr><td colspan='3' class='muted'>No projection.</td></tr>"}</tbody>
        </table>
      </div>
      <table class="pricing-model-table">
        <thead><tr><th>WACC \\ TG</th><th class="pricing-model-num">2.0%</th><th class="pricing-model-num">2.5%</th><th class="pricing-model-num">3.0%</th></tr></thead>
        <tbody>${heat || "<tr><td colspan='4' class='muted'>No sensitivity.</td></tr>"}</tbody>
      </table>
    </details>
    <p class="muted pricing-model-footnote">
      Research model for ${escapeHtml(ticker)}. Sliders reprice from cached SEC statements — they do not call EDGAR or Yahoo, and they do not change scan scores.
    </p>
  `;
}

function readAssumptionsFromForm() {
  const growthEl = document.getElementById("pricingGrowth");
  const waccEl = document.getElementById("pricingWacc");
  const tgEl = document.getElementById("pricingTerminal");
  const toRatio = (el) => {
    const n = Number(el?.value);
    return Number.isFinite(n) ? n / 100 : null;
  };
  const body = {
    growth: toRatio(growthEl),
    wacc: toRatio(waccEl),
    terminal_growth: toRatio(tgEl),
  };
  if (_lastInputs?.last_fcf != null) body.last_fcf = _lastInputs.last_fcf;
  if (_lastInputs?.net_debt != null) body.net_debt = _lastInputs.net_debt;
  if (_lastInputs?.shares != null) body.shares = _lastInputs.shares;
  if (_lastInputs?.price != null) body.price = _lastInputs.price;
  return body;
}

function renderPayload(payload) {
  const host = body();
  if (!host) return;
  const next = payload || {};
  if (next.sharadar_cross_check) _lastSharadar = next.sharadar_cross_check;
  else if (_lastSharadar) next.sharadar_cross_check = _lastSharadar;
  host.innerHTML = pricingModelHtml(next);
  setAsyncState(host, ASYNC_SUCCESS);
  _lastInputs = next?.inputs || null;
  wireForm(_lastTicker);
}

function wireForm(ticker) {
  const form = document.getElementById("pricingModelForm");
  if (!form || !ticker) return;
  form.querySelectorAll("input[data-pricing-field]").forEach((input) => {
    input.addEventListener("input", () => scheduleRecompute(ticker));
  });
  document.getElementById("pricingRecomputeBtn")?.addEventListener("click", (e) => {
    e.preventDefault();
    void recomputeNow(ticker);
  });
}

function scheduleRecompute(ticker) {
  if (_recomputeTimer) window.clearTimeout(_recomputeTimer);
  _recomputeTimer = window.setTimeout(() => {
    _recomputeTimer = 0;
    void recomputeNow(ticker);
  }, 280);
}

async function recomputeNow(ticker) {
  const host = body();
  if (!host || !ticker) return;
  const gen = _gen;
  const out = await api.recomputeResearchPricing(ticker, readAssumptionsFromForm());
  if (!isCurrentGen(gen)) return;
  if (!out?.ok) {
    logEvent({ kind: "system", severity: "warn", message: `Pricing recompute ${ticker} failed: ${out?.error}` });
    return;
  }
  renderPayload(out.data || {});
}

export function applyPricingModelTickerStale(ticker) {
  const sym = String(ticker || "").trim().toUpperCase();
  if (!sym || sym === _lastTicker) return;
  const host = body();
  if (host && _lastTicker) {
    host.innerHTML = noticeHtml(`Ticker changed to ${sym}.`, "Run a quick check to reload the pricing model.");
  }
  _lastTicker = "";
  _lastInputs = null;
  _lastSharadar = null;
}

export async function loadPricingModel(ticker, { refresh = false } = {}) {
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
  setAsyncState(host, ASYNC_LOADING, { message: `Loading pricing model for ${symbol}…` });

  const out = refresh
    ? await api.ingestResearchPricing(symbol)
    : await api.getResearchPricing(symbol);
  if (!isCurrentGen(gen)) return;

  if (!out?.ok) {
    const reason = out?.error || out?.data?.recovery?.summary || "Request failed.";
    setAsyncState(host, ASYNC_ERROR, {
      headline: "Pricing model unavailable.",
      message: String(reason),
      onRetry: () => void loadPricingModel(symbol, { refresh }),
    });
    logEvent({ kind: "system", severity: "warn", message: `Pricing model ${symbol} failed: ${reason}` });
    return;
  }

  const payload = out.data || {};
  const payloadTicker = String(payload.ticker || "").toUpperCase();
  if (payloadTicker && payloadTicker !== symbol) return;

  _lastTicker = symbol;
  renderPayload(payload);
}

export function initPricingModelPanel() {
  const section = document.getElementById(SECTION_ID);
  if (!section) return;
  document.getElementById("pricingModelRefreshBtn")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const railTicker = document.getElementById("tickerInput")?.value || _lastTicker;
    if (railTicker.trim()) void loadPricingModel(railTicker, { refresh: true });
  });
  section.addEventListener("toggle", () => {
    if (!section.open || _lastTicker) return;
    const railTicker = document.getElementById("tickerInput")?.value || "";
    if (railTicker.trim()) void loadPricingModel(railTicker);
  });
}
