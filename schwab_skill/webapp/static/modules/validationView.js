import { durationSec, safeNum, safeText } from "./format.js";

export function renderValidationRecentSteps(validation = {}) {
  const listEl = document.getElementById("validationRecentSteps");
  const wrapEl = document.getElementById("validationRecentWrap");
  if (!listEl || !wrapEl) return;
  listEl.innerHTML = "";
  const rows = Array.isArray(validation.results) ? validation.results : [];
  if (!rows.length) {
    const empty = document.createElement("li");
    empty.className = "muted";
    empty.textContent = "No validation steps yet.";
    listEl.appendChild(empty);
    return;
  }
  const lastFive = rows.slice(-5).reverse();
  lastFive.forEach((step) => {
    const name = safeText(step.name || "unknown_step");
    const rc = safeNum(step.returncode, 1);
    const status = rc === 0 ? "PASS" : "FAIL";
    const seconds = durationSec(step.started_at, step.ended_at);
    const durText = seconds === null ? "n/a" : `${seconds}s`;
    const li = document.createElement("li");
    li.append(document.createTextNode(`${name}: `));
    const strong = document.createElement("strong");
    strong.textContent = status;
    li.append(strong);
    li.append(document.createTextNode(` (${durText})`));
    listEl.appendChild(li);
  });
}
