/**
 * Unified slide-in trade drawer.
 *
 * Replaces the previous trio of single-purpose panels:
 *   - panels/quickView.js       (slide-in decision card for a pending trade)
 *   - panels/decisionCard.js    (in-page decision card form + render)
 *   - panels/recovery.js        (in-page failure-recovery form + render)
 *
 * The drawer hosts two tabs — Decision and Recovery — that share the
 * same surface so any "explain this trade" or "explain this error"
 * entry point opens the same UI. The drawer participates in keyboard
 * focus management (Esc closes, backdrop click closes) and exposes a
 * tiny imperative API (`openTradeDrawer`, `openTradeDrawerForTrade`,
 * `closeTradeDrawer`) so call sites in app.js and commandPalette.js can
 * use it without owning the DOM.
 *
 * DOM contract — the drawer expects these IDs in index.html:
 *   #tradeDrawer, #tradeDrawerCloseBtn, #tradeDrawerBackdrop,
 *   #tradeDrawerTabDecision, #tradeDrawerTabRecovery,
 *   #tradeDrawerPanelDecision, #tradeDrawerPanelRecovery,
 *   #tradeDrawerDecisionTicker, #tradeDrawerDecisionBtn,
 *   #tradeDrawerDecisionPlaceholder, #tradeDrawerDecisionSummary,
 *   #tradeDrawerDecisionJsonDetails, #tradeDrawerDecisionOutput,
 *   #tradeDrawerRecoverySource, #tradeDrawerRecoveryMessage,
 *   #tradeDrawerRecoveryBtn, #tradeDrawerRecoveryPlaceholder,
 *   #tradeDrawerRecoverySummary, #tradeDrawerRecoveryJsonDetails,
 *   #tradeDrawerRecoveryOutput.
 */

import { api } from "../modules/api.js";
import { state } from "../modules/state.js";
import { safeText, escapeHtml, formatMoney } from "../modules/format.js";
import { buildOperatorAlertHtml } from "../modules/asyncState.js";
import {
  buildTicketModel,
  rowStageBlockers,
  systemStageBlockers,
} from "../modules/ticketFacts.js";

const TABS = ["decision", "recovery"];
let _wired = false;
/** Element that had focus before the drawer opened; restored on close. */
let _returnFocusEl = null;
/** Pending row that opened the drawer, used to keep its qty and limit. */
let _ticketRow = null;

