/**
 * Manual override for Today and Debate tab agents.
 * Approve and reject write Sam's review verdict. Rollback restores a retired prompt.
 * Nothing here places an order.
 */

import { api } from "../modules/api.js";
import { escapeHtml } from "../modules/format.js";

function num(value, digits = 3) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

function versionLine(row, actions) {
  if (!row) return "<p class='muted small'>None</p>";
  const review = row.review
    ? `<span class="pill">${escapeHtml(row.review.verdict)}</span> <span class="muted small">${escapeHtml(row.review.reviewer || "")}</span>`
    : "<span class='muted small'>No verdict yet</span>";
  return `<p><span class="pill">${escapeHtml(row.status || "")}</span> <code>${escapeHtml(row.id || "")}</code></p>
    <p class="muted small">n=${row.n ?? "—"} (live ${row.n_live ?? row.n ?? "—"}, replay ${row.n_replay ?? 0}) ${escapeHtml(row.primary_metric || "score")}=${num(row.primary_score)} spy_sortino=${num(row.spy_sortino)} spy_maxDD=${num(row.spy_max_drawdown)} sample=${escapeHtml(row.sample_status || "")} spy=${num(row.spy_total_return)} excess=${num(row.excess_vs_spy && row.excess_vs_spy.shrunk, 4)} selection=${num(row.selection_vs_pool && row.selection_vs_pool.shrunk, 4)} edge=${num(row.edge_vs_baseline)} maxDD=${num(row.max_drawdown)} ${escapeHtml(row.edge_vs_live || "")}${row.decision_prompt === false ? " non-decision" : row.not_scored_reason ? " " + escapeHtml(row.not_scored_reason) : row.degenerate ? " no-variance" : ""}${row.sample_status === "insufficient" || (row.excess_vs_spy && row.excess_vs_spy.status === "insufficient") ? " insufficient" : ""}</p>
    <p class="muted small">${review}</p>
    <p class="muted small">${escapeHtml(row.rationale || "")}</p>
    <div class="inline-form compact">${actions}</div>`;
}

function actionButtons(tab, row, kind) {
  if (!row) return "";
  const id = escapeHtml(row.id || "");
  const tabId = escapeHtml(tab);
  if (kind === "shadow" || kind === "pending") {
    return `<button type="button" class="btn small primary" data-tab-agent-action="approve" data-tab="${tabId}" data-version-id="${id}">Approve</button>
      <button type="button" class="btn small secondary" data-tab-agent-action="reject" data-tab="${tabId}" data-version-id="${id}">Reject</button>`;
  }
  if (kind === "retired") {
    return `<button type="button" class="btn small secondary" data-tab-agent-action="rollback" data-tab="${tabId}" data-version-id="${id}">Rollback</button>`;
  }
  return "";
}

function renderDigest(digest) {
  if (!digest) return "<p class='muted small'>No digest file yet. The weekday 16:00 ET pass writes one.</p>";
  const tabs = (digest.tabs || [])
    .map((tab) => {
      const live = (tab.live || [])
        .map(
          (row) =>
            `${row.prompt_key || ""} ${row.version_id || ""} n=${row.n ?? "—"} live ${row.n_live ?? "—"} replay ${row.n_replay ?? 0} ${row.primary_metric || ""}=${row.primary_score ?? "—"}`,
        )
        .join("; ");
      const events = (tab.events || []).map((row) => row.action).join(", ") || "none";
      return `<p><strong>${escapeHtml(tab.tab_id || "")}</strong> ${escapeHtml(live || "no live score")} · events: ${escapeHtml(events)}</p>`;
    })
    .join("");
  const errors = (digest.errors || []).map((row) => `${row.tab_id}: ${row.error}`).join("; ");
  return `<p class="muted small">Latest digest ${escapeHtml(digest.day || "")}</p>${tabs}${
    errors ? `<p class="error">${escapeHtml(errors)}</p>` : ""
  }`;
}

function render(board) {
  const tabs = (board.tabs || [])
    .map((tab) => {
      const prompts = (tab.prompts || [])
        .map((group) => {
          const pending = (group.pending || [])
            .map((row) => versionLine(row, actionButtons(tab.tab_id, row, "pending")))
            .join("");
          const retired = (group.retired || [])
            .map((row) => versionLine(row, actionButtons(tab.tab_id, row, "retired")))
            .join("");
          return `<h4>${escapeHtml(group.prompt_key || "")}</h4>
            <p class="muted small">Live</p>
            ${versionLine(group.live, "")}
            <p class="muted small">Shadow</p>
            ${versionLine(group.shadow, actionButtons(tab.tab_id, group.shadow, "shadow"))}
            ${pending ? `<p class="muted small">Pending</p>${pending}` : ""}
            ${retired ? `<p class="muted small">Retired</p>${retired}` : ""}`;
        })
        .join("");
      const waiting = tab.pending_reviews || [];
      const waitingText = waiting.length
        ? waiting
            .map((row) => `${row.prompt_key || ""} ${row.id || ""} n=${row.n ?? "—"} live ${row.n_live ?? "—"} replay ${row.n_replay ?? 0}`)
            .join("; ")
        : "None";
      return `<section class="card">
        <h3>${escapeHtml(tab.tab_id || "")}</h3>
        <p class="muted small">Pending reviews: ${escapeHtml(waitingText)}</p>
        ${prompts || "<p class='muted small'>No prompts seeded.</p>"}
      </section>`;
    })
    .join("");
  const guide = `<p class="muted small">Score is the annualized daily Sortino of an equal-weight book, beside SPY Sortino and SPY max DD on the same sessions. A missing price path is unscored. Excess and selection are shrunk by n/(n+10). Fewer than 8 distinct weeks is insufficient. The system tab shows a Brier score.</p>`;
  return `${guide}${renderDigest(board.digest)}<div id="tabAgentsStatus" class="muted small" aria-live="polite"></div>${tabs}`;
}

async function refresh() {
  const panel = document.getElementById("tabAgentsPanel");
  if (!panel) return;
  const out = await api.get("/api/tab-agents");
  if (!out.ok) {
    panel.innerHTML = `<p class="error">${escapeHtml(out.error || "Could not load tab agents.")}</p>`;
    return;
  }
  panel.innerHTML = render(out.data || {});
}

export function bindTabAgents() {
  document.getElementById("tabAgentsRefreshBtn")?.addEventListener("click", () => void refresh());
  document.getElementById("tabAgentsPanel")?.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-tab-agent-action]");
    if (!btn) return;
    const action = btn.getAttribute("data-tab-agent-action");
    const tab = btn.getAttribute("data-tab");
    const versionId = btn.getAttribute("data-version-id");
    const out = await api.post(`/api/tab-agents/${tab}/${action}`, { version_id: versionId });
    const status = document.getElementById("tabAgentsStatus");
    if (!out.ok && status) {
      status.innerHTML = `<span class="error">${escapeHtml(out.error || "Request failed.")}</span>`;
    } else if (status) {
      status.textContent = "";
    }
    await refresh();
  });
  void refresh();
}
