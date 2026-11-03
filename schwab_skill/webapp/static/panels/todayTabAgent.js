/**
 * Today prompt agent. Approve / reject / rollback are explicit.
 * The primary number is the shared risk-adjusted score, not win rate.
 */

import { api } from "../modules/api.js";
import { escapeHtml } from "../modules/format.js";

function num(value, digits = 3) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

function renderInto(panelId, data, changes, onText) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const on = onText;
  const versions = (data.score && data.score.versions) || [];
  const rows = versions
    .map((row) => `<tr>
      <td><code>${escapeHtml(row.version_id)}</code></td>
      <td>${escapeHtml(row.prompt_key || "")}</td>
      <td>${row.n}</td>
      <td>${row.sufficient ? "readable" : "insufficient"}</td>
      <td>${row.primary_unbounded ? "unbounded" : num(row.primary_score)}</td>
      <td>${num(row.max_drawdown, 3)}</td>
      <td>${row.max_drawdown_duration_days ?? "—"}</td>
      <td>${row.win_rate == null ? "—" : `${(Number(row.win_rate) * 100).toFixed(1)}%`}</td>
      <td>${num(row.avg_realized_return_pct, 2)}</td>
    </tr>`)
    .join("");
  const comparisons = ((data.score && data.score.comparisons) || [])
    .map((row) => `${row.version_id}: ${row.edge_vs_live}`)
    .join("; ");
  const prompts = (data.prompts || [])
    .map((group) => {
      const bits = [group.live, group.shadow, ...(group.pending || []), ...(group.retired || []).slice(-2)]
        .filter(Boolean)
        .map((row) => {
          const id = escapeHtml(row.id);
          let actions = "";
          if (row.status === "pending" || row.status === "shadow") {
            actions = `<button type="button" class="btn small primary" data-agent-action="approve" data-version-id="${id}">Approve</button>
              <button type="button" class="btn small secondary" data-agent-action="reject" data-version-id="${id}">Reject</button>`;
            if (row.status === "pending") {
              actions += `<button type="button" class="btn small secondary" data-agent-action="shadow" data-version-id="${id}">Shadow</button>`;
            }
          } else if (row.status === "retired") {
            actions = `<button type="button" class="btn small secondary" data-agent-action="rollback" data-version-id="${id}">Roll back</button>`;
          }
          return `<p><span class="pill">${escapeHtml(row.status)}</span> <code>${id}</code> ${escapeHtml(row.prompt_key || "")}</p>
            <p class="muted small">${escapeHtml(row.rationale || "")}</p>
            <div class="inline-form compact">${actions}</div>`;
        })
        .join("");
      return `<h4>${escapeHtml(group.prompt_key)}</h4>${bits}`;
    })
    .join("");
  const feed = (changes || [])
    .slice()
    .reverse()
    .slice(0, 8)
    .map((row) => `<pre class="small">${escapeHtml(row.summary || "")}</pre>`)
    .join("");
  panel.innerHTML = `<p class="muted small">${escapeHtml(on)} ${escapeHtml((data.score && data.score.winner_rule) || "")} Manual approve and rollback stay available.</p>
    <h4>Change reports</h4>
    ${feed || "<p class='muted small'>No promotions or rollbacks yet.</p>"}
    <div class="table-wrap"><table>
      <thead><tr><th>Version</th><th>Prompt</th><th>n</th><th>Sample</th><th>Sortino/Sharpe</th><th>Max DD</th><th>DD days</th><th>Win rate</th><th>Avg return</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="muted small">${escapeHtml(comparisons || "No comparison yet.")}</p>
    ${prompts}`;
}

async function refresh(tab, panelId, onText) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const [out, changes] = await Promise.all([
    api.get(`/api/tab-agent/${tab}`),
    api.get("/api/tab-agent/changes"),
  ]);
  if (!out.ok) {
    panel.innerHTML = `<p class="error">${escapeHtml(out.error || "Could not load the agent.")}</p>`;
    return;
  }
  const rows = ((changes.data && changes.data.changes) || []).filter((row) => row.tab_id === tab);
  renderInto(panelId, out.data || {}, rows, onText);
}

function bind(tab, refreshId, proposeId, panelId, onText) {
  document.getElementById(refreshId)?.addEventListener("click", () => void refresh(tab, panelId, onText));
  document.getElementById(proposeId)?.addEventListener("click", async () => {
    await api.post(`/api/tab-agent/${tab}/propose`, {});
    await refresh(tab, panelId, onText);
  });
  document.getElementById(panelId)?.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-agent-action]");
    if (!btn) return;
    await api.post(`/api/tab-agent/${tab}/${btn.getAttribute("data-agent-action")}`, {
      version_id: btn.getAttribute("data-version-id"),
    });
    await refresh(tab, panelId, onText);
  });
  void refresh(tab, panelId, onText);
}

export function bindTodayTabAgent() {
  bind(
    "today",
    "todayAgentRefreshBtn",
    "todayAgentProposeBtn",
    "todayTabAgentPanel",
    "Shadow candidates auto-promote only when they beat the risk-adjusted score without a worse drawdown.",
  );
  bind(
    "debate",
    "debateAgentRefreshBtn",
    "debateAgentProposeBtn",
    "debateTabAgentPanel",
    "Committee shadow prompts auto-promote on the same objective. They do not change qual_raw, signal_score, or orders.",
  );
}
