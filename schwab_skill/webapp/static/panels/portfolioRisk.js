/**
 * Portfolio Risk dashboard panel (dedicated Risk sub-tab).
 *
 * Figma: Old Logan DS "Portfolio Risk — Success/Partial/Empty/Loading/Error"
 * (file FMWpss3fGm0yzbjiyP3wSx, nodes 70-40 … 70-60).
 *
 * Fetches the unified `/api/portfolio/risk-dashboard` payload and renders:
 *  - toolbar (lookback selector, freshness chip, refresh)
 *  - sticky anchor chips for section navigation
 *  - headline metrics table + correlation heatmap (colors via CSS
 *    `data-corr-bucket`, click a cell to highlight its pair)
 *  - risk contribution (ex-ante vs realized vol, weight-vs-risk chart,
 *    sortable per-name table)
 *  - concentration (HHI, effective N, top-5/top-10) + limit breaches
 *  - stress testing sub-tabs: Historical | Monte Carlo | Tail Risk | FX
 *  - single-name gap stress; sector allocation / closed trades / equity
 *    sparkline in collapsed disclosures
 *
 * Loading uses a skeleton with staged progress copy (the cold build fetches a
 * year of history per ticker). Missing analytics render as em-dashes with
 * `data_quality` notes — the panel never fabricates numbers.
 */

import { api } from "../modules/api.js";
import { safeText, formatMoney, formatDecimal, formatCount } from "../modules/format.js";
import { state } from "../modules/state.js";
import { setResearchPanelStatus } from "../modules/researchStatus.js";
import { buildOperatorAlertHtml } from "../modules/asyncState.js";
import {
  metricValue,
  signedMetric,
  compactMoney,
  renderEquitySparkline,
} from "../modules/portfolioFormat.js";
import { getPortfolioSource, getManualPayload, wirePortfolioSource } from "./portfolioManual.js";
import { refreshPortfolio } from "./portfolio.js";
import { loadBook, resolveBookHash } from "./portfolioBook.js";

const HEATMAP_MAX_TICKERS = 20;
const LOOKBACK_OPTIONS = [
  { value: 60, label: "3M (60d)" },
  { value: 252, label: "1Y (252d)" },
  { value: 756, label: "3Y (756d)" },
];

let progressTimer = null;

function paintRiskSurface(stateName, title, detail, extras = {}) {
  return setResearchPanelStatus({
    stripId: "portfolioStatusStrip",
    snapshotId: "portfolioSnapshot",
    sectionId: "portfolioSection",
    stateName,
    title,
    detail,
    hint: extras.hint || "risk · correlation · stress",
    output: extras.output,
    data: extras.data,
    action: extras.action,
    confidence: extras.confidence,
    lines: extras.lines,
  });
}

function setRiskPanelState(name) {
  document.getElementById("portfolioPanelRisk")?.setAttribute("data-async-state", name);
}

/* ── Toolbar ───────────────────────────────────────────────────── */

function renderToolbar(d) {
  const lookback = state.riskDashboardLookback || 252;
  const options = LOOKBACK_OPTIONS.map(
    (o) => `<option value="${o.value}" ${o.value === lookback ? "selected" : ""}>${o.label}</option>`,
  ).join("");
  let freshness = "";
  if (d && d.cached_at) {
    const at = new Date(d.cached_at);
    const time = Number.isNaN(at.getTime())
      ? ""
      : at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    freshness = `as of ${time}${d.cache_hit ? " · cached" : " · fresh"}`;
  }
  return `
    <div class="risk-toolbar">
      <span class="risk-freshness">${safeText(freshness)}</span>
      <label class="risk-freshness" for="riskLookbackSelect">Lookback</label>
      <select id="riskLookbackSelect" aria-label="Risk analytics lookback window">${options}</select>
      <button id="riskRefreshBtn" class="btn small secondary" type="button">↻ Refresh</button>
    </div>`;
}

function wireToolbar(mount) {
  mount.querySelector("#riskLookbackSelect")?.addEventListener("change", (e) => {
    const v = Number(e.target.value);
    state.riskDashboardLookback = Number.isFinite(v) ? v : 252;
    void loadPortfolioRiskDashboard({ reload: true });
  });
  mount.querySelector("#riskRefreshBtn")?.addEventListener("click", () => {
    void loadPortfolioRiskDashboard({ force: true });
  });
}

/* ── Loading skeleton ──────────────────────────────────────────── */

const PROGRESS_STAGES = [
  { at: 0, label: "Fetching price history for each position…" },
  { at: 18, label: "Computing correlation matrix & risk contribution…" },
  { at: 34, label: "Running historical stress scenarios…" },
  { at: 45, label: "Running Monte Carlo simulation (5,000 paths)…" },
  { at: 58, label: "Almost there — assembling the dashboard…" },
];

function renderLoadingSkeleton(mount, positionsCount) {
  const posLine = positionsCount
    ? `✓ Positions loaded (${formatCount(positionsCount, "0")})`
    : "✓ Positions check";
  mount.innerHTML = `
    ${renderToolbar(null)}
    <div class="risk-progress-steps" role="status" aria-live="polite">
      <span class="risk-progress-step--done">${safeText(posLine)}</span>
      <span class="risk-progress-step--active" id="riskProgressLabel">${PROGRESS_STAGES[0].label}</span>
      <span class="risk-progress-step--pending">First load fetches a year of history per ticker — about a minute. Cached for 5 minutes afterward.</span>
    </div>
    <div class="risk-progress-track"><div class="risk-progress-fill" id="riskProgressFill" style="width:4%"></div></div>
    <div class="risk-skeleton-grid" aria-hidden="true">
      <div class="risk-skeleton-row">
        <div class="risk-skeleton-block" style="flex:1;height:200px"></div>
        <div class="risk-skeleton-block" style="flex:1.4;height:200px"></div>
      </div>
      <div class="risk-skeleton-row">
        <div class="risk-skeleton-block" style="flex:1;height:64px"></div>
        <div class="risk-skeleton-block" style="flex:1;height:64px"></div>
        <div class="risk-skeleton-block" style="flex:1;height:64px"></div>
        <div class="risk-skeleton-block" style="flex:1;height:64px"></div>
      </div>
      <div class="risk-skeleton-block" style="height:110px"></div>
    </div>`;
  wireToolbar(mount);

  const started = Date.now();
  clearInterval(progressTimer);
  progressTimer = setInterval(() => {
    const elapsed = (Date.now() - started) / 1000;
    const label = document.getElementById("riskProgressLabel");
    const fill = document.getElementById("riskProgressFill");
    if (!label || !fill) {
      clearInterval(progressTimer);
      return;
    }
    const stage = [...PROGRESS_STAGES].reverse().find((s) => elapsed >= s.at) || PROGRESS_STAGES[0];
    label.textContent = stage.label;
    fill.style.width = `${Math.min(92, 4 + elapsed * 1.5)}%`;
  }, 2000);
}

