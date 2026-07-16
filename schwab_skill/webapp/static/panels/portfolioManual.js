/**
 * Manual portfolio panel: ticker/shares grid for non-Schwab (or what-if) books.
 *
 * The book lives only in this browser (`localStorage`) — no server-side
 * persistence. "Update prices" POSTs to the public `/api/portfolio/manual/*`
 * endpoints, which price each row from daily history (Schwab → yfinance
 * fallback) and fail closed when any ticker cannot be priced.
 *
 * `panels/portfolio.js` delegates here when the source toggle is Manual;
 * `panels/portfolioRisk.js` pulls `getManualPayload()` to build the same
 * risk dashboard pack from the manual book.
 */

import { api } from "../modules/api.js";
import { safeText, escapeHtml, formatMoney, formatDecimal } from "../modules/format.js";

const STORAGE_KEY = "manualPortfolio.v1";
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

export function loadManualBook() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    const positions = Array.isArray(raw.positions)
      ? raw.positions
          .map((p) => ({ ticker: String(p.ticker || "").toUpperCase().trim(), qty: Number(p.qty) }))
          .slice(0, MAX_MANUAL_ROWS)
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

/** Payload for the manual risk-dashboard endpoint; null when no valid rows. */
export function getManualPayload() {
  const book = readBookFromEditor() || loadManualBook();
  const positions = book.positions.filter((p) => p.ticker && Number.isFinite(p.qty) && p.qty > 0);
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
    const ticker = String(tr.querySelector("[data-manual-ticker]")?.value || "").toUpperCase().trim();
    const qty = Number(tr.querySelector("[data-manual-qty]")?.value);
    if (ticker || Number.isFinite(qty)) positions.push({ ticker, qty });
  });
  const cash = Number(document.getElementById("manualCashInput")?.value);
  return { positions, cash: Number.isFinite(cash) && cash >= 0 ? cash : null };
}

function persistEditor() {
  const book = readBookFromEditor();
  if (book) saveManualBook(book);
}

function editorRowHtml(pos = { ticker: "", qty: "" }) {
  return `
    <tr data-manual-row>
      <td><input type="text" data-manual-ticker maxlength="16" placeholder="AAPL"
        autocapitalize="characters" autocomplete="off" spellcheck="false"
        value="${escapeHtml(pos.ticker || "")}" aria-label="Ticker symbol"></td>
      <td><input type="number" data-manual-qty min="0" step="any" placeholder="100"
        value="${Number.isFinite(Number(pos.qty)) && pos.qty !== "" ? Number(pos.qty) : ""}" aria-label="Share count"></td>
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
  (book.positions.length ? book.positions : [{ ticker: "", qty: "" }]).forEach((p) => addEditorRow(p));
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
        <td class="mono-nums">${formatMoney(p.last)}</td>
        <td class="mono-nums">${formatMoney(p.market_value)}</td>
        <td class="mono-nums">${p.weight_pct != null ? `${formatDecimal(p.weight_pct, 1)}%` : "—"}</td>
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
        <td class="mono-nums">${formatMoney(cash)}</td>
        <td class="mono-nums">${weight != null ? `${formatDecimal(weight, 1)}%` : "—"}</td>
      </tr>`;
  }
  document.getElementById("manualPortfolioSnapshotWrap")?.classList.remove("hidden");
}

let pricingInFlight = false;

export async function priceManualPortfolio() {
  if (pricingInFlight) return null;
  const payload = getManualPayload();
  if (!payload) {
    setManualStatus("Add at least one ticker with a positive share count.", "bad");
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
