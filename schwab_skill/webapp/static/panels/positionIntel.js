/**
 * Position Intel panel — four quant tables for current open positions:
 *   1. Conviction Scorecard (six indicator votes → composite in [−2, +2])
 *   2. Volatility Signals (HV20 vs GARCH(1,1) forecast, expansion %)
 *   3. Options Setup (long call nearest 0.40Δ in a ~2-week window)
 *   4. Covered Calls (income: OTM call 0.15–0.40Δ, max annualized yield)
 *
 * Driven by `GET /api/position-intel` (server-side ~5 min TTL cache;
 * `?refresh=1` forces recompute). Manual ticker lookup uses
 * `GET /api/position-intel/lookup?ticker=` and opens a modal with the
 * same four tables for one symbol. Missing values render as "—" — never
 * fabricated. `data_quality` notes are surfaced verbatim.
 */

import { api } from "../modules/api.js";
import { safeText } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import {
  setAsyncState,
  ASYNC_LOADING,
  ASYNC_EMPTY,
  ASYNC_ERROR,
  ASYNC_SUCCESS,
  ASYNC_SIGNED_OUT,
} from "../modules/asyncState.js";

/**
 * @typedef {Object} ConvictionRow
 * @property {string} ticker
 * @property {number|null} rsi
 * @property {{rsi:number|null,macd:number|null,trend:number|null,bb:number|null,vol:number|null,range:number|null}} votes
 * @property {number|null} composite
 * @property {"BUY"|"NEUTRAL"|"LEAN BEAR"|"SELL"|null} signal
 *
 * @typedef {Object} VolatilityRow
 * @property {string} ticker
 * @property {number|null} hv20 annualized fraction (0.42 = 42%)
 * @property {number|null} garch annualized fraction
 * @property {number|null} expansion_pct
 * @property {"LOW"|"NORMAL"|"HIGH"|"EXTREME"|null} regime
 * @property {"SIGNAL"|"BASE"|"WEAK"|null} signal
 *
 * @typedef {Object} OptionRow
 * @property {string} ticker
 * @property {string} expiry
 * @property {number} strike
 * @property {number} mid
 * @property {number} dte
 * @property {number} delta
 * @property {number|null} breakeven_pct
 * @property {number|null} p_win
 * @property {number|null} edge_pct
 * @property {string} liq
 *
 * @typedef {Object} CoveredRow
 * @property {string} ticker
 * @property {string} expiry
 * @property {number} strike
 * @property {number} mid
 * @property {number} dte
 * @property {number} delta
 * @property {number|null} yield_pct
 * @property {number|null} p_keep
 * @property {number|null} annualized_pct
 * @property {string} liq
 */

let _loading = false;
let _lookupLoading = false;

const DASH = "—";
const TICKER_RE = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;

function fmtPctFrac(v, digits = 0) {
  return v === null || v === undefined ? DASH : `${(Number(v) * 100).toFixed(digits)}%`;
}