function stopProgress() {
  clearInterval(progressTimer);
  progressTimer = null;
}

/* ── Section renderers ─────────────────────────────────────────── */

function renderAnchorChips() {
  const anchors = [
    ["risk-sec-metrics", "Metrics"],
    ["risk-sec-contribution", "Contribution"],
    ["risk-sec-concentration", "Concentration"],
    ["risk-sec-stress", "Stress"],
    ["risk-sec-singlename", "Single-Name"],
  ];
  return `
    <nav class="risk-anchor-chips" aria-label="Risk dashboard sections">
      ${anchors.map(([id, label]) => `<button type="button" class="risk-anchor-chip" data-risk-anchor="${id}">${label}</button>`).join("")}
    </nav>`;
}

function renderMetricsTable(metrics, tail, dq) {
  const m = metrics || {};
  const q = dq || {};
  const obs = Number(m.observations) || 0;
  // Drawdown / total return are measured on whichever equity basis the
  // backend used — real account snapshots vs a modeled backfill. Label it.
  const snapshotDays = Number(q.equity_curve_days) || 0;
  const ddBasis = q.drawdown_source === "snapshots"
    ? `account snapshots, ${snapshotDays}d`
    : "modeled from current weights";
  const annualizedNote = m.annualized_return_pct == null && obs > 0 && obs < 60
    ? " (needs 60+ obs)"
    : "";
  const rows = [
    [`Annualized Return${annualizedNote}`, signedMetric(m.annualized_return_pct, 1, "%"),
      "Geometric annualization of the modeled daily return series. Suppressed under 60 observations because short windows extrapolate absurdly."],
    ["Annualized Volatility", metricValue(m.volatility_ann_pct, 1, "%"),
      "Std dev of modeled daily returns × √252 (current weights applied to history)."],
    ["Sharpe Ratio", metricValue(m.sharpe, 2), "Mean excess return / vol, annualized, same modeled series."],
    ["Sortino Ratio", metricValue(m.sortino, 2), "Downside-deviation Sharpe variant."],
    [`Max Drawdown (${ddBasis})`, metricValue(m.max_drawdown_pct, 1, "%"),
      "Worst peak-to-trough on the equity basis shown in the row label."],
    [`Beta (${safeText(m.benchmark || "SPY")})`, metricValue(m.beta_vs_benchmark, 2),
      "Covariance with the benchmark / benchmark variance."],
    ["VaR 95% (daily)", metricValue(m.var_95_pct, 2, "%"), "5th percentile of modeled daily returns."],
    ["CVaR 95% (daily)", metricValue(tail?.cvar_95_pct, 2, "%"), "Average of returns beyond the VaR cutoff."],
    ["Daily Win Rate (% up days)", metricValue(m.daily_win_rate_pct, 1, "%"), "Share of positive days in the modeled series."],
    [`Total Return (${ddBasis})`, signedMetric(m.total_return_pct, 1, "%"),
      "End-to-start change on the equity basis shown in the row label — not the lookback window unless snapshots cover it."],
  ];
  return `
    <table class="risk-metrics-table">
      <tbody>
        ${rows.map(([label, value, why]) => `
          <tr title="${safeText(why)}">
            <td>${label}</td>
            <td class="mono-nums">${value}</td>
          </tr>`).join("")}
      </tbody>
    </table>
    <div class="muted small">${formatCount(obs, "0")} aligned daily observations · hover a row for methodology</div>`;
}

function renderConfidenceBanner(d) {
  const dq = d.data_quality || {};
  const obs = Number(d.metrics?.observations) || 0;
  const excluded = Number(dq.excluded_weight_pct) || 0;
  const issues = [];
  if (obs > 0 && obs < 60) {
    issues.push(`Only ${obs} aligned daily observations — ratios (Sharpe, VaR, win rate) are statistically weak below ~60.`);
  }
  if (excluded > 5) {
    issues.push(`${metricValue(excluded, 1, "%")} of portfolio weight is excluded from return analytics (missing or short history).`);
  }
  if ((dq.low_coverage_dropped || []).length) {
    issues.push(`Dropped from return math to protect the sample: ${dq.low_coverage_dropped.map(safeText).join(", ")} (history too short vs peers).`);
  }
  if (!issues.length) return "";
  return `
    <div class="risk-recommendation-card" style="border-left-color: var(--warn); margin-bottom: 0.5rem">
      <div class="risk-breach-title" style="color: var(--warn)">Interpret with care</div>
      ${issues.map((i) => `<div class="risk-breach-row">${i}</div>`).join("")}
    </div>`;
}

function corrBucket(value) {
  const v = Number(value);
  if (!Number.isFinite(v)) return "0";
  return String(Math.max(-5, Math.min(5, Math.round(v * 5))));
}

