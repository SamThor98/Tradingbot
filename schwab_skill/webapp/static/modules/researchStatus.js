import { escapeHtml } from "./format.js";

const VALID_STATES = new Set(["success", "partial", "empty", "loading", "error"]);

export function setResearchStatusStrip(id, stateName, title, detail = "") {
  const strip = document.getElementById(id);
  if (!strip) return;
  const state = VALID_STATES.has(stateName) ? stateName : "empty";
  const label = state.charAt(0).toUpperCase() + state.slice(1);
  strip.dataset.state = state;
  strip.innerHTML = `
    <span class="research-status-pill">${escapeHtml(label)}</span>
    <strong>${escapeHtml(title)}</strong>
    <span class="muted">${escapeHtml(detail)}</span>
  `;
}
