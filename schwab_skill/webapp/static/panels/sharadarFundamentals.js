/**
 * Sharadar Bundle panel — PIT fundamentals, daily multiples, insiders, 13F.
 *
 * Loads `/api/research/sharadar/<ticker>` in parallel with quick check.
 * Research only: does not change scan scores, ranking, sizing, or advisory.
 */

import { api } from "../modules/api.js";
import { escapeHtml, formatPercentPoints, pct } from "../modules/format.js";
import { humanizeDataQualityLabel } from "../modules/humanize.js";
import { logEvent } from "../modules/logger.js";
import { setAsyncState, ASYNC_ERROR, ASYNC_LOADING, ASYNC_EMPTY, ASYNC_SUCCESS } from "../modules/asyncState.js";

const SECTION_ID = "sharadarBundleSection";
const BODY_ID = "sharadarBundleBody";

let _gen = 0;
let _lastTicker = "";

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
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  return `${sign}$${abs.toFixed(2)}`;
}

function fmtNum(value, digits = 2) {
  const n = finiteNum(value);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function fmtRoe(value) {
  // ratio from sharadar_data.map_fundamentals_row (1.47 = 147%)
  const n = finiteNum(value);
  if (!Number.isFinite(n)) return "—";
  return pct(n, 1);
}

function fmtOwnership(value) {
  // percent points: SF3 holder percent / pit_fields_from_parts sum (tests: 6.5)
  const n = finiteNum(value);
  if (!Number.isFinite(n)) return "—";
  return formatPercentPoints(n, 1);
}

function fmtShares(value) {
  const n = finiteNum(value);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  return String(Math.round(n));
}

function kpiTile(label, value, sub = "") {
  return `<div class="sharadar-kpi">
    <span class="sharadar-kpi-label">${escapeHtml(label)}</span>
    <strong class="sharadar-kpi-value">${escapeHtml(value)}</strong>
    ${sub ? `<span class="sharadar-kpi-sub muted">${escapeHtml(sub)}</span>` : ""}
  </div>`;
}

function noticeHtml(headline, detail) {
  return `<div class="sharadar-notice">
    <strong>${escapeHtml(headline)}</strong>
    ${detail ? `<span class="muted">${escapeHtml(detail)}</span>` : ""}
  </div>`;
}

function tableRows(headers, rows) {
  if (!rows.length) {
    return `<p class="muted">None in the latest Sharadar pull.</p>`;
  }
  const head = headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("");
  const bodyRows = rows
    .map((cells) => `<tr>${cells.map((c) => `<td>${escapeHtml(c)}</td>`).join("")}</tr>`)
    .join("");
  return `<table class="sharadar-table"><thead><tr>${head}</tr></thead><tbody>${bodyRows}</tbody></table>`;
}

const ACTION_TOKENS = ["split", "dividend", "spinoff", "spin-off", "spin off", "stocksplit", "stock split", "div"];

function qualifyingEvents(payload) {
  const rows = [...(payload?.actions || []), ...(payload?.events || [])];
  const seen = new Set();
  const out = [];
  for (const row of rows) {
    const date = String(row?.date || "").slice(0, 10);
    const label = String(row?.event || row?.action || row?.name || "").trim();
    if (!date && !label) continue;
    const blob = `${label} ${row?.action || ""}`.toLowerCase();
    if (!ACTION_TOKENS.some((tok) => blob.includes(tok))) continue;
    const key = `${date}|${label.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ date, event: label });
  }
  return out;
}

function sparklinePath(values) {
  if (values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1e-9);
  const step = 100 / Math.max(values.length - 1, 1);
  return values
    .map((v, idx) => {
      const x = idx * step;
      const y = 34 - ((v - min) / span) * 30;
      return `${idx === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function artSparklineHtml(history) {
  const rows = (Array.isArray(history) ? history : [])
    .map((row) => ({
      datekey: String(row?.datekey || "").slice(0, 10),
      revenue: finiteNum(row?.revenue),
      fcf: finiteNum(row?.fcf),
      roe: finiteNum(row?.roe),
    }))
    .filter((row) => row.datekey)
    .sort((a, b) => a.datekey.localeCompare(b.datekey));
  if (!rows.length) {
    return `<p class="muted sharadar-sparkline-empty">No ART history in this snapshot.</p>`;
  }
  const series = [
    { key: "revenue", label: "Revenue", fmt: fmtMoney },
    { key: "fcf", label: "FCF", fmt: fmtMoney },
    { key: "roe", label: "ROE", fmt: fmtRoe },
  ];
  const charts = series
    .map((s) => {
      const vals = rows.map((r) => r[s.key]).filter((n) => Number.isFinite(n));
      if (vals.length < 2) {
        return `<div class="sharadar-sparkline-cell">
          <span class="muted">${escapeHtml(s.label)}</span>
          <p class="muted sharadar-sparkline-empty">Not enough ${escapeHtml(s.label)} points.</p>
        </div>`;
      }
      const d = sparklinePath(vals);
      return `<div class="sharadar-sparkline-cell">
        <span class="muted">${escapeHtml(s.label)}</span>
        <svg class="sharadar-sparkline" viewBox="0 0 100 38" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(s.label)} ${s.key === "roe" ? "ratio" : "USD"} vs filing date">
          <path d="${d}" fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke"></path>
        </svg>
      </div>`;
    })
    .join("");
  const table = tableRows(
    ["Filing date (datekey)", "Revenue", "FCF", "ROE"],
    [...rows].reverse().slice(0, 8).map((row) => [
      row.datekey,
      Number.isFinite(row.revenue) ? fmtMoney(row.revenue) : "—",
      Number.isFinite(row.fcf) ? fmtMoney(row.fcf) : "—",
      Number.isFinite(row.roe) ? fmtRoe(row.roe) : "—",
    ]),
  );
  return `<div class="sharadar-sparkline-grid">${charts}</div>${table}`;
}

function actionStripHtml(events) {
  if (!events.length) return "";
  const chips = events
    .slice(0, 8)
    .map((row) => `<span class="sharadar-action-chip">${escapeHtml(row.date)} · ${escapeHtml(row.event)}</span>`)
    .join("");
  return `<div class="sharadar-action-strip" aria-label="Corporate actions">
    <span class="sharadar-kpi-label">Corporate actions</span>
    <div class="sharadar-action-chips">${chips}</div>
  </div>`;
}

/**
 * Panel HTML for one payload. Pure so status branches can be unit tested.
 */
export function sharadarBundleHtml(payload) {
  const status = String(payload?.status || "");
  const ticker = payload?.ticker || "";
  const errors = Array.isArray(payload?.errors) ? payload.errors : [];
  const warnings = Array.isArray(payload?.warnings) ? payload.warnings : [];

  if (status === "disabled" || payload?.enabled === false) {
    const needsKey = errors.some((e) => e === "missing_sharadar_api_key" || e === "sharadar_disabled");
    return noticeHtml(
      "Sharadar Bundle is off.",
      needsKey
        ? "Sharadar is off. Add the Sharadar API key in System → Connect and restart the dashboard. Live scan ranking is unchanged."
        : payload?.data_quality || "Enable Sharadar to load PIT fundamentals, 13F, and insiders.",
    );
  }
  if (status === "error" && !payload?.profile && !payload?.daily && !payload?.fundamentals) {
    return noticeHtml("Sharadar snapshot unavailable.", errors.join(" · ") || payload?.data_quality || "Request failed.");
  }

  const profile = payload?.profile || {};
  const daily = payload?.daily || {};
  const fund = payload?.fundamentals || {};
  const sp = payload?.sp500 || {};
  const pit = payload?.pit_fields || {};
  const member =
    sp.is_member === true ? "S&P 500 member" : sp.is_member === false ? "Not in current S&P 500" : "S&P 500 n/a";
  const name = profile.name || ticker || "This name";
  const asOf = payload?.as_of || "n/a";
  const cacheNote = payload?.from_cache ? "cached" : "live";
  const sourceLine = `Sharadar Bundle · as-of ${asOf} · ${cacheNote} · Data quality: ${humanizeDataQualityLabel(payload?.data_quality || status)}`;
  const events = qualifyingEvents(payload);

  const insiderRows = (payload?.insiders || []).slice(0, 8).map((row) => [
    String(row.filingdate || ""),
    String(row.ownername || ""),
    String(row.transactioncode || ""),
    fmtShares(row.transactionshares),
    fmtMoney(row.transactionvalue || (Number(row.transactionshares) * Number(row.transactionprice) || null)),
  ]);
  const holderRows = (payload?.holders || []).slice(0, 8).map((row) => [
    String(row.name || ""),
    fmtShares(row.units),
    fmtMoney(row.value),
    row.percent == null ? "—" : fmtOwnership(row.percent),
  ]);
  const eventRows = events.map((row) => [String(row.date || ""), String(row.event || "")]);

  return `
    <p class="muted sharadar-provenance">${escapeHtml(sourceLine)}</p>
    ${warnings.length ? noticeHtml("Partial snapshot", warnings.join(" · ")) : ""}
    ${errors.length && status === "degraded" ? noticeHtml("Some Bundle tables failed", errors.join(" · ")) : ""}
    <p class="sharadar-identity">
      <strong>${escapeHtml(name)}</strong>
      <span class="muted">${escapeHtml([profile.exchange, profile.sector, profile.industry].filter(Boolean).join(" · ") || "US equity")}</span>
      <span class="sharadar-badge">${escapeHtml(member)}</span>
    </p>
    ${actionStripHtml(events)}
    <div class="sharadar-kpis">
      ${kpiTile("Market cap", fmtMoney(daily.marketcap ?? pit.market_cap), daily.date || "daily")}
      ${kpiTile("EV / EBITDA", fmtNum(daily.evebitda), "daily")}
      ${kpiTile("P/E", fmtNum(daily.pe), "daily")}
      ${kpiTile("P/S", fmtNum(daily.ps), "daily")}
      ${kpiTile("ROE TTM", fund.roe == null ? "—" : fmtRoe(fund.roe), fund.datekey || "ART")}
      ${kpiTile("Revenue TTM", fmtMoney(fund.revenue), fund.datekey || "ART")}
    </div>
    <details class="sharadar-details" open>
      <summary>Statements, holders, insiders</summary>
      <div class="sharadar-subgrid">
        <div><span class="muted">Net income TTM</span><strong>${escapeHtml(fmtMoney(fund.netinc))}</strong></div>
        <div><span class="muted">Free cash flow</span><strong>${escapeHtml(fmtMoney(fund.fcf))}</strong></div>
        <div><span class="muted">Shares (basic)</span><strong>${escapeHtml(fmtShares(fund.sharesbas || fund.shareswa))}</strong></div>
        <div><span class="muted">Debt / equity</span><strong>${escapeHtml(fmtNum(fund.de))}</strong></div>
        <div><span class="muted">Current ratio</span><strong>${escapeHtml(fmtNum(fund.currentratio))}</strong></div>
        <div><span class="muted">Inst. ownership</span><strong>${escapeHtml(pit.institutional_ownership == null ? "—" : fmtOwnership(pit.institutional_ownership))}</strong></div>
      </div>
      <h4 class="sharadar-h4">ART history</h4>
      ${artSparklineHtml(payload?.fundamentals_history || [])}
      <h4 class="sharadar-h4">13F holders</h4>
      ${tableRows(["Holder", "Units", "Value", "%"], holderRows)}
      <h4 class="sharadar-h4">Insider filings</h4>
      ${tableRows(["Filed", "Owner", "Code", "Shares", "Value"], insiderRows)}
      ${
        eventRows.length
          ? `<h4 class="sharadar-h4">Actions / events</h4>${tableRows(["Date", "Event"], eventRows)}`
          : ""
      }
    </details>
    <p class="muted sharadar-footnote">
      Point-in-time Sharadar Bundle for ${escapeHtml(ticker)}. This panel does not change scan scores or live ranking.
    </p>
  `;
}

function renderPayload(payload) {
  const host = body();
  if (!host) return;
  host.innerHTML = sharadarBundleHtml(payload);
  setAsyncState(host, ASYNC_SUCCESS);
}

export function applySharadarBundleTickerStale(ticker) {
  const sym = String(ticker || "").trim().toUpperCase();
  if (!sym || sym === _lastTicker) return;
  const host = body();
  if (host && _lastTicker) {
    host.innerHTML = noticeHtml(`Ticker changed to ${sym}.`, "Run a quick check to reload Sharadar.");
  }
  _lastTicker = "";
}

export async function loadSharadarBundle(ticker, { refresh = false } = {}) {
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
  setAsyncState(host, ASYNC_LOADING, { message: `Loading Sharadar Bundle for ${symbol}…` });

  const out = await api.getResearchSharadar(symbol, { refresh });
  if (!isCurrentGen(gen)) return;

  if (!out?.ok) {
    const reason = out?.error || out?.data?.recovery?.summary || "Request failed.";
    setAsyncState(host, ASYNC_ERROR, {
      headline: "Sharadar snapshot unavailable.",
      message: String(reason),
      onRetry: () => void loadSharadarBundle(symbol, { refresh }),
    });
    logEvent({ kind: "system", severity: "warn", message: `Sharadar ${symbol} failed: ${reason}` });
    return;
  }

  const payload = out.data || {};
  const payloadTicker = String(payload.ticker || "").toUpperCase();
  if (payloadTicker && payloadTicker !== symbol) return;

  _lastTicker = symbol;
  renderPayload(payload);
}

export function initSharadarBundlePanel() {
  const section = document.getElementById(SECTION_ID);
  if (!section) return;
  document.getElementById("sharadarBundleRefreshBtn")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const railTicker = document.getElementById("tickerInput")?.value || _lastTicker;
    if (railTicker.trim()) void loadSharadarBundle(railTicker, { refresh: true });
  });
  section.addEventListener("toggle", () => {
    if (!section.open || _lastTicker) return;
    const railTicker = document.getElementById("tickerInput")?.value || "";
    if (railTicker.trim()) void loadSharadarBundle(railTicker);
  });
}
