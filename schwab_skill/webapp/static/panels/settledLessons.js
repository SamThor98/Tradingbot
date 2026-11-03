/**
 * Today card: what settled, which pocket has evidence, and one operator decision.
 * Keep / Sharpen / Revert record a journal entry. They do not change settings or place orders.
 */

import { api } from "../modules/api.js";
import { escapeHtml } from "../modules/format.js";
import { renderPeadCanaryForward } from "./peadCanary.js";

const ACTION_IDS = {
  keep: "settledLessonKeep",
  sharpen: "settledLessonSharpen",
  revert: "settledLessonRevert",
};

function fmtPf(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
}

function labelClass(label) {
  if (label === "Blocked") return "warn";
  if (label === "Shadow") return "info";
  return "neutral";
}

function renderError(message) {
  const root = document.getElementById("settledLessonsSection");
  const body = document.getElementById("settledLessonsBody");
  const decision = document.getElementById("settledLessonsDecision");
  if (root) root.dataset.state = "error";
  const meta = document.getElementById("settledLessonsMeta");
  if (meta) meta.textContent = "Could not load";
  const fold = document.getElementById("settledLessonsFold");
  if (fold) fold.open = true;
  if (body) {
    body.innerHTML = `<p class="muted small">${escapeHtml(message)}</p>
      <button type="button" class="btn small secondary" id="settledLessonsRetry">Retry</button>`;
    document.getElementById("settledLessonsRetry")?.addEventListener("click", () => {
      void refreshSettledLessons();
    });
  }
  if (decision) decision.textContent = "";
  setActionsEnabled(false);
}

function setActionsEnabled(enabled) {
  for (const id of Object.values(ACTION_IDS)) {
    const btn = document.getElementById(id);
    if (btn) btn.disabled = !enabled;
  }
}

function renderSettledLessons(data) {
  const root = document.getElementById("settledLessonsSection");
  const body = document.getElementById("settledLessonsBody");
  const meta = document.getElementById("settledLessonsMeta");
  const decisionEl = document.getElementById("settledLessonsDecision");
  if (!root || !body) return;

  const settled = data.settled || {};
  const pocket = data.pocket || {};
  const gate = data.signal_gate || {};
  const proposal = data.proposal || null;
  const decision = data.decision || null;
  const label = String(pocket.label || "Paper");

  root.dataset.state = settled.status === "scored" ? "scored" : "still_paper";

  if (meta) {
    const pf = fmtPf(gate.pf_mean);
    const worst = fmtPf(gate.worst_era_pf);
    const proposalBit = proposal ? "1 proposal" : "No proposal";
    const book = gate.source ? `phase-1 ${gate.source}` : "phase-1 book";
    meta.textContent = gate.on_file
      ? `${label} · ${book} · ${proposalBit} · base PF ${pf} (need ${fmtPf(gate.pf_mean_min)}) · worst era ${worst} (need ${fmtPf(gate.worst_era_pf_min)})`
      : `${label} · ${proposalBit} · Base-signal profit factor is not on file.`;
  }

  const proposalText = proposal
    ? escapeHtml(proposal.summary || "Proposal waiting.")
    : "No proposal waiting.";
  const pocketName = pocket.name ? ` ${escapeHtml(String(pocket.name))}` : "";

  body.innerHTML = `
    <ol class="settled-lessons-lines">
      <li>
        <span class="settled-lessons-kicker">What settled</span>
        <span>${escapeHtml(settled.headline || "Still paper.")}</span>
      </li>
      <li>
        <span class="settled-lessons-kicker">Pocket</span>
        <span class="pill ${labelClass(label)}">${escapeHtml(label)}</span>
        <span>${pocketName ? `${pocketName} — ` : ""}${escapeHtml(pocket.detail || "")}</span>
      </li>
      <li>
        <span class="settled-lessons-kicker">Proposal</span>
        <span>${proposalText}</span>
      </li>
    </ol>
    <p class="muted small settled-lessons-note">${escapeHtml(data.note || "")}</p>`;

  renderPeadCanaryForward(data.pead_canary);
  setActionsEnabled(data.actions_enabled === true);
  if (decisionEl) {
    decisionEl.textContent = decision?.summary ? String(decision.summary) : "";
  }
}

export async function refreshSettledLessons() {
  const body = document.getElementById("settledLessonsBody");
  if (!body) return;
  const out = await api.get("/api/settled-lessons");
  if (!out.ok) {
    renderError(out.error || out.user_message || "Could not load settled lessons.");
    return;
  }
  renderSettledLessons(out.data || {});
}

async function decide(action) {
  setActionsEnabled(false);
  const decisionEl = document.getElementById("settledLessonsDecision");
  if (decisionEl) decisionEl.textContent = "Recording decision…";
  const out = await api.post("/api/settled-lessons/decision", { action });
  if (!out.ok) {
    if (out.data && typeof out.data === "object") renderSettledLessons(out.data);
    if (decisionEl) decisionEl.textContent = out.error || "Decision was not recorded.";
    return;
  }
  renderSettledLessons(out.data || {});
}

export function bindSettledLessons() {
  const root = document.getElementById("settledLessonsActions");
  if (!root || root.dataset.bound === "1") return;
  root.dataset.bound = "1";
  root.addEventListener("click", (event) => {
    const btn = event.target instanceof Element ? event.target.closest("[data-action]") : null;
    if (!btn || btn.disabled) return;
    const action = btn.getAttribute("data-action");
    if (!action) return;
    void decide(action);
  });
}