function renderCorrelationHeatmap(correlation, positionsWeighted) {
  const matrix = correlation?.matrix || {};
  let tickers = Object.keys(matrix);
  if (!tickers.length) {
    return `<div class="muted small">Correlation matrix unavailable — needs aligned price history for 2+ positions.</div>`;
  }
  const weightOrder = new Map(
    (positionsWeighted || []).map((p, idx) => [String(p.symbol || "").toUpperCase(), idx]),
  );
  tickers.sort((a, b) => (weightOrder.get(a) ?? 999) - (weightOrder.get(b) ?? 999));
  const truncated = tickers.length > HEATMAP_MAX_TICKERS;
  tickers = tickers.slice(0, HEATMAP_MAX_TICKERS);

  let cells = `<div class="risk-corr-corner"></div>`;
  tickers.forEach((t) => {
    cells += `<div class="risk-corr-label risk-corr-label-col" data-corr-col="${safeText(t)}">${safeText(t)}</div>`;
  });
  tickers.forEach((row) => {
    cells += `<div class="risk-corr-label" data-corr-row="${safeText(row)}">${safeText(row)}</div>`;
    tickers.forEach((col) => {
      const v = row === col ? 1 : matrix[row]?.[col];
      const n = Number(v);
      const text = Number.isFinite(n) ? n.toFixed(2).replace("0.", ".") : "—";
      cells += `<div class="risk-corr-cell mono-nums" data-corr-bucket="${corrBucket(n)}" data-corr-pair="${safeText(row)}|${safeText(col)}" title="${safeText(row)}/${safeText(col)}: ${Number.isFinite(n) ? n.toFixed(4) : "n/a"}">${text}</div>`;
    });
  });
  const breaches = (correlation?.breaches || []).length;
  return `
    <div class="risk-corr-heatmap" style="grid-template-columns: auto repeat(${tickers.length}, minmax(0, 1fr))">${cells}</div>
    <div class="risk-corr-legend">
      <span class="risk-corr-swatch risk-corr-swatch--neg"></span> diversifying (−1)
      <span class="risk-corr-swatch risk-corr-swatch--pos"></span> correlated (+1)
      <span>· avg pair ${metricValue(correlation?.avg_pair_corr, 2)} · ${breaches} breach(es) ≥ ${metricValue(correlation?.threshold, 2)}${truncated ? ` · top ${HEATMAP_MAX_TICKERS} by weight` : ""} · click a cell to trace its pair</span>
    </div>`;
}

function wireHeatmap(mount) {
  const grid = mount.querySelector(".risk-corr-heatmap");
  if (!grid) return;
  grid.addEventListener("click", (e) => {
    const cell = e.target.closest("[data-corr-pair]");
    if (!cell) return;
    const wasHilited = cell.classList.contains("corr-hilite");
    grid.querySelectorAll(".risk-corr-cell").forEach((c) => c.classList.remove("corr-hilite", "corr-dim"));
    if (wasHilited) return;
    const [row, col] = cell.getAttribute("data-corr-pair").split("|");
    grid.querySelectorAll("[data-corr-pair]").forEach((c) => {
      const [r, k] = c.getAttribute("data-corr-pair").split("|");
      const onPair = (r === row || r === col) && (k === row || k === col);
      const onAxis = r === row || r === col || k === row || k === col;
      if (onPair) c.classList.add("corr-hilite");
      else if (!onAxis) c.classList.add("corr-dim");
    });
  });
}

function contributionRowsHtml(rows) {
  return rows.map((r) => `
    <tr>
      <td>${safeText(r.ticker)}</td>
      <td class="mono-nums">${metricValue(r.weight_pct, 1, "%")}</td>
      <td class="mono-nums">${metricValue(r.vol_ann_pct, 0, "%")}</td>
      <td class="mono-nums">${metricValue(r.risk_contrib_ann_pct, 1, "%")}</td>
      <td>
        <div class="risk-share-track" aria-hidden="true"><div class="risk-share-fill" style="width:${Math.min(100, Math.max(0, Number(r.risk_contrib_pct) || 0))}%"></div></div>
        <span class="mono-nums small">${metricValue(r.risk_contrib_pct, 1, "%")}</span>
      </td>
    </tr>`).join("");
}

