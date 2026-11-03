/**
 * Today tab agent: show persona prompt versions and approve / reject / roll back.
 * Proposals are never applied by this panel except through an explicit button.
 */

import { api } from "../modules/api.js";
import { escapeHtml } from "../modules/format.js";

function pill(status) {
  const label = String(status || "unknown");
  const tone = label === "live" ? "good" : label === "pending" || label === "shadow" ? "neutral" : "";
  return `<span class="pill ${tone}">${escapeHtml(label)}</span>`;
}

function versionBlock(row, actions) {
  if (!row) return "";
  const text = String(row.text || "");
  return `<article class="card" style="margin:0.5rem 0">
    <div>${pill(row.status)} <code>${escapeHtml(row.id)}</code>
      <small class="muted">${escapeHtml(row.created_at || "")}</small></div>
    <p class="muted small">${escapeHtml(row.rationale || "")}</p>
    <details>
      <summary>Prompt text</summary>
      <pre class="small">${escapeHtml(text)}</pre>
    </details>
    ${actions || ""}
  </article>`;
}

function actionButtons(row) {
  const id = escapeHtml(row.id);
  if (row.status === "pending") {
    return `<div class="inline-form compact">
      <button type="button" class="btn small primary" data-agent-action="approve" data-version-id="${id}">Approve</button>
      <button type="button" class="btn small secondary" data-agent-action="shadow" data-version-id="${id}">Shadow</button>
      <button type="button" class="btn small secondary" data-agent-action="reject" data-version-id="${id}">Reject</button>
    </div>`;
  }
  if (row.status === "shadow") {
    return `<div class="inline-form compact">
      <button type="button" class="btn small primary" data-agent-action="approve" data-version-id="${id}">Approve</button>
      <button type="button" class="btn small secondary" data-agent-action="reject" data-version-id="${id}">Reject</button>
    </div>`;
  }
  if (row.status === "retired") {
    return `<div class="inline-form compact">
      <button type="button" class="btn small secondary" data-agent-action="rollback" data-version-id="${id}">Roll back to this</button>
    </div>`;
  }
  return "";
}

function scoreLine(score) {
  const versions = (score && score.versions) || [];
  if (!versions.length) return "<p class='muted small'>No scored versions yet.</p>";
  const minN = score.min_samples;
  const rows = versions
    .map((row) => {
      const enough = row.sufficient ? "readable" : `n&lt;${minN}`;
      const win = row.win_rate == null ? "—" : `${(Number(row.win_rate) * 100).toFixed(1)}%`;
      const ci = Array.isArray(row.win_rate_ci95) && row.win_rate_ci95.length === 2
        ? `${(row.win_rate_ci95[0] * 100).toFixed(0)}–${(row.win_rate_ci95[1] * 100).toFixed(0)}%`
        : "—";
      const avg = row.avg_realized_return_pct == null ? "—" : `${Number(row.avg_realized_return_pct).toFixed(2)}%`;
      const sup = row.suppress_precision == null ? "—" : `${(Number(row.suppress_precision) * 100).toFixed(0)}% (n=${row.suppress_n})`;
      return `<tr>
        <td><code>${escapeHtml(row.version_id)}</code></td>
        <td>${escapeHtml(row.prompt_key || "")}</td>
        <td>${row.n}</td>
        <td>${enough}</td>
        <td>${win}</td>
        <td>${ci}</td>
        <td>${avg}</td>
        <td>${sup}</td>
      </tr>`;
    })
    .join("");
  const comparisons = (score.comparisons || [])
    .map((row) => `${escapeHtml(row.version_id)}: ${escapeHtml(row.edge_vs_live)}`)
    .join("; ");
  return `<p class="muted small">${escapeHtml(score.winner_rule || "")} Auto-promote is off.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Version</th><th>Prompt</th><th>n</th><th>Sample</th><th>Win rate</th><th>95% CI</th><th>Avg return</th><th>Suppress precision</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    <p class="muted small">${comparisons || "No comparison yet."}</p>`;
}

function render(data) {
  const panel = document.getElementById("todayTabAgentPanel");
  if (!panel) return;
  const applied = data.applied_to_scans ? "On: scans use the live registry prompt." : "Off: scans still use the built-in personas until TAB_AGENT_TODAY_ENABLED=true.";
  const shadow = data.shadow_enabled
    ? "Shadow calls are enabled."
    : "Shadow calls are off (TAB_AGENT_SHADOW_ENABLED). Marking a version shadow does not change decisions.";
  const prompts = (data.prompts || [])
    .map((group) => {
      const retired = (group.retired || []).slice(-3).map((row) => versionBlock(row, actionButtons(row))).join("");
      const pending = (group.pending || []).map((row) => versionBlock(row, actionButtons(row))).join("");
      return `<h4>${escapeHtml(group.prompt_key)}</h4>
        ${versionBlock(group.live, "")}
        ${group.shadow ? versionBlock(group.shadow, actionButtons(group.shadow)) : ""}
        ${pending}
        ${retired}`;
    })
    .join("");
  panel.innerHTML = `<p class="muted small">${escapeHtml(applied)} ${escapeHtml(shadow)}</p>
    ${scoreLine(data.score)}
    ${prompts || "<p class='muted'>No prompts seeded.</p>"}`;
}

async function refreshTodayTabAgent() {
  const panel = document.getElementById("todayTabAgentPanel");
  if (!panel) return;
  const out = await api.get("/api/tab-agent/today");
  if (!out.ok) {
    panel.innerHTML = `<p class="error">${escapeHtml(out.error || "Could not load the Today agent.")}</p>`;
    return;
  }
  render(out.data || {});
}

async function postAction(action, versionId) {
  const out = await api.post(`/api/tab-agent/today/${action}`, { version_id: versionId });
  if (!out.ok) {
    const panel = document.getElementById("todayTabAgentPanel");
    if (panel) {
      panel.insertAdjacentHTML("afterbegin", `<p class="error">${escapeHtml(out.error || "Request failed.")}</p>`);
    }
    return;
  }
  await refreshTodayTabAgent();
}

export function bindTodayTabAgent() {
  const refreshBtn = document.getElementById("todayAgentRefreshBtn");
  const proposeBtn = document.getElementById("todayAgentProposeBtn");
  const panel = document.getElementById("todayTabAgentPanel");
  if (refreshBtn) refreshBtn.addEventListener("click", () => void refreshTodayTabAgent());
  if (proposeBtn) {
    proposeBtn.addEventListener("click", async () => {
      proposeBtn.disabled = true;
      try {
        const out = await api.post("/api/tab-agent/today/propose", {});
        if (!out.ok && panel) {
          panel.insertAdjacentHTML("afterbegin", `<p class="error">${escapeHtml(out.error || "Proposal failed.")}</p>`);
        }
        await refreshTodayTabAgent();
      } finally {
        proposeBtn.disabled = false;
      }
    });
  }
  if (panel) {
    panel.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-agent-action]");
      if (!btn) return;
      void postAction(btn.getAttribute("data-agent-action"), btn.getAttribute("data-version-id"));
    });
  }
  void refreshTodayTabAgent();
}
