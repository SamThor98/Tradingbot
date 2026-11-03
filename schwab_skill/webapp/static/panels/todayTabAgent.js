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

function render(data, panelId) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const applied = data.effect === "log_only"
    ? (data.enabled
      ? "On: after each scan the committee logs approve/pass. It does not change scan decisions or place orders."
      : "Off: no committee LLM calls until TAB_AGENT_DEBATE_ENABLED=true.")
    : (data.applied_to_scans
      ? "On: scans use the live registry prompt."
      : "Off: scans still use the built-in personas until TAB_AGENT_TODAY_ENABLED=true.");
  const shadowFlag = data.effect === "log_only" ? "TAB_AGENT_DEBATE_SHADOW_ENABLED" : "TAB_AGENT_SHADOW_ENABLED";
  const shadow = data.shadow_enabled
    ? "Shadow calls are enabled and are not used as the live decision."
    : `Shadow calls are off (${shadowFlag}). Marking a version shadow does not add LLM calls.`;
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
  const cal = data.score && data.score.calibration;
  const passes = data.score && data.score.passes;
  const extra = cal
    ? `<p class="muted small">Calibration ${escapeHtml(cal.verdict || "")}: ECE ${cal.ece == null ? "—" : cal.ece} on n=${cal.n}. Pass counterfactual ${escapeHtml((passes && passes.verdict) || "")}: win rate ${passes && passes.win_rate != null ? `${(Number(passes.win_rate) * 100).toFixed(1)}%` : "—"} (n=${passes ? passes.n : 0}).</p>`
    : "";
  panel.innerHTML = `<p class="muted small">${escapeHtml(applied)} ${escapeHtml(shadow)}</p>
    ${scoreLine(data.score)}
    ${extra}
    ${prompts || "<p class='muted'>No prompts seeded.</p>"}`;
}

async function refreshTabAgent(tab, panelId) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const out = await api.get(`/api/tab-agent/${tab}`);
  if (!out.ok) {
    panel.innerHTML = `<p class="error">${escapeHtml(out.error || "Could not load the agent.")}</p>`;
    return;
  }
  render(out.data || {}, panelId);
}

async function postAction(tab, panelId, action, versionId) {
  const out = await api.post(`/api/tab-agent/${tab}/${action}`, { version_id: versionId });
  if (!out.ok) {
    const panel = document.getElementById(panelId);
    if (panel) {
      panel.insertAdjacentHTML("afterbegin", `<p class="error">${escapeHtml(out.error || "Request failed.")}</p>`);
    }
    return;
  }
  await refreshTabAgent(tab, panelId);
}

function bindOne(tab, refreshId, proposeId, panelId) {
  const refreshBtn = document.getElementById(refreshId);
  const proposeBtn = document.getElementById(proposeId);
  const panel = document.getElementById(panelId);
  if (refreshBtn) refreshBtn.addEventListener("click", () => void refreshTabAgent(tab, panelId));
  if (proposeBtn) {
    proposeBtn.addEventListener("click", async () => {
      proposeBtn.disabled = true;
      try {
        const out = await api.post(`/api/tab-agent/${tab}/propose`, {});
        if (!out.ok && panel) {
          panel.insertAdjacentHTML("afterbegin", `<p class="error">${escapeHtml(out.error || "Proposal failed.")}</p>`);
        }
        await refreshTabAgent(tab, panelId);
      } finally {
        proposeBtn.disabled = false;
      }
    });
  }
  if (panel) {
    panel.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-agent-action]");
      if (!btn) return;
      void postAction(tab, panelId, btn.getAttribute("data-agent-action"), btn.getAttribute("data-version-id"));
    });
  }
  void refreshTabAgent(tab, panelId);
}

export function bindTodayTabAgent() {
  bindOne("today", "todayAgentRefreshBtn", "todayAgentProposeBtn", "todayTabAgentPanel");
  bindOne("debate", "debateAgentRefreshBtn", "debateAgentProposeBtn", "debateTabAgentPanel");
}
