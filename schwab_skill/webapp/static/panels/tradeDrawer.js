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
import { safeText, safeNum, formatMoney, prettyJson } from "../modules/format.js";
import {
  formatConfidenceBucket,
  formatDecisionReason,
} from "../modules/decisionPlainLanguage.js";
import { buildOperatorAlertHtml } from "../modules/asyncState.js";

const TABS = ["decision", "recovery"];
let _wired = false;
/** Element that had focus before the drawer opened; restored on close. */
let _returnFocusEl = null;

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

/**
 * Render the decision-card summary for either the in-drawer Decision
 * tab or the legacy in-page #decisionSection. Pass an `idPrefix` of
 * `"tradeDrawerDecision"` for the drawer or `"decision"` for legacy.
 */
function renderDecisionInto(idPrefix, data, error, opts = {}) {
  const ph = $(`${idPrefix}Placeholder`);
  const sum = $(`${idPrefix}Summary`);
  const det = $(`${idPrefix}JsonDetails`);
  const pre = $(`${idPrefix}Output`);
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
    if (det) det.classList.add("hidden");
    if (pre) pre.textContent = "";
    return;
  }
  if (ph) ph.classList.add("hidden");
  const d = data || {};
  const ez = d.entry_zone || {};
  const sz = d.size || {};
  const conf = d.confidence || {};
  const blocked = Boolean(d.checklist && d.checklist.blocked);
  const scoreN = Number(conf.signal_score);
  const scoreTxt = Number.isFinite(scoreN) ? scoreN.toFixed(1) : "—";
  const confLabel = formatConfidenceBucket(conf.bucket);
  const verdict = blocked
    ? "Not ready — safety checks need attention."
    : "Looks OK to review — no hard blocks right now.";
  const verdictClass = blocked ? "bad" : "good";
  const checklistLines = Array.isArray(d.checklist?.checklist_lines) ? d.checklist.checklist_lines : [];
  const checklistHtml = checklistLines.length
    ? `<ul class="tool-summary-list tool-summary-list--plain">${checklistLines
        .map((line) => {
          if (!line || typeof line !== "object") return "";
          return `<li><strong>${safeText(line.label)}:</strong> ${safeText(line.value_plain)}</li>`;
        })
        .filter(Boolean)
        .join("")}</ul>`
    : "";
  const reasonChips = (d.key_reasons || [])
    .map((r) => formatDecisionReason(r))
    .filter(Boolean)
    .slice(0, 6);
  if (sum) {
    sum.classList.remove("hidden");
    sum.innerHTML = `
      <h4 class="tool-summary-title">${safeText(d.ticker)} — trade snapshot</h4>
      <ul class="tool-summary-list tool-summary-list--plain">
        <li><strong>Size:</strong> ${safeNum(sz.qty, 0)} shares (about ${formatMoney(sz.usd || 0)})</li>
        <li><strong>Buy zone:</strong> $${safeText(ez.low)} – $${safeText(ez.high)}</li>
        <li><strong>Stop level:</strong> $${safeText(d.stop_invalidation)} (idea only — not a live order)</li>
        <li><strong>Confidence:</strong> ${confLabel} · signal score ${scoreTxt}</li>
        <li><strong>Status:</strong> <span class="pill ${verdictClass} small">${verdict}</span></li>
      </ul>
      ${checklistHtml}
      ${
        reasonChips.length
          ? `<div class="tool-summary-reasons"><span class="muted small">Why this ranked here:</span><ul class="tool-summary-list tool-summary-list--compact">${reasonChips.map((r) => `<li>${safeText(r)}</li>`).join("")}</ul></div>`
          : ""
      }
    `;
  }
  if (det) det.classList.remove("hidden");
  if (pre) pre.textContent = prettyJson(data);
}

function renderRecoveryInto(idPrefix, data, error, opts = {}) {
  const ph = $(`${idPrefix}Placeholder`);
  const sum = $(`${idPrefix}Summary`);
  const det = $(`${idPrefix}JsonDetails`);
  const pre = $(`${idPrefix}Output`);
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
    if (det) det.classList.add("hidden");
    if (pre) pre.textContent = "";
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
  if (det) det.classList.remove("hidden");
  if (pre) pre.textContent = prettyJson(data);
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
  renderDecisionInto("tradeDrawerDecision", out.data, null);
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
  openTradeDrawer({ tab: "decision", ticker: row.ticker });
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