function renderRiskContribution(rc, equity) {
  if (!rc || !Array.isArray(rc.rows) || !rc.rows.length) {
    return `<div class="muted small">Risk contribution unavailable — needs aligned return history.</div>`;
  }
  const exAnte = Number(rc.ex_ante_vol_pct);
  const realized = Number(rc.realized_vol_pct);
  let consistency = "";
  if (Number.isFinite(exAnte) && Number.isFinite(realized)) {
    const diff = exAnte - realized;
    consistency = `Model vs account consistency: covariance forecast ${metricValue(exAnte, 1)}% vs realized (from daily account snapshots) ${metricValue(realized, 1)}% (${signedMetric(diff, 1)} pts).`;
  } else if (Number.isFinite(exAnte)) {
    consistency = "Realized vol needs 21+ daily account equity snapshots to be an independent check — it accrues automatically each trading day.";
  }

  const kpis = `
    <div class="risk-kpi-row">
      <div class="risk-kpi" title="Portfolio vol implied by current weights and the sample covariance of holdings' returns.">
        <div class="risk-kpi-value">${metricValue(rc.ex_ante_vol_pct, 1, "%")}</div>
        <div class="risk-kpi-label">Ex-Ante Vol (model forecast)</div>
      </div>
      <div class="risk-kpi" title="Vol of actual daily account equity changes (snapshots) — independent of the model.">
        <div class="risk-kpi-value">${metricValue(rc.realized_vol_pct, 1, "%")}</div>
        <div class="risk-kpi-label">Realized Vol (account snapshots)</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${formatMoney(equity)}</div>
        <div class="risk-kpi-label">Portfolio Equity</div>
      </div>
    </div>`;

  const maxVal = Math.max(
    1,
    ...rc.rows.map((r) => Math.max(Number(r.weight_pct) || 0, Number(r.risk_contrib_pct) || 0)),
  );
  const chart = rc.rows.slice(0, 20).map((r) => {
    const w = Math.max(0, Number(r.weight_pct) || 0);
    const risk = Number(r.risk_contrib_pct) || 0;
    return `
      <div class="risk-wr-col" title="${safeText(r.ticker)}: weight ${metricValue(r.weight_pct, 1)}% · risk ${metricValue(r.risk_contrib_pct, 1)}%">
        <div class="risk-wr-bars">
          <div class="risk-wr-bar risk-wr-bar-weight" style="height:${Math.round((w / maxVal) * 100)}%"></div>
          <div class="risk-wr-bar risk-wr-bar-risk" style="height:${Math.max(0, Math.round((risk / maxVal) * 100))}%"></div>
        </div>
        <div class="risk-wr-tick">${safeText(r.ticker)}</div>
      </div>`;
  }).join("");

  const top = rc.rows[0];
  const callout = top && Number(top.risk_contrib_pct) > 1.75 * (Number(top.weight_pct) || 0) && Number(top.weight_pct) > 5
    ? `<div class="risk-breach-list"><div class="risk-breach-row">${safeText(top.ticker)} is ${metricValue(top.weight_pct, 0)}% of weight but ${metricValue(top.risk_contrib_pct, 0)}% of portfolio risk — its standalone vol is ${metricValue(top.vol_ann_pct, 0)}% annualized.</div></div>`
    : "";

  const table = `
    <div class="table-wrap">
      <table class="risk-metrics-table" id="riskContribTable">
        <thead>
          <tr>
            <th>Ticker</th>
            <th data-risk-sort="weight_pct" title="Sort by weight">Weight % ↕</th>
            <th data-risk-sort="vol_ann_pct" title="Sort by standalone vol">Vol (ann) % ↕</th>
            <th data-risk-sort="risk_contrib_ann_pct" title="Sort by risk contribution">Risk Contrib (ann) % ↕</th>
            <th data-risk-sort="risk_contrib_pct" title="Sort by share of risk">% of Risk ↕</th>
          </tr>
        </thead>
        <tbody>${contributionRowsHtml(rc.rows)}</tbody>
      </table>
    </div>`;

  return `
    ${kpis}
    ${consistency ? `<div class="muted small" style="margin-bottom:0.5rem">${consistency}</div>` : ""}
    <div class="risk-wr-legend muted small"><span class="risk-wr-swatch risk-wr-bar-weight"></span> Weight % <span class="risk-wr-swatch risk-wr-bar-risk"></span> Risk %</div>
    <div class="risk-wr-chart">${chart}</div>
    ${callout}
    ${table}`;
}

function wireContributionSort(mount, rows) {
  const table = mount.querySelector("#riskContribTable");
  if (!table || !Array.isArray(rows)) return;
  let currentKey = "risk_contrib_pct";
  let descending = true;
  table.querySelectorAll("[data-risk-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.getAttribute("data-risk-sort");
      descending = key === currentKey ? !descending : true;
      currentKey = key;
      const sorted = [...rows].sort((a, b) => {
        const av = Number(a[key]) || 0;
        const bv = Number(b[key]) || 0;
        return descending ? bv - av : av - bv;
      });
      const tbody = table.querySelector("tbody");
      if (tbody) tbody.innerHTML = contributionRowsHtml(sorted);
    });
  });
}

function renderConcentration(conc) {
  const c = conc || {};
  const kpis = `
    <div class="risk-kpi-row">
      <div class="risk-kpi">
        <div class="risk-kpi-value">${metricValue(c.hhi, 3)}</div>
        <div class="risk-kpi-label">Herfindahl Index</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${metricValue(c.effective_n, 1)}</div>
        <div class="risk-kpi-label">Effective N (diversification eq.)</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${metricValue(c.top_5_pct, 1, "%")}</div>
        <div class="risk-kpi-label">Top 5 of NAV</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${metricValue(c.top_10_pct, 1, "%")}</div>
        <div class="risk-kpi-label">Top 10 of NAV</div>
      </div>
    </div>`;
  const breaches = Array.isArray(c.breaches) ? c.breaches : [];
  const breachBox = breaches.length
    ? `<div class="risk-breach-list">
        <div class="risk-breach-title">Limit breaches (${breaches.length})</div>
        ${breaches.map((b) => `<div class="risk-breach-row">⚠ ${safeText(b.message)}</div>`).join("")}
      </div>`
    : `<div class="muted small">No single-name, sector, or country limit breaches.</div>`;
  return kpis + breachBox;
}