function fmtSigned(v, digits = 1, suffix = "%") {
  if (v === null || v === undefined) return DASH;
  const n = Number(v);
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}${suffix}`;
}

function fmtMoney(v) {
  return v === null || v === undefined ? DASH : `$${Number(v).toFixed(2)}`;
}

function toneClass(value, { goodAbove = 0, badBelow = 0 } = {}) {
  if (value === null || value === undefined) return "muted";
  if (Number(value) > goodAbove) return "intel-good";
  if (Number(value) < badBelow) return "intel-bad";
  return "muted";
}

function pill(label, cls) {
  return `<span class="pill ${cls}">${safeText(label)}</span>`;
}

const SIGNAL_PILL = { BUY: "good", NEUTRAL: "neutral", "LEAN BEAR": "warn", SELL: "bad" };
const VOL_SIGNAL_PILL = { SIGNAL: "good", BASE: "neutral", WEAK: "neutral" };
const REGIME_PILL = { LOW: "info", NORMAL: "neutral", HIGH: "warn", EXTREME: "bad" };
const LIQ_PILL = { TIGHT: "good", OK: "neutral", WIDE: "warn", "LOW OI": "warn", ILLIQUID: "bad" };

function signalPill(signal) {
  return signal ? pill(signal, SIGNAL_PILL[signal] || "neutral") : DASH;
}

/** Vote dot: +1 → filled good, −1 → filled bad, 0 → hollow, null → dash. */
function voteDot(vote) {
  if (vote === null || vote === undefined) return `<span class="muted">${DASH}</span>`;
  const cls = vote > 0 ? "intel-dot--good" : vote < 0 ? "intel-dot--bad" : "intel-dot--flat";
  const label = vote > 0 ? "bullish" : vote < 0 ? "bearish" : "neutral";
  return `<span class="intel-dot ${cls}" role="img" aria-label="${label}"></span>`;
}

/** Centered-origin signed bar; `scale` is the |value| that fills half the track. */
function signedBar(value, scale, text) {
  if (value === null || value === undefined) {
    return `<span class="muted mono-nums">${DASH}</span>`;
  }
  const v = Number(value);
  const half = Math.min(50, (Math.abs(v) / scale) * 50);
  const dir = v >= 0 ? "intel-bar-fill--good" : "intel-bar-fill--bad";
  const side = v >= 0 ? `left:50%;width:${half.toFixed(1)}%` : `left:${(50 - half).toFixed(1)}%;width:${half.toFixed(1)}%`;
  const toneCls = v > 0 ? "intel-good" : v < 0 ? "intel-bad" : "muted";
  return `<span class="intel-bar-cell">
    <span class="intel-bar-track" aria-hidden="true"><span class="intel-bar-fill ${dir}" style="${side}"></span></span>
    <span class="mono-nums ${toneCls}">${safeText(text)}</span>
  </span>`;
}

function tableShell(title, hint, headHtml, bodyHtml, emptyMsg) {
  const body = bodyHtml || `<tr><td colspan="20" class="muted">${safeText(emptyMsg || "No rows.")}</td></tr>`;
  return `<div class="intel-table-card">
    <div class="intel-table-head">
      <h3>${safeText(title)}</h3>
      <small class="muted">${safeText(hint)}</small>
    </div>
    <div class="table-wrap intel-table-wrap">
      <table>
        <thead><tr>${headHtml}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  </div>`;
}

function tickerCell(ticker) {
  const t = String(ticker || "").toUpperCase();
  return `<td class="mono-nums intel-ticker">
    <button type="button" class="intel-ticker-btn" data-intel-ticker="${safeText(t)}" title="Open intel for ${safeText(t)}">${safeText(t)}</button>
  </td>`;
}

/** @param {ConvictionRow[]} rows */
function convictionTable(rows) {
  const head =
    "<th>Ticker</th><th>Signal</th><th>Composite</th><th class=\"num\">RSI</th>" +
    "<th class=\"ctr\">MACD</th><th class=\"ctr\">Trend</th><th class=\"ctr\">BB</th><th class=\"ctr\">Vol</th><th class=\"ctr\">Range</th>";
  const body = (rows || [])
    .map((r) => {
      const v = r.votes || {};
      const rsiTone = r.rsi === null || r.rsi === undefined ? "muted" : r.rsi > 55 ? "intel-good" : r.rsi < 45 ? "intel-bad" : "muted";
      return `<tr>
        ${tickerCell(r.ticker)}
        <td>${signalPill(r.signal)}</td>
        <td class="intel-bar-td">${signedBar(r.composite, 2, fmtSigned(r.composite, 2, ""))}</td>
        <td class="num mono-nums ${rsiTone}">${r.rsi === null || r.rsi === undefined ? DASH : r.rsi.toFixed(1)}</td>
        <td class="ctr">${voteDot(v.macd)}</td>
        <td class="ctr">${voteDot(v.trend)}</td>
        <td class="ctr">${voteDot(v.bb)}</td>
        <td class="ctr">${voteDot(v.vol)}</td>
        <td class="ctr">${voteDot(v.range)}</td>
      </tr>`;
    })
    .join("");
  return tableShell(
    "Conviction Scorecard",
    "six indicators · composite in [−2, +2] · BUY ≥ +1.0, SELL ≤ −1.0",
    head,
    body,
    "No open equity positions to score.",
  );
}

/** @param {VolatilityRow[]} rows */
function volatilityTable(rows) {
  const head =
    "<th>Ticker</th><th class=\"num\">HV 20D</th><th class=\"num\">GARCH</th><th>Expansion</th><th class=\"ctr\">Regime</th><th class=\"ctr\">Signal</th>";
  const body = (rows || [])
    .map(
      (r) => `<tr>
        ${tickerCell(r.ticker)}
        <td class="num mono-nums">${fmtPctFrac(r.hv20)}</td>
        <td class="num mono-nums">${fmtPctFrac(r.garch)}${r.garch_method === "ewma" ? '<sup class="muted" title="EWMA fallback — GARCH fit unavailable">*</sup>' : ""}</td>
        <td class="intel-bar-td">${signedBar(r.expansion_pct, 50, fmtSigned(r.expansion_pct))}</td>
        <td class="ctr">${r.regime ? pill(r.regime, REGIME_PILL[r.regime] || "neutral") : DASH}</td>
        <td class="ctr">${r.signal ? pill(r.signal, VOL_SIGNAL_PILL[r.signal] || "neutral") : DASH}</td>
      </tr>`,
    )
    .join("");
  return tableShell(
    "Volatility Signals",
    "GARCH(1,1) next-day forecast vs 20-day realized · SIGNAL ≥ +25% expansion",
    head,
    body,
    "No volatility rows — price history unavailable.",
  );
}

/** @param {OptionRow[]} rows */
function optionsTable(rows) {
  const head =
    "<th>Ticker</th><th class=\"ctr\">Expiry</th><th class=\"num\">Strike</th><th class=\"num\">Mid</th><th class=\"ctr\">DTE</th>" +
    "<th class=\"num\">Delta</th><th class=\"num\">B/E</th><th class=\"num\">P(win)</th><th class=\"num\">Edge</th><th class=\"ctr\">Liq</th>";
  const body = (rows || [])
    .map(
      (r) => `<tr>
        ${tickerCell(r.ticker)}
        <td class="ctr mono-nums">${safeText(r.expiry || DASH)}</td>
        <td class="num mono-nums">$${Number(r.strike).toFixed(r.strike % 1 ? 2 : 0)}C</td>
        <td class="num mono-nums">${fmtMoney(r.mid)}</td>
        <td class="ctr mono-nums">${r.dte ?? DASH}d</td>
        <td class="num mono-nums">${r.delta === null || r.delta === undefined ? DASH : Number(r.delta).toFixed(2)}</td>
        <td class="num mono-nums">${fmtSigned(r.breakeven_pct)}</td>
        <td class="num mono-nums">${fmtPctFrac(r.p_win)}</td>
        <td class="num mono-nums ${toneClass(r.edge_pct)}">${fmtSigned(r.edge_pct, 0)}</td>
        <td class="ctr">${pill(r.liq || DASH, LIQ_PILL[r.liq] || "neutral")}</td>
      </tr>`,
    )
    .join("");
  return tableShell(
    "Options Setup — long call",
    "strike nearest 0.40Δ, ~7–21 DTE · EDGE = (GARCH vol − market IV) / IV",
    head,
    body,
    "No tradeable near-0.40Δ calls found for current positions.",
  );
}

/** @param {CoveredRow[]} rows */
function coveredTable(rows) {
  const head =
    "<th>Ticker</th><th class=\"ctr\">Expiry</th><th class=\"num\">Strike</th><th class=\"num\">Mid</th><th class=\"ctr\">DTE</th>" +
    "<th class=\"num\">Delta</th><th class=\"num\">Yield %</th><th class=\"num\">P(keep)</th><th class=\"num\">Ann %</th><th class=\"ctr\">Liq</th>";
  const body = (rows || [])
    .map(
      (r) => `<tr>
        ${tickerCell(r.ticker)}
        <td class="ctr mono-nums">${safeText(r.expiry || DASH)}</td>
        <td class="num mono-nums">$${Number(r.strike).toFixed(r.strike % 1 ? 2 : 0)}C</td>
        <td class="num mono-nums">${fmtMoney(r.mid)}</td>
        <td class="ctr mono-nums">${r.dte ?? DASH}d</td>
        <td class="num mono-nums">${r.delta === null || r.delta === undefined ? DASH : Number(r.delta).toFixed(2)}</td>
        <td class="num mono-nums">${r.yield_pct === null || r.yield_pct === undefined ? DASH : `${Number(r.yield_pct).toFixed(2)}%`}</td>
        <td class="num mono-nums"><strong>${fmtPctFrac(r.p_keep)}</strong></td>
        <td class="num mono-nums intel-good">${r.annualized_pct === null || r.annualized_pct === undefined ? DASH : `${Number(r.annualized_pct).toFixed(1)}%`}</td>
        <td class="ctr">${pill(r.liq || DASH, LIQ_PILL[r.liq] || "neutral")}</td>
      </tr>`,
    )
    .join("");
  return tableShell(
    "Covered Calls — income",
    "OTM call 0.15–0.40Δ, 7–30 DTE, max annualized yield against shares",
    head,
    body,
    "No qualifying covered-call contracts found.",
  );
}

function summaryStrip(summary) {
  const s = summary || {};
  const avg = s.avg_composite === null || s.avg_composite === undefined ? DASH : fmtSigned(s.avg_composite, 2, "");
  const items = [
    ["Positions", s.positions ?? DASH],
    ["Bullish", s.bullish ?? DASH],
    ["Neutral", s.neutral ?? DASH],
    ["Bearish", s.bearish ?? DASH],
    ["Avg composite", avg],
    ["Vol signals", s.vol_signals ?? DASH],
  ];
  return `<div class="intel-summary-strip">${items
    .map(
      ([label, value]) =>
        `<span class="intel-summary-kpi"><span class="intel-summary-label">${safeText(label)}</span><strong class="mono-nums">${safeText(String(value))}</strong></span>`,
    )
    .join("")}</div>`;
}

function footnote(payload) {
  const g = payload?.garch_params || {};
  const params =
    g.alpha !== null && g.alpha !== undefined && g.beta !== null && g.beta !== undefined
      ? `GARCH(1,1) α=${g.alpha} β=${g.beta}`
      : "GARCH(1,1) (EWMA fallback where fit unavailable)";
  return `<p class="muted small intel-footnote mono-nums">${safeText(
    `${params} · BS lognormal, zero drift · ~0.40Δ target · EDGE = (GARCH vol − market IV) / IV · cache 5 min · Refresh forces recompute`,
  )}</p>`;
}

function renderMeta(meta) {
  const el = document.getElementById("intelMeta");
  if (!el) return;
  const m = meta || {};
  const parts = [];
  if (m.cache_hit && Number.isFinite(m.cache_age_sec)) {
    const mins = Math.floor(m.cache_age_sec / 60);
    parts.push(mins > 0 ? `cache ${mins}m` : "cache <1m");
  } else if (m.computed_at) {
    parts.push("fresh");
  }
  if (m.positions_total !== null && m.positions_total !== undefined) parts.push(`${m.positions_total} positions`);
  el.textContent = parts.join(" · ");
}

function renderDataQuality(notes, elId = "intelDataQuality") {
  const el = document.getElementById(elId);
  if (!el) return;
  const list = Array.isArray(notes) ? notes.filter(Boolean) : [];
  if (!list.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  // Surface degraded-data notes verbatim — never soften or omit.
  el.classList.remove("hidden");
  el.innerHTML = `<div class="operator-alert operator-alert--neutral intel-dq-alert" role="status">
    <div class="operator-alert__body">
      <strong class="operator-alert__headline">Partial data (${list.length})</strong>
      <ul class="intel-dq-list">${list.map((n) => `<li>${safeText(n)}</li>`).join("")}</ul>
    </div>
  </div>`;
}

function renderTablesHtml(payload, { includeSummary = true } = {}) {
  const parts = [];
  if (includeSummary) parts.push(summaryStrip(payload?.summary));
  parts.push(
    convictionTable(payload?.conviction),
    volatilityTable(payload?.volatility),
    optionsTable(payload?.options_setup),
    coveredTable(payload?.covered_calls),
    footnote(payload),
  );
  return parts.join("");
}

function renderPayload(payload) {
  const body = document.getElementById("intelBody");
  if (!body) return;
  const conviction = payload?.conviction || [];
  const volatility = payload?.volatility || [];
  if (!conviction.length && !volatility.length) {
    setAsyncState(body, ASYNC_EMPTY, {
      headline: "No open positions",
      message: "Link Schwab and hold at least one equity position to see Intel tables — or look up any ticker above.",
    });
    renderMeta(payload?.meta);
    renderDataQuality(payload?.data_quality);
    return;
  }
  body.setAttribute("data-async-state", ASYNC_SUCCESS);
  body.innerHTML = renderTablesHtml(payload, { includeSummary: true });
  renderMeta(payload?.meta);
  renderDataQuality(payload?.data_quality);
}

function normalizeLookupTicker(raw) {
  const t = String(raw || "").trim().toUpperCase();
  return TICKER_RE.test(t) ? t : null;
}

function openLookupDialog() {
  const dlg = document.getElementById("intelLookupDialog");
  if (!dlg) return;
  if (typeof dlg.showModal === "function" && !dlg.open) dlg.showModal();
}

function closeLookupDialog() {
  const dlg = document.getElementById("intelLookupDialog");
  if (dlg?.open) dlg.close();
}

function renderLookupPayload(payload) {
  const body = document.getElementById("intelLookupBody");
  const title = document.getElementById("intelLookupTitle");
  const sub = document.getElementById("intelLookupSub");
  if (!body) return;
  const meta = payload?.meta || {};
  const ticker = meta.ticker || payload?.conviction?.[0]?.ticker || "Ticker";
  if (title) title.textContent = `${ticker} intel`;
  if (sub) {
    const bits = [];
    if (meta.held) bits.push(meta.shares ? `held · ${meta.shares} shares` : "held");
    else bits.push("not held · research lookup");
    if (meta.cache_hit && Number.isFinite(meta.cache_age_sec)) {
      const mins = Math.floor(meta.cache_age_sec / 60);
      bits.push(mins > 0 ? `cache ${mins}m` : "cache <1m");
    } else {
      bits.push("fresh");
    }
    sub.textContent = bits.join(" · ");
  }
  body.setAttribute("data-async-state", ASYNC_SUCCESS);
  body.innerHTML = renderTablesHtml(payload, { includeSummary: false });
  renderDataQuality(payload?.data_quality, "intelLookupQuality");
}

export async function lookupTickerIntel(rawTicker, { refresh = false } = {}) {
  const ticker = normalizeLookupTicker(rawTicker);
  const body = document.getElementById("intelLookupBody");
  const input = document.getElementById("intelTickerInput");
  if (!ticker) {
    logEvent({ kind: "system", severity: "warn", message: "Intel lookup: invalid ticker" });
    if (input) {
      input.focus();
      input.select?.();
    }
    openLookupDialog();
    if (body) {
      setAsyncState(body, ASYNC_ERROR, {
        headline: "Invalid ticker",
        message: "Use 1–10 letters/digits (e.g. AAPL, BRK.B).",
      });
    }
    return;
  }
  if (_lookupLoading) return;
  _lookupLoading = true;
  if (input) input.value = ticker;
  openLookupDialog();
  const btn = document.getElementById("intelLookupBtn");
  if (btn) btn.disabled = true;
  if (body) {
    setAsyncState(body, ASYNC_LOADING, {
      message: `Computing ${ticker} — indicators, GARCH, and options chain…`,
    });
  }
  const title = document.getElementById("intelLookupTitle");
  const sub = document.getElementById("intelLookupSub");
  if (title) title.textContent = `${ticker} intel`;
  if (sub) sub.textContent = "loading…";
  renderDataQuality([], "intelLookupQuality");
  try {
    const qs = new URLSearchParams({ ticker });
    if (refresh) qs.set("refresh", "1");
    const out = await api.get(`/api/position-intel/lookup?${qs.toString()}`);
    if (!out.ok) {
      const msg = out.user_message || out.error || "Request failed.";
      logEvent({ kind: "system", severity: "warn", message: `Intel lookup failed (${ticker}): ${msg}` });
      if (out.status === 401) {
        setAsyncState(body, ASYNC_SIGNED_OUT, { message: "Sign in to look up ticker intel." });
        return;
      }
      setAsyncState(body, ASYNC_ERROR, {
        headline: `${ticker} unavailable`,
        message: msg,
        onRetry: () => void lookupTickerIntel(ticker, { refresh: true }),
      });
      return;
    }
    renderLookupPayload(out.data || {});
  } finally {
    _lookupLoading = false;
    if (btn) btn.disabled = false;
  }
}

export async function loadPositionIntel({ refresh = false } = {}) {
  const body = document.getElementById("intelBody");
  if (!body || _loading) return;
  _loading = true;
  const btn = document.getElementById("intelRefreshBtn");
  if (btn) btn.disabled = true;
  setAsyncState(body, ASYNC_LOADING, {
    message: refresh
      ? "Recomputing — indicators, GARCH fits, and option chains (~10s)…"
      : "Loading position intel — first compute can take ~10s…",
  });
  try {
    const out = await api.get(`/api/position-intel${refresh ? "?refresh=1" : ""}`);
    if (!out.ok) {
      const msg = out.user_message || out.error || "Request failed.";
      logEvent({ kind: "system", severity: "warn", message: `Position intel load failed: ${msg}` });
      if (out.status === 401) {
        setAsyncState(body, ASYNC_SIGNED_OUT, { message: "Sign in to load position intel." });
        return;
      }
      setAsyncState(body, ASYNC_ERROR, {
        headline: "Position intel unavailable",
        message: msg,
        onRetry: () => void loadPositionIntel({ refresh }),
      });
      return;
    }
    renderPayload(out.data || {});
  } finally {
    _loading = false;
    if (btn) btn.disabled = false;
  }
}

function wireLookupControls() {
  const input = document.getElementById("intelTickerInput");
  const btn = document.getElementById("intelLookupBtn");
  const closeBtn = document.getElementById("intelLookupCloseBtn");
  const dlg = document.getElementById("intelLookupDialog");

  btn?.addEventListener("click", () => {
    void lookupTickerIntel(input?.value || "");
  });
  input?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void lookupTickerIntel(input.value || "");
    }
  });
  closeBtn?.addEventListener("click", () => closeLookupDialog());
  dlg?.addEventListener("click", (e) => {
    // Click on the backdrop (dialog itself) closes — content sits in .modal-stack.
    if (e.target === dlg) closeLookupDialog();
  });

  // Delegate ticker clicks from both the main tables and the lookup modal.
  document.addEventListener("click", (e) => {
    const target = e.target;
    if (!(target instanceof Element)) return;
    const hit = target.closest("[data-intel-ticker]");
    if (!hit) return;
    const ticker = hit.getAttribute("data-intel-ticker");
    if (!ticker) return;
    e.preventDefault();
    void lookupTickerIntel(ticker);
  });
}

export function initPositionIntelPanel() {
  document.getElementById("intelRefreshBtn")?.addEventListener("click", () => {
    void loadPositionIntel({ refresh: true });
  });
  wireLookupControls();
}

export async function primePositionIntelPanel() {
  // Server holds the 5-min cache; re-priming after first load is a cheap
  // cache hit, so always refetch on screen entry to keep the meta line honest.
  if (_loading) return;
  await loadPositionIntel({ refresh: false });
}
