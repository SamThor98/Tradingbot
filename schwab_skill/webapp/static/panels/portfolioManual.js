/**
 * Manual portfolio panel: ticker/shares/acquired/avg-cost grid for non-Schwab books.
 *
 * The book lives only in this browser (`localStorage`) — no server-side
 * persistence. "Update prices" POSTs to the public `/api/portfolio/manual/*`
 * endpoints, which price each row from daily history (Schwab → yfinance
 * fallback) and fail closed when any ticker cannot be priced.
 *
 * `panels/portfolio.js` delegates here when the source toggle is Manual;
 * `panels/portfolioRisk.js` pulls `getManualPayload()` to build the same
 * risk dashboard pack from the manual book (ownership-period returns).
 */

import { api } from "../modules/api.js";
import { safeText, escapeHtml, formatMoney, formatDecimal } from "../modules/format.js";

const STORAGE_KEY = "manualPortfolio.v2";
const STORAGE_KEY_V1 = "manualPortfolio.v1";
const SOURCE_KEY = "portfolioSource.v1";
export const MAX_MANUAL_ROWS = 15;

/* ── Storage ───────────────────────────────────────────────────── */

export function getPortfolioSource() {
  try {
    return localStorage.getItem(SOURCE_KEY) === "manual" ? "manual" : "schwab";
  } catch {
    return "schwab";
  }
}

export function setPortfolioSource(source) {
  try {
    localStorage.setItem(SOURCE_KEY, source === "manual" ? "manual" : "schwab");
  } catch {
    /* storage unavailable — toggle still works for the session */
  }
}

function normalizePosition(p) {
  const ticker = String(p?.ticker || "").toUpperCase().trim();
  const qty = Number(p?.qty);
  const acquired_at = String(p?.acquired_at || "").trim();
  const avg_cost = Number(p?.avg_cost);
  return {
    ticker,
    qty: Number.isFinite(qty) ? qty : "",
    acquired_at: /^\d{4}-\d{2}-\d{2}$/.test(acquired_at) ? acquired_at : "",
    avg_cost: Number.isFinite(avg_cost) && avg_cost > 0 ? avg_cost : "",
  };
}

export function loadManualBook() {
  try {
    let raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (!raw) {
      const v1 = JSON.parse(localStorage.getItem(STORAGE_KEY_V1) || "{}");
      raw = v1 && typeof v1 === "object" ? v1 : {};
    }
    const positions = Array.isArray(raw.positions)
      ? raw.positions.map(normalizePosition).slice(0, MAX_MANUAL_ROWS)
      : [];
    const cash = Number(raw.cash);
    return { positions, cash: Number.isFinite(cash) && cash >= 0 ? cash : null };
  } catch {
    return { positions: [], cash: null };
  }
}

function saveManualBook(book) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(book));
  } catch {
    /* storage unavailable — edits stay in the DOM for the session */
  }
}

function rowIsComplete(p) {
  return (
    p.ticker &&
    Number.isFinite(Number(p.qty)) &&
    Number(p.qty) > 0 &&
    /^\d{4}-\d{2}-\d{2}$/.test(String(p.acquired_at || "")) &&
    Number.isFinite(Number(p.avg_cost)) &&
    Number(p.avg_cost) > 0
  );
}

/** Payload for the manual risk-dashboard endpoint; null when no valid rows. */
export function getManualPayload() {
  const book = readBookFromEditor() || loadManualBook();
  const positions = book.positions
    .map(normalizePosition)
    .filter(rowIsComplete)
    .map((p) => ({
      ticker: p.ticker,
      qty: Number(p.qty),
      acquired_at: p.acquired_at,
      avg_cost: Number(p.avg_cost),
    }));
  if (!positions.length) return null;
  const payload = { positions };
  if (Number.isFinite(book.cash) && book.cash > 0) payload.cash = book.cash;
  return payload;
}

/* ── Editor grid ───────────────────────────────────────────────── */

function readBookFromEditor() {
  const rowsEl = document.getElementById("manualPortfolioRows");
  if (!rowsEl) return null;
  const positions = [];
  rowsEl.querySelectorAll("tr[data-manual-row]").forEach((tr) => {
    const ticker = String(tr.querySelector("[data-manual-ticker]")?.value || "")
      .toUpperCase()
      .trim();
    const qty = Number(tr.querySelector("[data-manual-qty]")?.value);
    const acquired_at = String(tr.querySelector("[data-manual-acquired]")?.value || "").trim();
    const avg_cost = Number(tr.querySelector("[data-manual-avg-cost]")?.value);
    if (ticker || Number.isFinite(qty) || acquired_at || Number.isFinite(avg_cost)) {
      positions.push({
        ticker,
        qty: Number.isFinite(qty) ? qty : "",
        acquired_at,
        avg_cost: Number.isFinite(avg_cost) ? avg_cost : "",
      });
    }
  });
  const cash = Number(document.getElementById("manualCashInput")?.value);
  return { positions, cash: Number.isFinite(cash) && cash >= 0 ? cash : null };
}