function renderHistoricalStress(rows, equity) {
  if (!Array.isArray(rows) || !rows.length) return `<div class="muted small">No stress scenarios available.</div>`;
  const chart = rows.map((r) => {
    const impact = Number(r.portfolio_impact_pct);
    const h = Number.isFinite(impact) ? Math.min(100, Math.abs(impact) * 1.4) : 0;
    const cls = !Number.isFinite(impact)
      ? "risk-stress-bar-na"
      : impact <= -40 ? "risk-stress-bar-severe" : impact >= 0 ? "risk-stress-bar-up" : "risk-stress-bar-down";
    return `
      <div class="risk-stress-col" title="${safeText(r.scenario)}: ${signedMetric(r.portfolio_impact_pct, 1)}%">
        <div class="risk-stress-bar-slot"><div class="risk-stress-bar ${cls}" style="height:${h.toFixed(0)}%"></div></div>
        <div class="risk-stress-tick">${safeText(r.scenario)}</div>
      </div>`;
  }).join("");

  const table = `
    <div class="table-wrap">
      <table class="risk-metrics-table">
        <thead>
          <tr><th>Scenario</th><th>Market Move</th><th>Portfolio Impact</th><th>Stressed NAV</th><th>P&amp;L</th><th>Description</th></tr>
        </thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${safeText(r.scenario)}${r.scenario_type === "hypothetical" ? ` <span class="risk-tag-hypothetical">hypothetical</span>` : ""}</td>
              <td class="mono-nums">${signedMetric(r.market_move_pct, 1, "%")}</td>
              <td class="mono-nums" style="color:${Number(r.portfolio_impact_pct) < 0 ? "var(--bad)" : "var(--good)"}">${signedMetric(r.portfolio_impact_pct, 1, "%")}</td>
              <td class="mono-nums">${r.stressed_nav != null ? formatMoney(r.stressed_nav) : "—"}</td>
              <td class="mono-nums">${r.pnl != null ? compactMoney(r.pnl) : "—"}</td>
              <td class="muted small">${safeText(r.description)}${r.method === "window_replay" ? " (replayed from actual holdings history)" : ""}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
  const anyBetaScaled = rows.some((r) => r.method === "beta_scaled");
  return `
    <div class="risk-stress-chart">${chart}</div>
    ${table}
    ${anyBetaScaled ? `<div class="muted small">Beta-scaled impacts = portfolio beta × scenario market move (current holdings lack price history for the window).</div>` : ""}
    ${equity ? "" : `<div class="muted small">Equity unavailable — dollar P&amp;L omitted.</div>`}`;
}

function renderMonteCarlo(mc) {
  if (!mc || !mc.simulations) {
    return `<div class="muted small">Monte Carlo VaR unavailable — needs 20+ days of aligned return history.</div>`;
  }
  return `
    <div class="risk-kpi-row">
      <div class="risk-kpi">
        <div class="risk-kpi-value">${metricValue(mc.var_95_pct, 2, "%")}</div>
        <div class="risk-kpi-label">VaR 95% (1-day)</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${metricValue(mc.var_99_pct, 2, "%")}</div>
        <div class="risk-kpi-label">VaR 99% (1-day)</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${metricValue(mc.cvar_95_pct, 2, "%")}</div>
        <div class="risk-kpi-label">CVaR 95%</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${mc.var_95_pnl != null ? compactMoney(mc.var_95_pnl) : "—"}</div>
        <div class="risk-kpi-label">VaR 95% ($)</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${mc.var_99_pnl != null ? compactMoney(mc.var_99_pnl) : "—"}</div>
        <div class="risk-kpi-label">VaR 99% ($)</div>
      </div>
    </div>
    <div class="muted small">Parametric simulation: ${formatCount(mc.simulations, "0")} correlated paths (Cholesky on sample covariance), current weights.</div>`;
}

function renderTailRisk(tail) {
  const t = tail || {};
  if (t.var_95_pct == null && t.worst_day_pct == null) {
    return `<div class="muted small">Tail risk unavailable — needs 20+ days of portfolio return history.</div>`;
  }
  const rows = [
    ["Historical VaR 95% (daily)", metricValue(t.var_95_pct, 2, "%")],
    ["Historical VaR 99% (daily)", metricValue(t.var_99_pct, 2, "%")],
    ["CVaR 95% (expected shortfall)", metricValue(t.cvar_95_pct, 2, "%")],
    ["CVaR 99%", metricValue(t.cvar_99_pct, 2, "%")],
    ["Worst Day", metricValue(t.worst_day_pct, 2, "%")],
    ["Best Day", metricValue(t.best_day_pct, 2, "%")],
    ["Skew", metricValue(t.skew, 2)],
    ["Excess Kurtosis", metricValue(t.kurtosis, 2)],
  ];
  return `
    <table class="risk-metrics-table">
      <tbody>${rows.map(([l, v]) => `<tr><td>${l}</td><td class="mono-nums">${v}</td></tr>`).join("")}</tbody>
    </table>
    <div class="muted small">${formatCount(t.observations, "0")} daily observations</div>`;
}

function renderFxStress(fx, countryExposure) {
  if (!fx) return `<div class="muted small">FX stress unavailable.</div>`;
  const nonUsd = Number(fx.non_usd_weight_pct);
  if (!Number.isFinite(nonUsd) || nonUsd <= 0) {
    const unresolved = (fx.unresolved_tickers || []).length;
    return `<div class="muted small">All resolved holdings are US-domiciled — no FX/country stress to report.${unresolved ? ` ${unresolved} ticker(s) could not be resolved to a country.` : ""}</div>`;
  }
  const kpis = `
    <div class="risk-kpi-row">
      <div class="risk-kpi">
        <div class="risk-kpi-value">${metricValue(fx.non_usd_weight_pct, 1, "%")}</div>
        <div class="risk-kpi-label">Non-US Weight of NAV</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value" style="color:var(--bad)">${signedMetric(fx.scenario_impact_pct, 2, "%")}</div>
        <div class="risk-kpi-label">Scenario Impact (per-country shock)</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value" style="color:var(--bad)">${fx.scenario_pnl != null ? compactMoney(fx.scenario_pnl) : "—"}</div>
        <div class="risk-kpi-label">Scenario P&amp;L</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value" style="color:var(--bad)">${fx.broad_em_impact_pnl != null ? compactMoney(fx.broad_em_impact_pnl) : "—"}</div>
        <div class="risk-kpi-label">Broad EM ${metricValue(fx.broad_em_shock_pct, 0, "%")} uniform shock</div>
      </div>
    </div>`;
  const nameByCode = new Map((countryExposure || []).map((c) => [c.country, c.country_name]));
  const table = `
    <div class="table-wrap">
      <table class="risk-metrics-table">
        <thead><tr><th>Country</th><th>Exposure %</th><th>FX Shock %</th></tr></thead>
        <tbody>
          ${(fx.by_country || []).map((row) => `
            <tr>
              <td>${safeText(nameByCode.get(row.country) || row.country)}</td>
              <td class="mono-nums">${metricValue(row.exposure_pct, 2, "%")}</td>
              <td class="mono-nums">${metricValue(row.fx_shock_pct, 0, "%")}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
  const unresolved = fx.unresolved_tickers || [];
  return `${kpis}${table}${unresolved.length ? `<div class="muted small">Unresolved country for: ${unresolved.map(safeText).join(", ")}</div>` : ""}`;
}

function renderSingleNameStress(rows) {
  if (!Array.isArray(rows) || !rows.length) {
    return `<div class="muted small">No positions large enough for single-name stress.</div>`;
  }
  return `
    <div class="table-wrap">
      <table class="risk-metrics-table">
        <thead>
          <tr><th>Scenario</th><th>Ticker</th><th>Gap</th><th>Weight</th><th>Port Impact</th><th>P&amp;L ($)</th></tr>
        </thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${safeText(r.scenario)}</td>
              <td>${safeText(r.ticker)}</td>
              <td class="mono-nums">${metricValue(r.gap_pct, 0, "%")}</td>
              <td class="mono-nums">${metricValue(r.weight_pct, 1, "%")}</td>
              <td class="mono-nums" style="color:var(--bad)">${signedMetric(r.portfolio_impact_pct, 2, "%")}</td>
              <td class="mono-nums">${r.pnl != null ? compactMoney(r.pnl) : "—"}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
}

function renderSectorAllocation(sectors) {
  if (!Array.isArray(sectors) || !sectors.length) return "";
  const maxSector = Math.max(1, ...sectors.map((s) => Number(s.weight_pct) || 0));
  return `
    <div class="risk-sector-bars">
      ${sectors.map((s) => `
        <div class="risk-sector-row">
          <span class="risk-sector-name">${safeText(s.sector)}</span>
          <div class="risk-sector-bar-track">
            <div class="risk-sector-bar-fill" style="width:${Math.max(2, Math.round(((Number(s.weight_pct) || 0) / maxSector) * 100))}%"></div>
          </div>
          <span class="risk-sector-pct mono-nums">${formatDecimal(s.weight_pct, 2)}%</span>
          <span class="risk-sector-val muted mono-nums">${formatMoney(s.value)}</span>
        </div>`).join("")}
    </div>`;
}

function renderClosedTrades(closed) {
  const c = closed || {};
  if (!c.trades) return `<div class="muted small">No closed trades recorded yet.</div>`;
  return `
    <div class="risk-kpi-row">
      <div class="risk-kpi"><div class="risk-kpi-value">${safeText(c.profit_factor ?? "—")}</div><div class="risk-kpi-label">Profit Factor</div></div>
      <div class="risk-kpi"><div class="risk-kpi-value">${metricValue((c.win_rate ?? NaN) * 100, 1, "%")}</div><div class="risk-kpi-label">Win Rate (${safeText(c.trades || 0)})</div></div>
      <div class="risk-kpi"><div class="risk-kpi-value">${metricValue(c.expectancy_pct, 2, "%")}</div><div class="risk-kpi-label">Expectancy</div></div>
      <div class="risk-kpi"><div class="risk-kpi-value">${metricValue(c.max_drawdown_pct, 1, "%")}</div><div class="risk-kpi-label">Trade DD</div></div>
    </div>`;
}

function renderDataQuality(dq, provenance) {
  const warnings = [];
  if ((dq?.missing_tickers || []).length) warnings.push(`Missing history: ${(dq.missing_tickers || []).map(safeText).join(", ")}`);
  if ((dq?.insufficient_history || []).length) warnings.push(`Short history: ${(dq.insufficient_history || []).map(safeText).join(", ")}`);
  if ((dq?.excluded_options || []).length) warnings.push(`${dq.excluded_options.length} option contract(s) excluded from return/correlation math (kept in positions and concentration): ${(dq.excluded_options || []).map(safeText).join(", ")}`);
  if (Number(dq?.excluded_weight_pct || 0) > 0) warnings.push(`${metricValue(dq.excluded_weight_pct, 1, "%")} of weight excluded from return analytics`);
  if ((dq?.country_unresolved || []).length) warnings.push(`No country profile: ${(dq.country_unresolved || []).map(safeText).join(", ")}`);
  if (dq?.drawdown_source === "current_weight_backfill") warnings.push("Drawdown curve backfilled with current weights (snapshots not yet accumulated)");
  const conf = provenance?.confidence ? ` · confidence: ${safeText(provenance.confidence)}` : "";
  if (!warnings.length) return `<div class="muted small">Data quality: clean${conf}</div>`;
  return `<div class="muted small" style="color:var(--warn)">Data quality: ${warnings.join(" · ")}${conf}</div>`;
}

function renderDashboard(d) {
  const stress = d.stress || {};
  const riskFlags = Array.isArray(d.risk_flags) ? d.risk_flags : [];
  return `
    ${renderToolbar(d)}
    ${renderAnchorChips()}
    <div class="risk-dashboard">
      ${renderConfidenceBanner(d)}
      <div class="risk-section-title" id="risk-sec-metrics">Metrics &amp; Correlation</div>
      <div class="risk-two-col">
        <div>${renderMetricsTable(d.metrics, stress.tail_risk, d.data_quality)}</div>
        <div>${renderCorrelationHeatmap(d.correlation, d.positions_weighted)}</div>
      </div>

      <div class="risk-section-title" id="risk-sec-contribution">Risk Contribution — where the volatility actually comes from</div>
      ${renderRiskContribution(d.risk_contribution, d.equity)}

      <div class="risk-section-title" id="risk-sec-concentration">Concentration — HHI · effective-N · limit breaches</div>
      ${renderConcentration(d.concentration)}
      ${riskFlags.length ? `<div class="risk-breach-list"><div class="risk-breach-title">Operational risk flags</div>${riskFlags.map((f) => `<div class="risk-breach-row">⚠ ${safeText(f)}</div>`).join("")}</div>` : ""}

      <div class="risk-section-title" id="risk-sec-stress">Stress Testing — scenario analysis and tail risk</div>
      <div class="tab-bar risk-stress-tabs" role="tablist" aria-label="Stress test views">
        <button type="button" class="tab-btn tab-btn-active" data-stress-tab="historical" role="tab" aria-selected="true">Historical</button>
        <button type="button" class="tab-btn" data-stress-tab="montecarlo" role="tab" aria-selected="false">Monte Carlo</button>
        <button type="button" class="tab-btn" data-stress-tab="tail" role="tab" aria-selected="false">Tail Risk</button>
        <button type="button" class="tab-btn" data-stress-tab="fx" role="tab" aria-selected="false">FX</button>
      </div>
      <div data-stress-panel="historical">${renderHistoricalStress(stress.historical, d.equity)}</div>
      <div data-stress-panel="montecarlo" class="hidden">${renderMonteCarlo(stress.monte_carlo)}</div>
      <div data-stress-panel="tail" class="hidden">${renderTailRisk(stress.tail_risk)}</div>
      <div data-stress-panel="fx" class="hidden">${renderFxStress(stress.fx, d.country_exposure)}</div>

      <div class="risk-section-title" id="risk-sec-singlename">Single-Name Stress — idiosyncratic gap-down losses</div>
      ${renderSingleNameStress(stress.single_name)}

      <details class="panel-disclosure">
        <summary class="panel-disclosure-summary">Sector Allocation <span class="disclosure-hint muted">weights by sector</span></summary>
        ${renderSectorAllocation(d.sector_allocation)}
      </details>
      <details class="panel-disclosure">
        <summary class="panel-disclosure-summary">Closed Trades · Equity Curve <span class="disclosure-hint muted">performance attribution</span></summary>
        ${renderClosedTrades(d.closed_trades)}
        ${renderEquitySparkline(d.equity_curve)}
      </details>
      ${renderDataQuality(d.data_quality, d.provenance)}
    </div>`;
}

/* ── Fetch + wiring ────────────────────────────────────────────── */

function wireStressTabs(mount) {
  mount.querySelectorAll("[data-stress-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.getAttribute("data-stress-tab");
      mount.querySelectorAll("[data-stress-tab]").forEach((b) => {
        const active = b === btn;
        b.classList.toggle("tab-btn-active", active);
        b.setAttribute("aria-selected", active ? "true" : "false");
      });
      mount.querySelectorAll("[data-stress-panel]").forEach((panel) => {
        panel.classList.toggle("hidden", panel.getAttribute("data-stress-panel") !== target);
      });
    });
  });
}

