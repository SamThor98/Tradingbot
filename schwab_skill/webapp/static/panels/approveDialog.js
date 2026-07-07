/**
 * Approve dialog — pre-trade preflight checklist, typed-ticker + risk-ack
 * guardrails, and the live approval submit.
 *
 * Extracted from app.js per the module decomposition policy in
 * docs/FRONTEND_DESIGN_SYSTEM.md ("Next Planned Splits"). DOM ids consumed:
 * #approveDialog, #approveSummary, #approveTickerInput, #approveOtpInput,
 * #approveRiskAck, #approveConfirmHint, #confirmApproveBtn.
 * Button/close wiring lives in screens/operations.js.
 *
 * app.js injects cross-panel callbacks once at boot via
 * `configureApproveDialog(deps)`:
 *   refreshPending(), trackUiEvent(name, props),
 *   trackFunnelMilestoneOnce(name, props), FUNNEL_EVENTS.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, safeNum, prettyJson, formatMoney } from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";
import { isScanSignalStageable } from "../modules/signalProvenance.js";
import { getCompositeScore, getReliabilityScore } from "../modules/signalScores.js";

let deps = {};
/** Element that had focus before the dialog opened; restored on close. */
let _returnFocusEl = null;
let _closeWired = false;

export function configureApproveDialog(injected = {}) {
  deps = { ...deps, ...injected };
}

function formatPreflightChecklistHtml(c) {
  if (!c || typeof c !== "object") return "";
  const lines = Array.isArray(c.checklist_lines) ? c.checklist_lines : [];
  const plainItems = lines
    .map((line) => {
      if (!line || typeof line !== "object") return "";
      const lb = safeText(line.label);
      const vl = safeText(line.value_plain);
      return `<li><strong>${lb}:</strong> ${vl}</li>`;
    })
    .filter(Boolean)
    .join("");
  let blockSection = "";
  if (c.blocked) {
    const br = Array.isArray(c.block_reasons_plain) ? c.block_reasons_plain : [];
    const brHtml = br.length ? br.map((t) => `<li>${safeText(t)}</li>`).join("") : "";
    const fallback = brHtml || "<li>Policy blocked this order.</li>";
    blockSection = `<p class="approve-blocked"><strong>Cannot send yet</strong></p><ul>${fallback}</ul>`;
  }
  const techJson = safeText(prettyJson(c));
  const tech = `<details class="approve-checklist-details"><summary>Technical checklist</summary><pre class="code-block code-block--tight">${techJson}</pre></details>`;
  return `<div class="approve-preflight"><strong>Pre-trade summary</strong><ul>${plainItems || "<li>No extra checklist rows.</li>"}</ul>${blockSection}${tech}</div>`;
}

export async function openApproveDialog(row) {
  const dialog = document.getElementById("approveDialog");
  const summary = document.getElementById("approveSummary");
  const est = safeNum(row.price, 0) * safeNum(row.qty, 0);
  const sig = row.signal || {};
  const expectedTicker = safeText(row.ticker).toUpperCase();
  state.approvingExpectedTicker = expectedTicker;
  if (!dialog.open) {
    _returnFocusEl =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }
  if (!_closeWired) {
    _closeWired = true;
    dialog.addEventListener("close", () => {
      // Return focus to the approve button that opened the dialog (WCAG 2.4.3).
      if (_returnFocusEl?.isConnected) _returnFocusEl.focus();
      _returnFocusEl = null;
    });
  }
  const riskHint = (!sig.sector_etf || safeNum(getCompositeScore(sig), 0) < 60 || safeNum(getReliabilityScore(sig), 0) < 45)
    ? "Caution: missing sector or lower-confidence setup."
    : "Setup context looks complete.";
  // Show the dialog immediately with a loading line so the preflight GET has
  // an explicit loading state instead of a frozen click.
  summary.innerHTML = `
    Approve BUY ${row.qty} ${row.ticker} @ ${row.price ? formatMoney(row.price) : "market"}?<br/>
    Est. value: <strong>${formatMoney(est)}</strong><br/>
    <span class="muted">Running pre-trade checklist…</span>
  `;
  if (!dialog.open) dialog.showModal();
  let checklistText = "";
  const preflight = await api.get(`/api/trades/${row.id}/preflight`);
  if (preflight.ok) {
    state.approvingChecklist = preflight.data?.checklist || null;
    const c = state.approvingChecklist || {};
    const hv = preflight.data?.high_value_2fa || {};
    checklistText = formatPreflightChecklistHtml(c);
    if (hv.required) {
      checklistText += `<p class="muted"><strong>High-value guardrail:</strong> 2FA code required for this approval.</p>`;
    }
  } else {
    checklistText = `<div class="approve-preflight muted">Checklist unavailable: ${safeText(preflight.error)}
      <button type="button" class="btn small secondary" data-approve-preflight-retry>Retry checklist</button></div>`;
  }
  if (sig && Object.keys(sig).length && !isScanSignalStageable(sig)) {
    checklistText += `<p class="approve-preflight warn-text"><strong>Scan filter:</strong> This staged signal was marked filtered at scan time. Re-run scan or adjust gates before approving.</p>`;
  }
  summary.innerHTML = `
    Approve BUY ${row.qty} ${row.ticker} @ ${row.price ? formatMoney(row.price) : "market"}?<br/>
    Est. value: <strong>${formatMoney(est)}</strong><br/>
    <span class="muted">${riskHint}</span>
    ${checklistText}
  `;
  const tickerInput = document.getElementById("approveTickerInput");
  const otpInput = document.getElementById("approveOtpInput");
  const riskAck = document.getElementById("approveRiskAck");
  if (tickerInput) {
    tickerInput.value = "";
    tickerInput.placeholder = expectedTicker || "TICKER";
  }
  if (otpInput) otpInput.value = "";
  if (riskAck) riskAck.checked = false;
  state.approvingTradeId = row.id;
  state.approvingScanSignal = sig;
  syncApproveDialogGuardrails();
  summary
    .querySelector("[data-approve-preflight-retry]")
    ?.addEventListener("click", () => void openApproveDialog(row));
}