function persistEditor() {
  const book = readBookFromEditor();
  if (book) saveManualBook(book);
}

function editorRowHtml(pos = { ticker: "", qty: "", acquired_at: "", avg_cost: "" }) {
  const qtyVal = Number.isFinite(Number(pos.qty)) && pos.qty !== "" ? Number(pos.qty) : "";
  const costVal =
    Number.isFinite(Number(pos.avg_cost)) && pos.avg_cost !== "" ? Number(pos.avg_cost) : "";
  return `
    <tr data-manual-row>
      <td><input type="text" data-manual-ticker maxlength="16" placeholder="AAPL"
        autocapitalize="characters" autocomplete="off" spellcheck="false"
        value="${escapeHtml(pos.ticker || "")}" aria-label="Ticker symbol"></td>
      <td><input type="number" data-manual-qty min="0" step="any" placeholder="100"
        value="${qtyVal}" aria-label="Share count"></td>
      <td><input type="date" data-manual-acquired min="1990-01-01"
        value="${escapeHtml(pos.acquired_at || "")}" aria-label="Ownership start date"></td>
      <td><input type="number" data-manual-avg-cost min="0" step="any" placeholder="150.00"
        value="${costVal}" aria-label="Average cost per share"></td>
      <td><button type="button" class="btn small secondary" data-manual-remove aria-label="Remove row">✕</button></td>
    </tr>`;
}

function wireEditorRow(tr) {
  tr.querySelectorAll("input").forEach((input) => input.addEventListener("change", persistEditor));
  tr.querySelector("[data-manual-remove]")?.addEventListener("click", () => {
    tr.remove();
    persistEditor();
    syncAddRowButton();
  });
}

function syncAddRowButton() {
  const btn = document.getElementById("manualAddRowBtn");
  if (!btn) return;
  const count = document.querySelectorAll("#manualPortfolioRows tr[data-manual-row]").length;
  btn.disabled = count >= MAX_MANUAL_ROWS;
  btn.textContent = count >= MAX_MANUAL_ROWS ? `Max ${MAX_MANUAL_ROWS} tickers` : "+ Add row";
}

function addEditorRow(pos) {
  const rowsEl = document.getElementById("manualPortfolioRows");
  if (!rowsEl) return;
  if (rowsEl.querySelectorAll("tr[data-manual-row]").length >= MAX_MANUAL_ROWS) return;
  const tpl = document.createElement("template");
  tpl.innerHTML = editorRowHtml(pos).trim();
  const tr = tpl.content.firstElementChild;
  rowsEl.appendChild(tr);
  wireEditorRow(tr);
  syncAddRowButton();
}

function renderEditorFromStorage() {
  const rowsEl = document.getElementById("manualPortfolioRows");
  if (!rowsEl) return;
  const book = loadManualBook();
  rowsEl.innerHTML = "";
  (book.positions.length ? book.positions : [{ ticker: "", qty: "", acquired_at: "", avg_cost: "" }]).forEach(
    (p) => addEditorRow(p),
  );
  const cashInput = document.getElementById("manualCashInput");
  if (cashInput) cashInput.value = book.cash == null ? "" : String(book.cash);
  syncAddRowButton();
}

/* ── Priced snapshot ───────────────────────────────────────────── */

function setManualStatus(text, tone) {
  const el = document.getElementById("manualPortfolioStatus");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = tone === "bad" ? "var(--bad)" : tone === "good" ? "var(--good)" : "";
}

function highlightUnpriced(unpriced) {
  const bad = new Set((unpriced || []).map((t) => String(t).toUpperCase()));
  document.querySelectorAll("#manualPortfolioRows [data-manual-ticker]").forEach((input) => {
    const isBad = bad.has(String(input.value || "").toUpperCase().trim());
    input.style.borderColor = isBad ? "var(--bad)" : "";
    input.setAttribute("aria-invalid", isBad ? "true" : "false");
  });
}

function incompleteRowMessage() {
  const book = readBookFromEditor() || loadManualBook();
  const started = book.positions.filter((p) => p.ticker || p.qty || p.acquired_at || p.avg_cost);
  if (!started.length) return "Add at least one ticker with shares, acquired date, and avg cost.";
  const missing = started.filter((p) => !rowIsComplete(normalizePosition(p)));
  if (!missing.length) return null;
  return "Each row needs ticker, positive shares, acquired date, and avg cost > 0.";
}