function wireAnchors(mount) {
  mount.querySelectorAll("[data-risk-anchor]").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.getElementById(chip.getAttribute("data-risk-anchor"))?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  });
}

export async function loadPortfolioRiskDashboard(options = {}) {
  const mount = document.getElementById("portfolioRiskMount");
  if (!mount) return;
  const lookback = state.riskDashboardLookback || 252;
  const source = getPortfolioSource();
  const cached = state.lastPortfolioRiskDashboard;
  if (!options.force && !options.reload && cached && cached._lookback === lookback && cached._source === source) {
    return; // rendered this session at this lookback+source; instant tab re-entry
  }

  // Manual source with an empty book: prompt instead of hitting the API.
  const manualPayload = source === "manual" ? getManualPayload() : null;
  if (source === "manual" && !manualPayload) {
    state.lastPortfolioRiskDashboard = null;
    setRiskPanelState("empty");
    mount.innerHTML = `
      <div class="empty-state-cell" style="padding:2rem 0;text-align:center">
        <div style="font-weight:700;font-size:1rem">Manual book is empty</div>
        <div class="muted" style="max-width:420px;margin:0.5rem auto 0.75rem">Add tickers and share counts on the Positions tab, then reopen Risk to build the dashboard.</div>
        <button type="button" class="btn small secondary" id="riskGoManualPositionsBtn">Open Positions</button>
      </div>`;
    mount.querySelector("#riskGoManualPositionsBtn")?.addEventListener("click", () => {
      document.getElementById("portfolioTabPositions")?.click();
    });
    paintRiskSurface("empty", "Manual book is empty.", "Add tickers on the Positions tab to build risk analytics.", {
      output: { value: "None", sub: "manual book" },
      data: { value: "Local", sub: "this browser" },
      action: { value: "Add", sub: "tickers" },
      confidence: 0,
    });
    return;
  }
  setRiskPanelState("loading");
  renderLoadingSkeleton(mount, state.lastPortfolioData?.positions_count);
  paintRiskSurface(
    "loading",
    "Building risk dashboard.",
    "Fetching price history, computing correlation, risk contribution, and stress tests.",
    {
      output: { value: "…", sub: "risk dashboard" },
      data: { value: "…", sub: "prices + profiles" },
      action: { value: "Wait", sub: "hold" },
      confidence: 34,
    },
  );

  let out;
  if (source === "manual") {
    out = await api.post(
      "/api/portfolio/risk-dashboard/manual",
      { ...manualPayload, lookback_days: lookback, force: Boolean(options.force) },
      { timeoutMs: 120000 },
    );
  } else {
    const params = new URLSearchParams({ lookback_days: String(lookback) });
    if (options.force) params.set("force", "true");
    out = await api.get(`/api/portfolio/risk-dashboard?${params}`, { timeoutMs: 120000 });
  }
  stopProgress();
  if (!out.ok) {
    state.lastPortfolioRiskDashboard = null;
    setRiskPanelState("error");
    const reason = safeText(out.user_message || out.error || "fetch failed");
    const hint = source === "manual"
      ? "Fix or remove flagged tickers on the Positions tab (or wait out the one-build-per-minute limit), then retry."
      : out.status === 409
        ? "Link Schwab account + market data in Settings, then retry."
        : out.status === 401
          ? "Sign in first to load tenant-scoped analytics."
          : "Retry in a moment. If this persists, check backend logs.";
    mount.innerHTML = `
      ${renderToolbar(null)}
      ${buildOperatorAlertHtml({
        tone: "bad",
        headline: "Risk dashboard unavailable",
        detail: `${reason} ${hint}`,
        retry: true,
        retryAttr: "data-risk-dashboard-retry",
      })}
      <div class="muted small" style="margin-top:0.5rem">The Positions tab still works while risk analytics are unavailable.</div>`;
    wireToolbar(mount);
    mount.querySelector("[data-risk-dashboard-retry]")?.addEventListener("click", () => void loadPortfolioRiskDashboard({ force: true }));
    paintRiskSurface("partial", "Positions loaded; risk dashboard unavailable.", hint, {
      output: { value: "Partial", sub: "positions only" },
      data: { value: "Limited", sub: "risk feed" },
      action: { value: "Review", sub: "retry risk" },
      confidence: 50,
    });
    return;
  }

  const d = out.data || {};
  d._lookback = lookback;
  d._source = source;
  state.lastPortfolioRiskDashboard = d;
  if (!d.position_count) {
    setRiskPanelState("empty");
    mount.innerHTML = `
      ${renderToolbar(d)}
      <div class="empty-state-cell" style="padding:2rem 0;text-align:center">
        <div style="font-weight:700;font-size:1rem">No positions to analyze</div>
        <div class="muted" style="max-width:420px;margin:0.5rem auto 0.75rem">Risk analytics need at least one open position. When you add positions, target 3–5 sectors and keep each name below the single-name limit.</div>
        <a href="#settingsSection" class="btn small secondary">Open Setup</a>
      </div>`;
    wireToolbar(mount);
    paintRiskSurface("empty", "No positions to analyze.", "Risk analytics need at least one open position.", {
      output: { value: "None", sub: "risk" },
      data: { value: "—", sub: "weights" },
      action: { value: "Wait", sub: "add positions" },
      confidence: 0,
    });
    return;
  }

  const breachCount = (d.concentration?.breaches || []).length + (d.risk_flags || []).length;
  setRiskPanelState(breachCount ? "stale" : "success");
  mount.innerHTML = renderDashboard(d);
  wireToolbar(mount);
  wireStressTabs(mount);
  wireAnchors(mount);
  wireHeatmap(mount);
  wireContributionSort(mount, d.risk_contribution?.rows || []);

  paintRiskSurface(
    breachCount ? "partial" : "success",
    `Risk dashboard ready — ${safeText(d.position_count)} position(s).`,
    breachCount
      ? `${breachCount} limit breach(es)/flag(s) need review. Vol ${metricValue(d.metrics?.volatility_ann_pct, 1)}% · Sharpe ${metricValue(d.metrics?.sharpe, 2)}.`
      : `Vol ${metricValue(d.metrics?.volatility_ann_pct, 1)}% · Sharpe ${metricValue(d.metrics?.sharpe, 2)} · VaR95 ${metricValue(d.metrics?.var_95_pct, 2)}%.`,
    {
      output: { value: "Ready", sub: "risk dashboard" },
      data: { value: d.cache_hit ? "Cached" : "Fresh", sub: "prices + profiles" },
      action: {
        value: breachCount ? "Review" : "Pass",
        sub: breachCount ? "limit breaches" : "within limits",
        tone: breachCount ? "warn" : "success",
      },
      confidence: breachCount ? 62 : 88,
    },
  );
}

