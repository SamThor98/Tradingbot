/**
 * Trade-review loop panel — surfaces the closed-loop learning endpoints that
 * previously had no UI: `GET /api/cockpit/review` (weekly diagnostics +
 * advisory tuning proposals), `POST /api/cockpit/review/backfill` (resolve
 * matured decision packets), and `GET /api/cockpit/decision-packets`
 * (recent packets with entry-time context, incl. Kronos snapshots).
 */

import { api } from "../modules/api.js";
import { escapeHtml, prettyJson } from "../modules/format.js";
import { updateActionCenter } from "../modules/logger.js";

function pct(x, digits = 1) {
  const n = Number(x);
  return Number.isFinite(n) ? `${n.toFixed(digits)}%` : "—";
}

function renderRegimeTable(byRegime) {
  const entries = Object.entries(byRegime || {});
  if (!entries.length) return '<div class="muted">No resolved packets yet.</div>';
  const rows = entries
    .map(([regime, s]) => {
      const fp = s.fp_rate != null ? `${(Number(s.fp_rate) * 100).toFixed(1)}%` : "—";
      return `<tr><td>${escapeHtml(regime)}</td><td>${Number(s.resolved) || 0}</td><td>${Number(s.losses) || 0}</td><td>${fp}</td></tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table><thead><tr><th>Regime</th><th>Resolved</th><th>Losses</th><th>FP rate</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderSetupTable(bySetup) {
  const entries = Object.entries(bySetup || {});
  if (!entries.length) return '<div class="muted">No setup data yet.</div>';
  const rows = entries
    .map(([setup, s]) => {
      const decay = s.edge_decay != null ? Number(s.edge_decay).toFixed(3) : "—";
      return `<tr><td>${escapeHtml(setup)}</td><td>${decay}</td><td>${Number(s.samples) || 0}</td><td>${Number(s.resolved) || 0}</td></tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table><thead><tr><th>Setup</th><th>Edge decay</th><th>Samples</th><th>Resolved</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderCohortRows(featureBlock) {
  const cohorts = (featureBlock || {}).cohorts || {};
  const entries = Object.entries(cohorts);
  if (!entries.length) return "";
  return entries
    .map(([bucket, s]) => {
      const win = s.win_rate != null ? `${(Number(s.win_rate) * 100).toFixed(0)}%` : "—";
      const avg = s.avg_return_pct != null ? pct(s.avg_return_pct, 2) : "—";
      return `<tr><td>${escapeHtml(bucket)}</td><td>${Number(s.resolved) || 0}</td><td>${win}</td><td>${avg}</td></tr>`;
    })
    .join("");
}

function renderFeatureLift(lift) {
  const l = lift || {};
  const eras = l.era_splits || {};
  const eraKeys = Object.keys(eras);
  const coverage = `<small class="muted">Feature coverage: Kronos ${Number(l.kronos_packets) || 0} · Mgmt integrity ${Number(l.management_integrity_packets) || 0} of ${Number(l.total_packets) || 0} packets.</small>`;

  let tables = "";
  for (const era of eraKeys) {
    const block = eras[era] || {};
    const sections = [
      ["Kronos", block.kronos],
      ["Mgmt integrity", block.management_integrity],
    ]
      .map(([label, feat]) => {
        const rows = renderCohortRows(feat);
        if (!rows) return "";
        return `<h4>${escapeHtml(label)} — era ${escapeHtml(era)}</h4>
          <div class="table-wrap"><table><thead><tr><th>Cohort</th><th>Resolved</th><th>Win rate</th><th>Avg return</th></tr></thead><tbody>${rows}</tbody></table></div>`;
      })
      .join("");
    tables += sections;
  }
  if (!tables) tables = '<div class="muted">No resolved shadow-feature cohorts yet — keep accumulating packets.</div>';

  const pilot = l.pilot_recommendation || {};
  const ready = pilot.ready_for_single_era_pilot === true;
  const pilotHtml = `<div class="perf-metric"><span class="label">Single-era pilot</span>
      <span class="value"><span class="pill ${ready ? "good" : "neutral"}">${ready ? `READY: ${escapeHtml(String(pilot.recommended_feature || "?"))}` : "NOT YET"}</span></span>
    </div>
    <small class="muted">${escapeHtml(String(pilot.note || ""))}</small>`;

  return `${coverage}${tables}${pilotHtml}`;
}

function renderProposals(tuning) {
  const proposals = (tuning || {}).proposals || [];
  if (!proposals.length) return '<div class="muted">No tuning proposals — not enough resolved evidence yet.</div>';
  const items = proposals
    .map(
      (p) => `<li>
        <strong>${escapeHtml(p.target || "?")}</strong>
        <span class="pill ${p.direction === "increase" ? "warn" : "info"}">${escapeHtml(p.direction || "")}</span>
        <small class="muted">${escapeHtml(p.scope || "")} — ${escapeHtml(p.evidence || "")} (${escapeHtml(p.confidence || "low")} confidence)</small>
      </li>`,
    )
    .join("");
  return `<ul>${items}</ul>
    <small class="muted">${escapeHtml((tuning || {}).note || "Advisory only — nothing auto-applied.")}</small>`;
}

function renderPackets(packets) {
  if (!Array.isArray(packets) || !packets.length) {
    return '<div class="muted">No decision packets recorded yet — packets are written at trade execution.</div>';
  }
  const rows = packets
    .slice(0, 15)
    .map((p) => {
      const created = String(p.created_at || "").slice(0, 16).replace("T", " ");
      const outcome = p.outcome || {};
      const realized = outcome.realized_return_pct != null ? pct(outcome.realized_return_pct, 2) : "open";
      const kr = p.kronos || null;
      const krText = kr ? `${escapeHtml(String(kr.direction || "?"))} (${escapeHtml(String(kr.confidence_bucket || "?"))})` : "—";
      const mi = p.management_integrity || null;
      const miText = mi ? `${escapeHtml(String(mi.score_bucket || "?"))}${mi.score != null ? ` (${Number(mi.score)})` : ""}` : "—";
      return `<tr>
        <td>${escapeHtml(String(p.ticker || "?"))}</td>
        <td>${escapeHtml(created)}</td>
        <td>${escapeHtml(String(p.setup_type || "—"))}</td>
        <td>${escapeHtml(String(p.regime || "—"))}</td>
        <td>${krText}</td>
        <td>${miText}</td>
        <td>${realized}</td>
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Created</th><th>Setup</th><th>Regime</th><th>Kronos</th><th>Mgmt</th><th>Outcome</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

export function renderReviewLoopPanel(panel, review, packets, error) {
  if (!panel) return;
  if (error) {
    panel.innerHTML = `<div class="report-empty">${escapeHtml(error)}</div>`;
    return;
  }
  const rep = review || {};
  const head = `<div class="perf-metric"><span class="label">Total packets</span><span class="value">${Number(rep.total_packets) || 0}</span></div>
    <div class="perf-metric"><span class="label">Resolved</span><span class="value">${Number(rep.resolved_packets) || 0}</span></div>
    <div class="perf-metric"><span class="label">Coverage</span><span class="value">${pct(rep.coverage_pct)}</span></div>`;
  panel.innerHTML = `
    <div class="preset-subsection">${head}</div>
    <div class="preset-subsection"><h3>False positives by regime</h3>${renderRegimeTable(rep.false_positives_by_regime)}</div>
    <div class="preset-subsection"><h3>Edge decay by setup</h3>${renderSetupTable(rep.edge_decay_by_setup)}</div>
    <div class="preset-subsection"><h3>Shadow feature lift (Kronos · Mgmt integrity)</h3>${renderFeatureLift(rep.feature_lift)}</div>
    <div class="preset-subsection"><h3>Tuning proposals</h3>${renderProposals(rep.tuning_proposals)}</div>
    <div class="preset-subsection"><h3>Recent decision packets</h3>${renderPackets(packets)}</div>
    <details class="tool-json-details" style="margin-top: 8px;"><summary>Raw report</summary><pre class="code-block code-block--tight">${escapeHtml(prettyJson(rep))}</pre></details>`;
}

export async function refreshReviewLoop() {
  const panel = document.getElementById("reviewLoopPanel");
  const card = document.getElementById("reviewLoopSection");
  if (!panel) return;
  if (card) card.setAttribute("data-async-state", "loading");
  panel.innerHTML = `<div class="async-state async-state--loading muted" role="status">
    <span class="async-spinner" aria-hidden="true"></span>
    <span>Loading trade-review report…</span>
  </div>`;
  const [reviewOut, packetsOut] = await Promise.all([
    api.get("/api/cockpit/review"),
    api.get("/api/cockpit/decision-packets?limit=15"),
  ]);
  if (!reviewOut.ok) {
    if (card) card.setAttribute("data-async-state", "error");
    const msg = reviewOut.user_message || reviewOut.error || "Request failed";
    panel.innerHTML = `<div class="async-state async-state--error" role="alert">
      <span>Trade-review load failed: ${escapeHtml(String(msg))}</span>
      <button type="button" class="btn small secondary" data-review-retry>Retry</button>
    </div>`;
    panel.querySelector("[data-review-retry]")?.addEventListener("click", () => void refreshReviewLoop());
    return;
  }
  const packets = packetsOut.ok ? (packetsOut.data || {}).packets || [] : [];
  if (card) card.setAttribute("data-async-state", "success");
  renderReviewLoopPanel(panel, reviewOut.data, packets, null);
}

export async function runReviewBackfill() {
  const btn = document.getElementById("reviewBackfillBtn");
  if (btn) btn.disabled = true;
  try {
    // Mutation — one-shot, never auto-retry.
    const out = await api.post("/api/cockpit/review/backfill", {});
    if (!out.ok) {
      const msg = out.user_message || out.error || "Request failed";
      updateActionCenter({ title: "Outcome backfill", message: String(msg), severity: "error" });
      return;
    }
    const d = out.data || {};
    updateActionCenter({
      title: "Outcome backfill",
      message: `Resolved ${d.resolved ?? 0} of ${d.total ?? 0} matured packets.`,
      severity: "success",
    });
    await refreshReviewLoop();
  } finally {
    if (btn) btn.disabled = false;
  }
}