function renderSnapshot(data) {
  const body = document.getElementById("manualPortfolioBody");
  if (!body) return;
  const rows = Array.isArray(data.positions) ? data.positions : [];
  body.innerHTML = rows
    .map(
      (p) => `
      <tr>
        <td>${safeText(p.symbol)}</td>
        <td class="mono-nums">${safeText(String(p.qty))}</td>
        <td class="mono-nums">${formatMoney(p.avg_cost)}</td>
        <td class="mono-nums">${formatMoney(p.last)}</td>
        <td class="mono-nums">${formatMoney(p.market_value)}</td>
        <td class="mono-nums">${p.pl_pct != null ? `${formatDecimal(p.pl_pct, 2)}%` : "—"}</td>
        <td class="mono-nums">${p.weight_pct != null ? `${formatDecimal(p.weight_pct, 1)}%` : "—"}</td>
        <td class="mono-nums">${safeText(p.acquired_at || "—")}</td>
      </tr>`,
    )
    .join("");
  const cash = Number(data.cash) || 0;
  if (cash > 0) {
    const weight = Number(data.equity) > 0 ? (cash / Number(data.equity)) * 100 : null;
    body.innerHTML += `
      <tr>
        <td class="muted">Cash</td>
        <td class="muted">—</td>
        <td class="muted">—</td>
        <td class="muted">—</td>
        <td class="mono-nums">${formatMoney(cash)}</td>
        <td class="muted">—</td>
        <td class="mono-nums">${weight != null ? `${formatDecimal(weight, 1)}%` : "—"}</td>
        <td class="muted">—</td>
      </tr>`;
  }
  document.getElementById("manualPortfolioSnapshotWrap")?.classList.remove("hidden");
}

let pricingInFlight = false;

export async function priceManualPortfolio() {
  if (pricingInFlight) return null;
  const incomplete = incompleteRowMessage();
  if (incomplete) {
    setManualStatus(incomplete, "bad");
    return null;
  }
  const payload = getManualPayload();
  if (!payload) {
    setManualStatus("Add at least one ticker with shares, acquired date, and avg cost.", "bad");
    return null;
  }
  persistEditor();
  pricingInFlight = true;
  const btn = document.getElementById("manualPriceBtn");
  if (btn) btn.disabled = true;
  setManualStatus("Pricing book…");
  try {
    const out = await api.post("/api/portfolio/manual/positions", payload, { timeoutMs: 60000 });
    if (!out.ok) {
      highlightUnpriced(out.data?.unpriced_tickers);
      setManualStatus(safeText(out.error || "Pricing failed."), "bad");
      return null;
    }
    highlightUnpriced([]);
    const d = out.data || {};
    renderSnapshot(d);
    setManualStatus(
      `Priced ${d.positions_count} position(s) — equity ${formatMoney(d.equity)} (stocks ${formatMoney(d.total_market_value)}${Number(d.cash) > 0 ? ` + cash ${formatMoney(d.cash)}` : ""}).`,
      "good",
    );
    return d;
  } finally {
    pricingInFlight = false;
    if (btn) btn.disabled = false;
  }
}

/* ── Source toggle + panel visibility ──────────────────────────── */

export function applySourceVisibility() {
  const manual = getPortfolioSource() === "manual";
  document.getElementById("portfolioSourceSchwab")?.classList.toggle("tab-btn-active", !manual);
  document.getElementById("portfolioSourceSchwab")?.setAttribute("aria-pressed", manual ? "false" : "true");
  document.getElementById("portfolioSourceManual")?.classList.toggle("tab-btn-active", manual);
  document.getElementById("portfolioSourceManual")?.setAttribute("aria-pressed", manual ? "true" : "false");
  document.getElementById("manualPortfolioEditor")?.classList.toggle("hidden", !manual);
  document.getElementById("schwabPositionsWrap")?.classList.toggle("hidden", manual);
}

/** Render the manual editor into the Positions panel (called by portfolio.js). */
export function renderManualPositionsPanel() {
  applySourceVisibility();
  renderEditorFromStorage();
  const meta = document.getElementById("portfolioMeta");
  if (meta) {
    const book = loadManualBook();
    const n = book.positions.filter((p) => p.ticker).length;
    meta.textContent = n
      ? `Manual book: ${n} ticker(s) stored in this browser.`
      : "Manual book: add tickers below — stored only in this browser.";
  }
}

/**
 * One-time wiring for the source toggle and manual editor controls.
 * `onSourceChange` lets the caller reset risk caches and repaint panels.
 */
export function wirePortfolioSource(onSourceChange) {
  const select = (source) => {
    if (getPortfolioSource() === source) return;
    setPortfolioSource(source);
    applySourceVisibility();
    if (source === "manual") renderManualPositionsPanel();
    if (typeof onSourceChange === "function") onSourceChange(source);
  };
  document.getElementById("portfolioSourceSchwab")?.addEventListener("click", () => select("schwab"));
  document.getElementById("portfolioSourceManual")?.addEventListener("click", () => select("manual"));
  document.getElementById("manualAddRowBtn")?.addEventListener("click", () => addEditorRow());
  document.getElementById("manualCashInput")?.addEventListener("change", persistEditor);
  document.getElementById("manualPriceBtn")?.addEventListener("click", () => void priceManualPortfolio());
  applySourceVisibility();
  if (getPortfolioSource() === "manual") renderManualPositionsPanel();
}