/** Wire the Positions | Risk sub-tab bar inside the portfolio card. */
export function wirePortfolioSubtabs() {
  // Schwab | Manual source toggle: switching sources invalidates the cached
  // risk pack and repaints whichever panel is visible.
  wirePortfolioSource(() => {
    state.lastPortfolioRiskDashboard = null;
    void refreshPortfolio();
    const riskVisible = !document.getElementById("portfolioPanelRisk")?.classList.contains("hidden");
    if (riskVisible) void loadPortfolioRiskDashboard({ reload: true });
  });
  const tabs = [
    { btn: "portfolioTabPositions", panel: "portfolioPanelPositions" },
    { btn: "portfolioTabRisk", panel: "portfolioPanelRisk" },
    { btn: "portfolioTabBook", panel: "portfolioPanelBook" },
  ];
  tabs.forEach(({ btn }) => {
    const el = document.getElementById(btn);
    if (!el) return;
    el.addEventListener("click", () => {
      tabs.forEach((t) => {
        const active = t.btn === btn;
        document.getElementById(t.btn)?.classList.toggle("tab-btn-active", active);
        document.getElementById(t.btn)?.setAttribute("aria-selected", active ? "true" : "false");
        document.getElementById(t.panel)?.classList.toggle("hidden", !active);
      });
      if (btn === "portfolioTabRisk") void loadPortfolioRiskDashboard();
      if (btn === "portfolioTabBook") void loadBook();
    });
  });
  // Deep link: router resolves ?section=risk to #portfolioPanelRisk. The
  // router applies the hash via replaceState (no hashchange event), so it
  // dispatches route_hash_applied; listen for both plus the initial state.
  const maybeOpenRisk = () => {
    if (window.location.hash === "#portfolioPanelRisk") openPortfolioRiskTab();
  };
  const maybeOpenBook = () => {
    const id = (window.location.hash || "").replace(/^#/, "");
    resolveBookHash(id);
  };
  window.addEventListener("hashchange", maybeOpenRisk);
  window.addEventListener("hashchange", maybeOpenBook);
  window.addEventListener("route_hash_applied", (e) => {
    if (e.detail?.id === "portfolioPanelRisk") openPortfolioRiskTab();
    if (e.detail?.id) resolveBookHash(e.detail.id);
  });
  maybeOpenRisk();
  maybeOpenBook();
}

/** Programmatic navigation target for router alias ?section=risk. */
export function openPortfolioRiskTab() {
  document.getElementById("portfolioTabRisk")?.click();
  document.getElementById("portfolioSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
}
