/**
 * Sharadar Bundle ticker-vs-ticker compare — Research → Quick check → Deep.
 *
 * Sibling of SEC Filing Compare. Additive ?section= aliases only.
 * Research only: does not change scan scores, ranking, sizing, or advisory.
 */

import { api } from "../modules/api.js";
import { escapeHtml, pct } from "../modules/format.js";
import { humanizeDataQualityLabel } from "../modules/humanize.js";
import { logEvent } from "../modules/logger.js";
import { setAsyncState, ASYNC_ERROR, ASYNC_LOADING, ASYNC_EMPTY, ASYNC_SUCCESS } from "../modules/asyncState.js";
import { syncResearchTickerRail } from "../modules/researchTickerFlow.js";

const SECTION_ID = "sharadarCompareSection";
const BODY_ID = "sharadarCompareBody";

let _gen = 0;

function body() {
  return document.getElementById(BODY_ID);
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

function formatField(id, value) {
  if (id === "roe") return Number.isFinite(finiteNum(value)) ? pct(value, 1) : "—"; // ratio from sharadar_data.map_fundamentals_row
  if (id === "revenue" || id === "fcf" || id === "marketcap") return fmtMoney(value);
  return fmtNum(value);
}

export function sharadarCompareHtml(payload) {
  const left = payload?.left || {};
  const right = payload?.right || {};
  const fields = Array.isArray(payload?.fields) ? payload.fields : [];
  const leftOff = left.status === "disabled" || left.enabled === false;
  const rightOff = right.status === "disabled" || right.enabled === false;
  if (leftOff && rightOff) {
    return `<div class="sharadar-notice">
      <strong>Sharadar Bundle is off.</strong>
      <span class="muted">Add the Sharadar API key in System → Connect. Live scan ranking is unchanged.</span>
    </div>`;
  }
  const header = (side) => {
    const name = side.name || side.ticker || "—";
    const asOf = side.as_of || "n/a";
    const dq = side.data_quality || side.status || "n/a";
    return `<div>
      <strong>${escapeHtml(name)}</strong>
      <p class="muted small">${escapeHtml(side.ticker || "")} · as-of ${escapeHtml(asOf)} · Data quality: ${escapeHtml(humanizeDataQualityLabel(dq))}</p>
    </div>`;
  };
  const rows = fields
    .map((field) => {
      const id = field.id;
      return `<tr>
        <th>${escapeHtml(field.label || id)}</th>
        <td>${escapeHtml(formatField(id, left[id]))}</td>
        <td>${escapeHtml(formatField(id, right[id]))}</td>
      </tr>`;
    })
    .join("");
  return `
    <div class="sharadar-compare-heads">
      ${header(left)}
      ${header(right)}
    </div>
    <table class="sharadar-table sharadar-compare-table">
      <thead><tr><th>Metric</th><th>${escapeHtml(left.ticker || "A")}</th><th>${escapeHtml(right.ticker || "B")}</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="muted sharadar-footnote">Sharadar daily + latest ART. Research only — does not change scan scores.</p>
  `;
}

export async function runSharadarCompare({ refresh = false } = {}) {
  const host = body();
  if (!host) return;
  const a = document.getElementById("sharadarCompareTickerA")?.value?.trim()?.toUpperCase() || "";
  const b = document.getElementById("sharadarCompareTickerB")?.value?.trim()?.toUpperCase() || "";
  if (!a || !b) {
    setAsyncState(host, ASYNC_EMPTY, {
      headline: "Enter two tickers.",
      message: "Compare daily multiples and latest ART for two names.",
    });
    return;
  }
  const gen = ++_gen;
  setAsyncState(host, ASYNC_LOADING, { message: `Comparing ${a} vs ${b}…` });
  const out = await api.getResearchSharadarCompare(a, b, { refresh });
  if (gen !== _gen) return;
  if (!out?.ok) {
    const reason = out?.error || "Request failed.";
    setAsyncState(host, ASYNC_ERROR, {
      headline: "Sharadar compare unavailable.",
      message: String(reason),
      onRetry: () => void runSharadarCompare({ refresh }),
    });
    logEvent({ kind: "system", severity: "warn", message: `Sharadar compare ${a}/${b} failed: ${reason}` });
    return;
  }
  host.innerHTML = sharadarCompareHtml(out.data || {});
  setAsyncState(host, ASYNC_SUCCESS);
}

export function initSharadarComparePanel() {
  const section = document.getElementById(SECTION_ID);
  if (!section) return;
  document.getElementById("sharadarCompareBtn")?.addEventListener("click", (e) => {
    e.preventDefault();
    void runSharadarCompare();
  });
  document.getElementById("sharadarCompareTickerA")?.addEventListener("change", () => {
    const a = document.getElementById("sharadarCompareTickerA")?.value || "";
    if (a.trim()) syncResearchTickerRail(a, { sourceId: "sharadarCompareTickerA" });
  });
  document.querySelectorAll("#sharadarComparePresetButtons button[data-a]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const a = btn.getAttribute("data-a") || "";
      const b = btn.getAttribute("data-b") || "";
      const aEl = document.getElementById("sharadarCompareTickerA");
      const bEl = document.getElementById("sharadarCompareTickerB");
      if (aEl) aEl.value = a;
      if (bEl) bEl.value = b;
      if (a) syncResearchTickerRail(a, { sourceId: "sharadarCompareTickerA" });
    });
  });
}