function $(id) {
  return document.getElementById(id);
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function drawerFocusables(drawer) {
  return Array.from(drawer.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

/**
 * Move focus into the drawer once the slide-in has rendered. Double-rAF so
 * the element is focusable even while the open transition is still running.
 */
function focusDrawerField(id) {
  const raf = window.requestAnimationFrame || ((cb) => setTimeout(cb, 0));
  raf(() =>
    raf(() => {
      const el = $(id);
      if (el) el.focus();
      else $("tradeDrawerCloseBtn")?.focus();
    }),
  );
}

function setActiveTab(tab) {
  if (!TABS.includes(tab)) tab = "decision";
  for (const t of TABS) {
    const btn = $(`tradeDrawerTab${t === "decision" ? "Decision" : "Recovery"}`);
    const panel = $(`tradeDrawerPanel${t === "decision" ? "Decision" : "Recovery"}`);
    const active = t === tab;
    if (btn) {
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
      btn.tabIndex = active ? 0 : -1;
    }
    if (panel) panel.classList.toggle("hidden", !active);
  }
}

function bookEquity() {
  const portfolio = state.lastPortfolioData;
  const risk = state.lastPortfolioRiskData;
  const candidates = [portfolio?.total_market_value, portfolio?.total_value, risk?.total_value];
  for (const value of candidates) {
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return null;
}

function bookPositions() {
  const risk = state.lastPortfolioRiskData;
  if (Array.isArray(risk?.positions_weighted) && risk.positions_weighted.length) {
    return risk.positions_weighted;
  }
  const dash = state.lastPortfolioRiskDashboard;
  if (Array.isArray(dash?.positions_weighted) && dash.positions_weighted.length) {
    return dash.positions_weighted;
  }
  const portfolio = state.lastPortfolioData;
  if (Array.isArray(portfolio?.positions)) return portfolio.positions;
  return [];
}

function stageSnapshot() {
  return {
    killSwitch: Boolean(state.publicConfig?.platform_live_trading_kill_switch),
    tradingHalted: Boolean(state.accountMe?.trading_halted),
    liveOff: Boolean(
      state.publicConfig?.saas_mode &&
        (!state.accountMe || !state.accountMe.live_execution_enabled),
    ),
  };
}

function moneyOrDash(value) {
  return value === null || value === undefined ? "—" : formatMoney(value);
}

function factTone(kind, model) {
  if (kind === "status") return model.clear ? "ok" : "bad";
  if (kind === "guardrail") return model.guardrail ? "bad" : "neutral";
  if (kind === "quality") {
    const dq = String(model.dataQuality || "").toLowerCase();
    if (!dq) return "neutral";
    if (dq === "ok") return "ok";
    if (dq === "degraded" || dq === "partial" || dq === "warn" || dq === "warning") return "warn";
    return "bad";
  }
  if (kind === "book") {
    if (model.book === "Yes") return "warn";
    if (model.book === "No") return "ok";
    return "neutral";
  }
  return "neutral";
}

function factCell(label, value, sub, tone, wide) {
  return `<div class="ticket-fact${wide ? " ticket-fact--wide" : ""}" data-tone="${tone}">
    <span class="ticket-fact-label">${escapeHtml(label)}</span>
    <strong class="ticket-fact-value">${escapeHtml(value)}</strong>
    ${sub ? `<small class="ticket-fact-sub">${escapeHtml(sub)}</small>` : ""}
  </div>`;
}

/**
 * One row of ticket facts. `idPrefix` stays `tradeDrawerDecision` for the drawer.
 */
function renderDecisionInto(idPrefix, data, error, opts = {}) {
  const ph = $(`${idPrefix}Placeholder`);
  const sum = $(`${idPrefix}Summary`);
  if (error) {
    if (ph) {
      ph.innerHTML = buildOperatorAlertHtml({
        tone: "bad",
        headline: "Data unavailable",
        detail: error,
        retry: Boolean(opts.retryAttr),
        retryAttr: opts.retryAttr,
      });
      ph.classList.remove("hidden");
    }
    if (sum) {
      sum.classList.add("hidden");
      sum.innerHTML = "";
    }
    return;
  }
  if (ph) ph.classList.add("hidden");
  const card = data || {};
  const row = opts.pendingRow || null;
  const model = buildTicketModel({
    card,
    row,
    equity: bookEquity(),
    positions: bookPositions(),
    blockers: rowStageBlockers(row || { signal: {} }),
    systemBlockers: systemStageBlockers(stageSnapshot()),
  });
  const qtyText = model.qty === null ? "—" : String(model.qty);
  if (sum) {
    sum.classList.remove("hidden");
    sum.innerHTML = `
      <h4 class="tool-summary-title">${escapeHtml(safeText(card.ticker || row?.ticker || ""))}</h4>
      <div class="ticket-facts" aria-label="Trade ticket">
        ${factCell("Side", model.side, "", "neutral", false)}
        ${factCell("Qty", qtyText, "shares", "neutral", false)}
        ${factCell("Limit", moneyOrDash(model.limit), "entry", "neutral", false)}
        ${factCell("Stop", moneyOrDash(model.stop), "invalidation", "neutral", false)}
        ${factCell("R", moneyOrDash(model.r), model.rFormula, model.r === null ? "neutral" : "ok", false)}
        ${factCell("Heat", model.heat, "R percent of equity · open risk", "neutral", false)}
        ${factCell("Sector", model.sector || "—", "", "neutral", false)}
        ${factCell("Book", model.book || "—", "sector already held", factTone("book", model), false)}
        ${factCell("Data quality", model.dataQuality || "—", "", factTone("quality", model), false)}
        ${factCell("Guardrail", model.guardrail || "—", "", factTone("guardrail", model), true)}
        ${factCell("Status", model.status, model.clear ? "" : "approve off", factTone("status", model), true)}
      </div>
    `;
  }
}

function renderRecoveryInto(idPrefix, data, error, opts = {}) {
  const ph = $(`${idPrefix}Placeholder`);
  const sum = $(`${idPrefix}Summary`);
  if (error) {
    if (ph) {
      ph.innerHTML = buildOperatorAlertHtml({
        tone: "bad",
        headline: "Data unavailable",
        detail: error,
        retry: Boolean(opts.retryAttr),
        retryAttr: opts.retryAttr,
      });
      ph.classList.remove("hidden");
    }
    if (sum) {
      sum.classList.add("hidden");
      sum.innerHTML = "";
    }
    return;
  }
  if (ph) ph.classList.add("hidden");
  if (sum) {
    sum.classList.remove("hidden");
    sum.innerHTML = `
      <h4 class="tool-summary-title">${safeText(data.title)}</h4>
      <p class="tool-summary-p">${safeText(data.summary)}</p>
      <p class="tool-summary-next"><strong>Next step:</strong> ${safeText(data.fix_path)}</p>
    `;
  }
}

async function fetchDecision(ticker) {
  const sym = ticker.toUpperCase().trim();
  if (!sym) return { ok: false, error: "Enter a ticker first." };
  return api.get(`/api/decision-card/${encodeURIComponent(sym)}`);
}

async function fetchRecovery(source, message) {
  const msg = (message || "").trim();
  if (!msg) return { ok: false, error: "Paste an error message first." };
  return api.get(
    `/api/recovery/map?source=${encodeURIComponent(source)}&error=${encodeURIComponent(msg)}`,
  );
}

/** Show a plain loading line in the placeholder (not the error alert). */
function renderDrawerLoading(idPrefix, text) {
  const ph = $(`${idPrefix}Placeholder`);
  if (ph) {
    ph.innerHTML = `<span class="async-state async-state--loading muted" role="status">
      <span class="async-spinner" aria-hidden="true"></span>
      <span>${safeText(text)}</span>
    </span>`;
    ph.classList.remove("hidden");
  }
  const sum = $(`${idPrefix}Summary`);
  if (sum) sum.classList.add("hidden");
}

function wireDrawerRetry(idPrefix, attr, retry) {
  $(`${idPrefix}Placeholder`)
    ?.querySelector(`[${attr}]`)
    ?.addEventListener("click", () => void retry());
}

/** Run the Decision lookup driven by the drawer's own ticker input. */
export async function loadDecisionInDrawer() {
  const inputEl = $("tradeDrawerDecisionTicker");
  const ticker = inputEl ? inputEl.value : "";
  renderDrawerLoading("tradeDrawerDecision", "Loading decision card…");
  const out = await fetchDecision(ticker);
  if (!out.ok) {
    renderDecisionInto("tradeDrawerDecision", null, `Decision card failed: ${out.error}`, {
      retryAttr: "data-drawer-decision-retry",
    });
    wireDrawerRetry("tradeDrawerDecision", "data-drawer-decision-retry", loadDecisionInDrawer);
    return;
  }
  const sym = ticker.toUpperCase().trim();
  const pendingRow =
    _ticketRow && String(_ticketRow.ticker || "").toUpperCase().trim() === sym ? _ticketRow : null;
  renderDecisionInto("tradeDrawerDecision", out.data, null, { pendingRow });
}

/** Run the Recovery lookup driven by the drawer's own inputs. */
export async function loadRecoveryInDrawer() {
  const sourceEl = $("tradeDrawerRecoverySource");
  const messageEl = $("tradeDrawerRecoveryMessage");
  const source = sourceEl ? sourceEl.value : "schwab_auth";
  const message = messageEl ? messageEl.value : "";
  renderDrawerLoading("tradeDrawerRecovery", "Mapping recovery…");
  const out = await fetchRecovery(source, message);
  if (!out.ok) {
    renderRecoveryInto("tradeDrawerRecovery", null, `Recovery mapping failed: ${out.error}`, {
      retryAttr: "data-drawer-recovery-retry",
    });
    wireDrawerRetry("tradeDrawerRecovery", "data-drawer-recovery-retry", loadRecoveryInDrawer);
    return;
  }
  renderRecoveryInto("tradeDrawerRecovery", out.data, null);
}

/**
 * Open the drawer to a specific tab and optionally prefill inputs.
 *
 * @param {object} [opts]
 * @param {"decision"|"recovery"} [opts.tab="decision"]
 * @param {string} [opts.ticker]            – prefill + auto-load the decision card
 * @param {string} [opts.recoverySource]    – prefill source dropdown
 * @param {string} [opts.recoveryMessage]   – prefill error message + auto-map
 */
export function openTradeDrawer(opts = {}) {
  const drawer = $("tradeDrawer");
  if (!drawer) return;
  const backdrop = $("tradeDrawerBackdrop");
  ensureWired();
  _returnFocusEl =
    document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const tab = opts.tab && TABS.includes(opts.tab) ? opts.tab : "decision";
  _ticketRow = opts.pendingRow || null;
  setActiveTab(tab);
  drawer.classList.add("open");
  drawer.removeAttribute("hidden");
  backdrop?.removeAttribute("hidden");
  document.body.classList.add("trade-drawer-open");
  if (tab === "decision") {
    if (opts.ticker) {
      const inputEl = $("tradeDrawerDecisionTicker");
      if (inputEl) inputEl.value = String(opts.ticker).toUpperCase();
      void loadDecisionInDrawer();
    }
    focusDrawerField("tradeDrawerDecisionTicker");
  } else {
    if (opts.recoverySource) {
      const sel = $("tradeDrawerRecoverySource");
      if (sel) sel.value = opts.recoverySource;
    }
    if (opts.recoveryMessage) {
      const msg = $("tradeDrawerRecoveryMessage");
      if (msg) msg.value = opts.recoveryMessage;
      void loadRecoveryInDrawer();
    }
    focusDrawerField("tradeDrawerRecoveryMessage");
  }
}

/** Backwards-compatible entry point used by the pending-trade Quick View row action. */
export async function openTradeDrawerForTrade(row) {
  if (!row || !row.ticker) return;
  openTradeDrawer({ tab: "decision", ticker: row.ticker, pendingRow: row });
}

export function closeTradeDrawer() {
  const drawer = $("tradeDrawer");
  const backdrop = $("tradeDrawerBackdrop");
  if (!drawer) return;
  drawer.classList.remove("open");
  backdrop?.setAttribute("hidden", "");
  // Hide after the slide animation so screen readers don't announce it.
  setTimeout(() => {
    if (!drawer.classList.contains("open")) drawer.setAttribute("hidden", "");
  }, 220);
  document.body.classList.remove("trade-drawer-open");
  // Return focus to whatever opened the drawer (WCAG 2.4.3).
  if (_returnFocusEl?.isConnected) _returnFocusEl.focus();
  _returnFocusEl = null;
}

/**
 * Wire the drawer's internal events. Called once on first open so the
 * legacy in-page sections that delegate into the drawer don't have to
 * pay the cost of binding listeners up front.
 */
function ensureWired() {
  if (_wired) return;
  _wired = true;

  const closeBtn = $("tradeDrawerCloseBtn");
  closeBtn?.addEventListener("click", () => closeTradeDrawer());

  const backdrop = $("tradeDrawerBackdrop");
  backdrop?.addEventListener("click", () => closeTradeDrawer());

  const decisionBtn = $("tradeDrawerDecisionBtn");
  decisionBtn?.addEventListener("click", () => void loadDecisionInDrawer());
  const decisionInput = $("tradeDrawerDecisionTicker");
  decisionInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void loadDecisionInDrawer();
    }
  });

  const recoveryBtn = $("tradeDrawerRecoveryBtn");
  recoveryBtn?.addEventListener("click", () => void loadRecoveryInDrawer());
  const recoveryInput = $("tradeDrawerRecoveryMessage");
  recoveryInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void loadRecoveryInDrawer();
    }
  });

  for (const t of TABS) {
    const btn = $(`tradeDrawerTab${t === "decision" ? "Decision" : "Recovery"}`);
    btn?.addEventListener("click", () => setActiveTab(t));
    // Standard tablist arrow-key pattern between the two tabs.
    btn?.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const other = t === "decision" ? "recovery" : "decision";
      setActiveTab(other);
      $(`tradeDrawerTab${other === "decision" ? "Decision" : "Recovery"}`)?.focus();
    });
  }

  // Esc closes when the drawer is open.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const drawer = $("tradeDrawer");
    if (drawer?.classList.contains("open")) {
      e.preventDefault();
      closeTradeDrawer();
    }
  });

  // Trap Tab inside the open drawer so keyboard focus can't wander into
  // the page behind it (WCAG 2.4.3 / dialog pattern).
  const drawerEl = $("tradeDrawer");
  drawerEl?.addEventListener("keydown", (e) => {
    if (e.key !== "Tab" || !drawerEl.classList.contains("open")) return;
    const focusables = drawerFocusables(drawerEl);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });
}

// Legacy adapters --------------------------------------------------------
//
// The in-page "Decision Card" and "Failure Recovery" sections still
// exist as thin landing cards with a CTA that opens the drawer. We keep
// these named exports so the small footprint of legacy event handlers
// in app.js doesn't have to know about the drawer's internals.

/** Open the drawer's Decision tab; ignored arguments preserved for compat. */
export function loadDecisionCard() {
  // Older code path read #decisionTickerInput; honour it if present so
  // existing keyboard shortcuts still pre-fill the lookup.
  const legacyInput = $("decisionTickerInput");
  const ticker = legacyInput?.value?.trim();
  openTradeDrawer({ tab: "decision", ticker });
}

/** Open the drawer's Recovery tab; ignored arguments preserved for compat. */
export function mapRecovery() {
  const legacySource = $("recoverySource");
  const legacyMessage = $("recoveryMessage");
  openTradeDrawer({
    tab: "recovery",
    recoverySource: legacySource?.value,
    recoveryMessage: legacyMessage?.value?.trim(),
  });
}