export function syncApproveDialogGuardrails() {
  const typed = (document.getElementById("approveTickerInput")?.value || "").trim().toUpperCase();
  const expected = safeText(state.approvingExpectedTicker || "").toUpperCase();
  const ack = Boolean(document.getElementById("approveRiskAck")?.checked);
  const hint = document.getElementById("approveConfirmHint");
  const btn = document.getElementById("confirmApproveBtn");
  const tickerMatch = expected && typed === expected;
  const stagingSig = state.approvingScanSignal || {};
  const signalFiltered =
    stagingSig && Object.keys(stagingSig).length > 0 && !isScanSignalStageable(stagingSig);
  const canSubmit = Boolean(state.approvingTradeId) && tickerMatch && ack && !signalFiltered;
  if (btn) btn.disabled = !canSubmit;
  if (hint) {
    if (!typed) {
      hint.textContent = expected
        ? `Type ${expected} and confirm risk to enable live submit.`
        : "Type the ticker and confirm risk to enable live submit.";
    } else if (!tickerMatch) {
      hint.textContent = `Ticker mismatch. Enter ${expected} exactly.`;
    } else if (!ack) {
      hint.textContent = "Confirm the risk acknowledgement to enable submit.";
    } else if (signalFiltered) {
      hint.textContent = "Staged signal failed scan gates — re-stage from a tradeable row.";
    } else {
      hint.textContent = "Ready to submit this live order.";
    }
    hint.className = `approve-confirm-hint ${canSubmit ? "good" : "warn"}`;
  }
}

export async function approveTradeById(id) {
  const typed = document.getElementById("approveTickerInput")?.value?.trim().toUpperCase() || "";
  const otpCode = document.getElementById("approveOtpInput")?.value?.trim() || "";
  const expected = safeText(state.approvingExpectedTicker || "").toUpperCase();
  const ack = Boolean(document.getElementById("approveRiskAck")?.checked);
  if (!typed) {
    updateActionCenter({
      title: "Confirm ticker",
      message: "Type the trade ticker in the box to confirm this live order.",
      severity: "warn",
    });
    return false;
  }
  if (expected && typed !== expected) {
    updateActionCenter({
      title: "Ticker mismatch",
      message: `Enter ${expected} exactly before approving this live order.`,
      severity: "warn",
    });
    return false;
  }
  if (!ack) {
    updateActionCenter({
      title: "Risk acknowledgement required",
      message: "Confirm the risk acknowledgement before submitting a live order.",
      severity: "warn",
    });
    return false;
  }
  // Immediate submit feedback so the dialog never looks frozen mid-POST.
  const confirmBtn = document.getElementById("confirmApproveBtn");
  const prevLabel = confirmBtn?.textContent;
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Submitting…";
  }
  const out = await api.post(`/api/trades/${id}/approve?confirm_live=true`, { typed_ticker: typed, otp_code: otpCode });
  if (confirmBtn) {
    confirmBtn.textContent = prevLabel || "Confirm Approve";
    confirmBtn.disabled = false;
  }
  if (!out.ok) {
    logEvent({ kind: "trade", severity: "error", message: `Approve ${id} failed: ${out.error}` });
    updateActionCenter({ title: "Approval Failed", message: out.error, severity: "error" });
    return false;
  } else {
    logEvent({ kind: "trade", severity: "info", message: `Approved ${id}: order submitted.` });
    deps.trackUiEvent?.("trade_approved", { trade_id: id });
    void deps.trackFunnelMilestoneOnce?.(deps.FUNNEL_EVENTS?.FIRST_APPROVED_TRADE, {
      source: "approve_dialog",
      trade_id: id,
    });
    updateActionCenter({ title: "Trade Approved", message: `Trade ${id} approved and submitted.`, severity: "success" });
    await deps.refreshPending?.();
    return true;
  }
}
