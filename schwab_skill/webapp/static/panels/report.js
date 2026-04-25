/**
 * Stock report panel — `/api/report/<ticker>` is the data source.
 *
 * This visual layout is decision-first: IC Snapshot, scenario framing,
 * portfolio-fit, and monitoring accountability lead the report, while
 * existing section tabs remain as appendix detail.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, safeNum, formatMoney, pct, verdictFromScore } from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";
import { normalizeReportPayload, runReportNormalizationSmokeChecks } from "../modules/reportNormalization.js";

let smokeChecked = false;

function inferredBadge(enabled) {
  return enabled ? `<span class="report-badge inferred">inferred</span>` : "";
}

function unavailable(value) {
  if (value === null || value === undefined || String(value).trim() === "") {
    return `<span class="muted">Unavailable</span>`;
  }
  return safeText(value);
}

function confidenceText(ic) {
  if (!Number.isFinite(Number(ic.confidence_score))) return safeText(ic.confidence_label || "Unavailable");
  return `${safeText(ic.confidence_label)} (${safeText(ic.confidence_score)}/100)`;
}

function renderClaimEvidence({ claim, evidence, confidence, falsifier }) {
  return `
    <div class="report-claim-grid">
      <div><span class="subtle">Claim</span><div>${unavailable(claim)}</div></div>
      <div><span class="subtle">Evidence</span><div>${unavailable(evidence)}</div></div>
      <div><span class="subtle">Confidence</span><div>${unavailable(confidence)}</div></div>
      <div><span class="subtle">Falsifier</span><div>${unavailable(falsifier)}</div></div>
    </div>
  `;
}

function getAppendixSections(normalized) {
  const d = normalized.appendix || {};
  const candidates = ["summary", "technical", "dcf", "comps", "health", "edgar", "mirofish", "synthesis"];
  return candidates.filter((key) => key === "summary" || d[key] !== undefined && d[key] !== null);
}

function buildAppendixBlocks(normalized) {
  const d = normalized.appendix || {};
  const ticker = normalized.ticker || "—";
  const tech = d.technical || null;
  const dcf = d.dcf || null;
  const health = d.health || null;
  const comps = d.comps || null;
  const edgar = d.edgar || null;
  const miro = d.mirofish || null;
  const synthesis = d.synthesis || "";
  const sectionVerdicts = {
    technical: verdictFromScore(tech?.signal_score ?? 50, 65, 45),
    dcf: verdictFromScore(dcf?.margin_of_safety ?? 0, 10, -10),
    health: (health?.flags || []).length === 0 ? "bullish" : (health.flags.length >= 3 ? "bearish" : "neutral"),
    mirofish: verdictFromScore(miro?.conviction_score ?? 0, 30, -30),
  };
  return {
    summary: `
      <div class="report-section">
        <h4>Appendix: Summary</h4>
        <div class="subtle">Legacy section-level views are preserved.</div>
        <ul class="report-bullets">
          <li>Ticker: ${safeText(ticker)}</li>
          <li>Technical Verdict: <span class="verdict ${sectionVerdicts.technical}">${sectionVerdicts.technical}</span></li>
          <li>DCF Verdict: <span class="verdict ${sectionVerdicts.dcf}">${sectionVerdicts.dcf}</span></li>
          <li>Health Verdict: <span class="verdict ${sectionVerdicts.health}">${sectionVerdicts.health}</span></li>
          <li>MiroFish Verdict: <span class="verdict ${sectionVerdicts.mirofish}">${sectionVerdicts.mirofish}</span></li>
        </ul>
      </div>`,
    technical: tech ? `
      <div class="report-section">
        <h4>Appendix: Technical <span class="verdict ${sectionVerdicts.technical}">${sectionVerdicts.technical}</span></h4>
        <ul class="report-bullets">
          <li>Price: ${formatMoney(tech.current_price)}</li>
          <li>52w Range: ${formatMoney(tech.low_52w)} - ${formatMoney(tech.high_52w)}</li>
          <li>SMA 50/150/200: ${formatMoney(tech.sma_50)} / ${formatMoney(tech.sma_150)} / ${formatMoney(tech.sma_200)}</li>
          <li>VCP: ${tech.vcp ? "YES" : "NO"} | Sector: ${safeText(tech.sector_etf)}</li>
          <li>Takeaway: ${tech.stage_2 && tech.vcp ? "Trend and volume structure are aligned." : "Setup quality is incomplete."}</li>
        </ul>
      </div>` : "",
    dcf: dcf ? `
      <div class="report-section">
        <h4>Appendix: DCF <span class="verdict ${sectionVerdicts.dcf}">${sectionVerdicts.dcf}</span></h4>
        <ul class="report-bullets">
          <li>Intrinsic Value: ${formatMoney(dcf.intrinsic_value)}</li>
          <li>Current Price: ${formatMoney(dcf.current_price)}</li>
          <li>Margin of Safety: ${safeNum(dcf.margin_of_safety).toFixed(1)}%</li>
          <li>Growth / WACC / Terminal: ${pct(dcf.growth_rate)} / ${pct(dcf.wacc)} / ${pct(dcf.terminal_growth)}</li>
          <li>Takeaway: ${safeNum(dcf.margin_of_safety) >= 0 ? "Valuation supports upside." : "Valuation implies premium pricing."}</li>
        </ul>
      </div>` : "",
    comps: comps ? `
      <div class="report-section">
        <h4>Appendix: Comps</h4>
        <ul class="report-bullets">
          <li>Peers: ${(comps.peers || []).slice(0, 6).map((p) => p.ticker).join(", ") || "—"}</li>
          <li>Median P/E: ${safeText(comps.median_pe)} | Median P/S: ${safeText(comps.median_ps)}</li>
          <li>Implied P/E: ${formatMoney(comps.implied_price_pe)} | Implied P/S: ${formatMoney(comps.implied_price_ps)}</li>
          <li>Takeaway: Comps provide cross-check against standalone DCF assumptions.</li>
        </ul>
      </div>` : "",
    health: health ? `
      <div class="report-section">
        <h4>Appendix: Health <span class="verdict ${sectionVerdicts.health}">${sectionVerdicts.health}</span></h4>
        <ul class="report-bullets">
          <li>Current Ratio: ${safeText(health.current_ratio)}</li>
          <li>Debt/Equity: ${safeText(health.debt_to_equity)}</li>
          <li>Interest Coverage: ${safeText(health.interest_coverage)}x</li>
          <li>ROE: ${pct(health.roe)} | Op Margin: ${pct(health.operating_margin)}</li>
          <li>Flags: ${(health.flags || []).length ? health.flags.slice(0, 3).join("; ") : "None"}</li>
        </ul>
      </div>` : "",
    edgar: edgar ? `
      <div class="report-section">
        <h4>Appendix: EDGAR</h4>
        <ul class="report-bullets">
          <li>Risk Tag: ${safeText(edgar.risk_tag).toUpperCase()}</li>
          <li>Recent 8-K: ${edgar.recent_8k ? "YES" : "NO"}</li>
          <li>Filing Recency: ${safeText(edgar.filing_recency_days)} day(s)</li>
          <li>Takeaway: ${(edgar.risk_reasons || []).slice(0, 2).join("; ") || "No notable filing risks."}</li>
        </ul>
      </div>` : "",
    mirofish: miro ? `
      <div class="report-section">
        <h4>Appendix: MiroFish <span class="verdict ${sectionVerdicts.mirofish}">${sectionVerdicts.mirofish}</span></h4>
        <ul class="report-bullets">
          <li>Conviction: ${safeText(miro.conviction_score)}</li>
          <li>Continuation: ${pct(miro.continuation_probability, 0)}</li>
          <li>Bull Trap: ${pct(miro.bull_trap_probability, 0)}</li>
          <li>Takeaway: ${safeText(miro.summary || "No summary provided.")}</li>
        </ul>
      </div>` : "",
    synthesis: synthesis ? `
      <div class="report-section">
        <h4>Appendix: Synthesis</h4>
        <div class="report-text">${safeText(synthesis)}</div>
      </div>` : "",
  };
}

function renderScenarioRow(row) {
  const probability = Number.isFinite(Number(row.probability)) ? `${safeNum(row.probability).toFixed(0)}%` : "<span class='muted'>Unavailable</span>";
  const returnPct = Number.isFinite(Number(row.return_pct)) ? `${safeNum(row.return_pct).toFixed(1)}%` : "<span class='muted'>Unavailable</span>";
  const target = row.price_target == null || row.price_target === "" ? "<span class='muted'>Unavailable</span>" : safeText(row.price_target);
  return `
    <tr>
      <td>${safeText(row.name)}</td>
      <td class="mono-nums">${probability}</td>
      <td class="mono-nums">${returnPct}</td>
      <td class="mono-nums">${target}</td>
    </tr>
  `;
}

export function renderReportTabs(data) {
  const tabs = document.getElementById("reportTabs");
  tabs.innerHTML = "";
  if (!data) return;
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Report appendix sections");
  const normalized = normalizeReportPayload(data, {
    portfolioRisk: state.lastPortfolioRiskData,
    portfolioSnapshot: state.lastPortfolioData,
  });
  const available = getAppendixSections(normalized);
  if (!available.includes(state.activeReportTab)) state.activeReportTab = "summary";
  available.forEach((key) => {
    const btn = document.createElement("button");
    const tabId = `report-tab-${key}`;
    const panelId = `report-panel-${key}`;
    const selected = state.activeReportTab === key;
    btn.className = `report-tab ${selected ? "active" : ""}`;
    btn.id = tabId;
    btn.type = "button";
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", selected ? "true" : "false");
    btn.setAttribute("aria-controls", panelId);
    btn.tabIndex = selected ? 0 : -1;
    btn.textContent = key === "summary" ? "Appendix: Summary" : `Appendix: ${key[0].toUpperCase()}${key.slice(1)}`;
    btn.addEventListener("click", () => {
      state.activeReportTab = key;
      renderReportTabs(data);
      renderReportVisual(data);
    });
    tabs.appendChild(btn);
  });
}

export function renderReportVisual(data) {
  const root = document.getElementById("reportVisual");
  if (!root) return;
  if (!data) {
    root.innerHTML = `<div class="report-empty">No report data.</div>`;
    return;
  }

  const normalized = normalizeReportPayload(data, {
    portfolioRisk: state.lastPortfolioRiskData,
    portfolioSnapshot: state.lastPortfolioData,
  });
  const inferred = new Set(normalized.meta?.inferred_fields || []);
  const tab = state.activeReportTab || "summary";
  const blocks = buildAppendixBlocks(normalized);

  const kpis = [
    { label: "Ticker", value: normalized.ticker || "—" },
    { label: "Recommendation", value: normalized.ic_snapshot.recommendation || "Unavailable" },
    {
      label: "Expected Return",
      value: normalized.ic_snapshot.expected_return_base_case != null
        ? `${safeNum(normalized.ic_snapshot.expected_return_base_case).toFixed(1)}%`
        : "Unavailable",
    },
    { label: "Risk Budget", value: normalized.portfolio_fit.risk_budget_impact || "Unavailable" },
  ];

  root.setAttribute("role", "tabpanel");
  root.setAttribute("id", `report-panel-${tab}`);
  root.setAttribute("aria-labelledby", `report-tab-${tab}`);
  root.innerHTML = `
    <div class="report-grid">
      ${kpis.map((k) => `<div class="report-kpi"><div class="label">${k.label}</div><div class="value">${safeText(k.value)}</div></div>`).join("")}
    </div>
    <div class="report-section">
      <h4>IC Snapshot ${inferredBadge(inferred.has("ic_snapshot.recommendation") || inferred.has("ic_snapshot.expected_return_base_case"))}</h4>
      <div class="subtle">Thesis-first decision frame for IC review and position expression.</div>
      ${renderClaimEvidence({
        claim: normalized.thesis.claim,
        evidence: normalized.thesis.evidence,
        confidence: normalized.thesis.confidence,
        falsifier: normalized.thesis.falsifier,
      })}
      <div class="ic-snapshot-grid">
        <div><span class="subtle">Recommendation</span><div>${unavailable(normalized.ic_snapshot.recommendation)}</div></div>
        <div><span class="subtle">Time Horizon</span><div>${unavailable(normalized.ic_snapshot.time_horizon)}</div></div>
        <div><span class="subtle">Expected Return (Base)</span><div>${normalized.ic_snapshot.expected_return_base_case != null ? `${safeNum(normalized.ic_snapshot.expected_return_base_case).toFixed(1)}%` : "<span class='muted'>Unavailable</span>"}</div></div>
        <div><span class="subtle">Confidence</span><div>${confidenceText(normalized.ic_snapshot)} ${inferredBadge(inferred.has("ic_snapshot.confidence"))}</div></div>
        <div><span class="subtle">Position Expression</span><div>${unavailable(normalized.ic_snapshot.suggested_position_size_text)} ${inferredBadge(inferred.has("ic_snapshot.suggested_position_size"))}</div></div>
        <div><span class="subtle">Invalidation</span><div>${unavailable(normalized.ic_snapshot.invalidation_criteria)}</div></div>
      </div>
      <div class="report-split-grid">
        <div>
          <div class="subtle">Top 3 Thesis Points</div>
          <ul class="report-bullets">${(normalized.ic_snapshot.top_thesis_points || []).map((point) => `<li>${safeText(point)}</li>`).join("") || "<li class='muted'>Unavailable</li>"}</ul>
        </div>
        <div>
          <div class="subtle">Top 3 Risks</div>
          <ul class="report-bullets">${(normalized.ic_snapshot.top_risks || []).map((risk) => `<li>${safeText(risk)}</li>`).join("") || "<li class='muted'>Unavailable</li>"}</ul>
        </div>
      </div>
      <div class="subtle">Top Catalysts Timeline</div>
      <ul class="report-bullets">${(normalized.ic_snapshot.catalysts_timeline || []).map((line) => `<li>${safeText(line)}</li>`).join("") || "<li class='muted'>Unavailable</li>"}</ul>
    </div>
    <div class="report-section">
      <h4>Scenario Analysis ${normalized.scenarios.inferred ? "<span class='report-badge inferred'>inferred</span>" : ""}</h4>
      <div class="subtle">Base/Bull/Bear scenarios with probability, EV, and asymmetry context.</div>
      ${normalized.scenarios.warning ? `<div class="report-callout warn">${safeText(normalized.scenarios.warning)}</div>` : ""}
      ${renderClaimEvidence({
        claim: normalized.thesis.claim,
        evidence: normalized.scenarios.sensitivity_bullets?.[0] || "Unavailable",
        confidence: normalized.thesis.confidence,
        falsifier: normalized.ic_snapshot.invalidation_criteria,
      })}
      <div class="table-wrap report-table-wrap">
        <table class="report-scenario-table">
          <thead>
            <tr><th>Scenario</th><th>Probability</th><th>Return Target</th><th>Price Target</th></tr>
          </thead>
          <tbody>${(normalized.scenarios.rows || []).map(renderScenarioRow).join("")}</tbody>
        </table>
      </div>
      <div class="report-scenario-kpis">
        <div><span class="subtle">Expected Value</span><div>${normalized.scenarios.expected_value_pct != null ? `${safeNum(normalized.scenarios.expected_value_pct).toFixed(2)}%` : "<span class='muted'>Unavailable</span>"}</div></div>
        <div><span class="subtle">Upside/Downside Ratio</span><div>${normalized.scenarios.upside_downside_ratio != null ? `${safeNum(normalized.scenarios.upside_downside_ratio).toFixed(2)}x` : "<span class='muted'>Unavailable</span>"}</div></div>
      </div>
      <div class="subtle">Sensitivity Bullets</div>
      <ul class="report-bullets">${(normalized.scenarios.sensitivity_bullets || []).map((line) => `<li>${safeText(line)}</li>`).join("") || "<li class='muted'>Unavailable</li>"}</ul>
    </div>
    <div class="report-section">
      <h4>Portfolio Fit</h4>
      <div class="subtle">Sector overlap, concentration contribution, and risk budget impact.</div>
      ${normalized.portfolio_fit.fallback_message ? `<div class="report-callout warn">${safeText(normalized.portfolio_fit.fallback_message)}</div>` : ""}
      ${renderClaimEvidence({
        claim: `Risk budget: ${normalized.portfolio_fit.risk_budget_impact || "Unavailable"}`,
        evidence: normalized.portfolio_fit.correlation_overlap_proxy || "Portfolio overlap proxy unavailable.",
        confidence: normalized.thesis.confidence,
        falsifier: normalized.ic_snapshot.invalidation_criteria,
      })}
      <ul class="report-bullets">
        <li>Sector overlap: ${normalized.portfolio_fit.sector_overlap_pct != null ? `${safeNum(normalized.portfolio_fit.sector_overlap_pct).toFixed(2)}%` : "<span class='muted'>Unavailable</span>"}</li>
        <li>Concentration contribution: ${normalized.portfolio_fit.concentration_contribution_pct != null ? `${safeNum(normalized.portfolio_fit.concentration_contribution_pct).toFixed(2)}%` : "<span class='muted'>Unavailable</span>"}</li>
        <li>Correlation/overlap proxy: ${unavailable(normalized.portfolio_fit.correlation_overlap_proxy)}</li>
        <li>Risk budget impact hint: ${unavailable(normalized.portfolio_fit.risk_budget_impact)}</li>
        <li>Exposure budget remaining: ${normalized.portfolio_fit.exposure_budget_remaining_pct != null ? `${safeNum(normalized.portfolio_fit.exposure_budget_remaining_pct).toFixed(2)}%` : "<span class='muted'>Unavailable</span>"}</li>
      </ul>
    </div>
    <div class="report-section">
      <h4>Monitoring Plan</h4>
      <div class="subtle">Structured post-trade attribution loop and refresh cadence.</div>
      ${renderClaimEvidence({
        claim: normalized.monitoring_plan.claim,
        evidence: normalized.monitoring_plan.evidence,
        confidence: normalized.monitoring_plan.confidence,
        falsifier: normalized.monitoring_plan.falsifier,
      })}
      <ul class="report-bullets">${(normalized.monitoring_plan.triggers || []).map((line) => `<li>${safeText(line)}</li>`).join("")}</ul>
      <div class="subtle">Review cadence: ${safeText(normalized.monitoring_plan.review_cadence || "Unavailable")}</div>
    </div>
    <div class="report-section">
      <h4>Appendix Section</h4>
      ${blocks[tab] || blocks.summary}
    </div>
  `;
}

export function applyReportViewMode() {
  const raw = document.getElementById("reportOutput");
  const visual = document.getElementById("reportVisual");
  const btn = document.getElementById("toggleReportViewBtn");
  if (!raw || !visual || !btn) return;
  if (state.reportRawView) {
    raw.style.display = "block";
    visual.style.display = "none";
    btn.textContent = "Show Visual";
  } else {
    raw.style.display = "none";
    visual.style.display = "grid";
    btn.textContent = "Show Raw JSON";
  }
}

export async function runReport() {
  if (!smokeChecked) {
    smokeChecked = true;
    const smoke = runReportNormalizationSmokeChecks();
    if (!smoke.ok) {
      logEvent({ kind: "report", severity: "warn", message: "Normalization smoke checks reported non-fatal issues." });
    }
  }

  const ticker = document.getElementById("reportTickerInput").value.trim().toUpperCase();
  if (!ticker) return;
  const section = document.getElementById("reportSection").value.trim();
  const skipMirofish = document.getElementById("skipMirofish").checked;
  const skipEdgar = document.getElementById("skipEdgar").checked;
  const btn = document.getElementById("reportBtn");
  const output = document.getElementById("reportOutput");
  const visual = document.getElementById("reportVisual");

  btn.disabled = true;
  btn.textContent = "Running...";
  output.textContent = "Generating report...";
  visual.innerHTML = `<div class="report-empty">Generating visual report...</div>`;
  updateActionCenter({ title: "Report Running", message: `Generating report for ${ticker}...`, severity: "info" });

  try {
    const qs = new URLSearchParams();
    if (section) qs.set("section", section);
    qs.set("skip_mirofish", String(skipMirofish));
    qs.set("skip_edgar", String(skipEdgar));
    const out = await api.get(`/api/report/${ticker}?${qs.toString()}`, { timeoutMs: 300000 });
    if (!out.ok) {
      output.textContent = out.error || "Report failed.";
      visual.innerHTML = `<div class="report-empty">${safeText(out.error || "Report failed.")}</div>`;
      logEvent({ kind: "report", severity: "error", message: `Report ${ticker} failed: ${out.error}` });
      return;
    }

    state.lastReportData = out.data;
    try {
      const portfolioRiskOut = await api.get("/api/portfolio/risk", { timeoutMs: 20000 });
      state.lastPortfolioRiskData = portfolioRiskOut.ok ? portfolioRiskOut.data : null;
    } catch {
      state.lastPortfolioRiskData = null;
    }

    state.activeReportTab = "summary";
    output.textContent = JSON.stringify(out.data, null, 2);
    renderReportTabs(out.data);
    renderReportVisual(out.data);
    logEvent({ kind: "report", severity: "info", message: `Report complete for ${ticker}${section ? ` (${section})` : ""}.` });
    updateActionCenter({ title: "Report Complete", message: `Full report ready for ${ticker}.`, severity: "success" });
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Report";
  }
}
