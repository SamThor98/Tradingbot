/**
 * Portfolio panel: positions table + risk analytics card.
 *
 * `refreshPortfolio` paints the positions table and shows setup guidance
 * when no account positions are available.
 *
 * `loadPortfolioRisk` paints the risk analytics block underneath
 * (concentration, sector allocation, position weights, day-PL movers,
 * and a single high-level recommendation).
 */

import { api } from "../modules/api.js";
import {
  safeText,
  safeNum,
  formatMoney,
  formatDecimal,
  formatCount,
  formatSignedDelta,
} from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import { state } from "../modules/state.js";
import { applyFreshness, markUnavailable, clearUnavailable } from "../modules/freshness.js";
import { setResearchStatusStrip } from "../modules/researchStatus.js";

export async function refreshPortfolio() {
  const card = document.getElementById("portfolioSection");
  const body = document.getElementById("portfolioBody");
  const meta = document.getElementById("portfolioMeta");
  if (!body) return;
  if (card) card.setAttribute("data-async-state", "loading");
  setResearchStatusStrip(
    "portfolioStatusStrip",
    "loading",
    "Loading portfolio.",
    "Fetching positions, market value, and account exposure.",
  );
  body.innerHTML = `<tr><td colspan="5" class="muted">
    <div class="async-state async-state--loading">
      <span class="async-spinner" aria-hidden="true"></span>
      <span>Loading positions…</span>
    </div>
  </td></tr>`;
  const out = await api.get("/api/portfolio");
  if (!out.ok) {
    state.lastPortfolioData = null;
    const reason = safeText(out.user_message || out.error || "fetch failed");
    // 401 → signed-out banner. 409 → "link Schwab in Settings". Other → generic.
    if (out.status === 401) {
      if (card) card.setAttribute("data-async-state", "signed_out");
      setResearchStatusStrip(
        "portfolioStatusStrip",
        "error",
        "Sign in required.",
        "Sign in to load tenant-scoped portfolio data.",
      );
      if (meta) {
        markUnavailable(meta, "signed out");
        meta.textContent = "Sign in to load positions.";
      }
      body.innerHTML = `<tr><td colspan="5">
        <div class="signed-out-banner" role="status">
          <strong>Signed out.</strong>
          <span>Sign in to load tenant-scoped portfolio data.</span>
          <a class="btn small secondary" href="#supabaseAuthBlock">Sign in</a>
        </div>
      </td></tr>`;
      return;
    }
    if (card) card.setAttribute("data-async-state", "error");
    setResearchStatusStrip(
      "portfolioStatusStrip",
      "error",
      "Portfolio unavailable.",
      reason,
    );
    if (meta) {
      markUnavailable(meta, reason);
      meta.textContent = `Portfolio unavailable: ${reason}`;
    }
    const hint = out.status === 409
      ? "Link Schwab account + market data in Settings, then retry."
      : "";
    body.innerHTML = `<tr><td colspan="5">
      <div class="async-state async-state--error" role="alert">
        <div>
          <div>${reason}</div>
          ${hint ? `<div class="muted small">${safeText(hint)}</div>` : ""}
        </div>
        <button type="button" class="btn small secondary" data-portfolio-retry>Retry</button>
      </div>
    </td></tr>`;
    body.querySelector("[data-portfolio-retry]")?.addEventListener("click", () => void refreshPortfolio());
    logEvent({ kind: "system", severity: "warn", message: `Portfolio load failed: ${out.error}` });
    return;
  }
  const data = out.data || {};
  state.lastPortfolioData = data;
  if (meta) {
    clearUnavailable(meta);
    meta.textContent = `${formatCount(data.positions_count, "0")} position(s) • ${formatMoney(data.total_market_value)}`;
  }
  body.innerHTML = "";
  if (!Array.isArray(data.positions) || !data.positions.length) {
    if (card) card.setAttribute("data-async-state", "empty");
    setResearchStatusStrip(
      "portfolioStatusStrip",
      "empty",
      "No open positions.",
      "Connect Schwab or add positions before analyzing exposure.",
    );
    body.innerHTML = `
      <tr>
        <td colspan="5" class="muted">
          <div class="empty-state-cell">
            <svg class="empty-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M12 3v18M3 12h18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
              <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.5"/>
            </svg>
            <div>No open positions in this account yet.</div>
            <a href="#settingsSection" class="btn small secondary">Open Setup</a>
          </div>
        </td>
      </tr>
    `;
    return;
  }
  if (card) card.setAttribute("data-async-state", "success");
  setResearchStatusStrip(
    "portfolioStatusStrip",
    "success",
    `${formatCount(data.positions_count, "0")} position(s) loaded.`,
    `${formatMoney(data.total_market_value)} total market value. Open Risk Analysis for concentration.`,
  );
  data.positions.slice(0, 25).forEach((p) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${safeText(p.symbol)}</td>
      <td>${formatCount(p.qty, "—")}</td>
      <td>${formatMoney(p.last)}</td>
      <td>${formatMoney(p.market_value)}</td>
      <td>${formatSignedDelta(p.pl_pct, (n) => formatDecimal(n, 2, "0.00"))}%</td>
    `;
    body.appendChild(tr);
  });
  // Provenance label: portfolio meta gets a freshness chip too.
  if (meta && !meta.dataset.freshAttached) {
    meta.dataset.freshAttached = "1";
    const fresh = document.createElement("small");
    fresh.style.display = "block";
    meta.appendChild(fresh);
    applyFreshness(fresh, {
      asOf: new Date().toISOString(),
      source: "/api/portfolio",
      surface: "portfolio",
    });
  }
  // suppress unused-var lint for safeNum which is intentionally still imported.
  void safeNum;
}

export async function loadPortfolioRisk() {
  const panel = document.getElementById("portfolioRiskContent");
  if (!panel) return;
  panel.innerHTML = `<div class="muted">Loading risk analytics...</div>`;
  setResearchStatusStrip(
    "portfolioStatusStrip",
    "loading",
    "Loading portfolio risk.",
    "Calculating concentration, sector allocation, and day P/L.",
  );
  const out = await api.get("/api/portfolio/risk");
  if (!out.ok) {
    state.lastPortfolioRiskData = null;
    const hint =
      out.status === 409
        ? "Link Schwab account + market data in Setup, then retry."
        : out.status === 401
          ? "Sign in first to load tenant-scoped portfolio analytics."
          : "Retry in a moment. If this persists, check backend logs.";
    panel.innerHTML = `<div class="muted">Risk analytics unavailable: ${safeText(out.error)}</div><div class="muted small">${safeText(hint)}</div><button id="portfolioRiskRetryBtn" class="btn small secondary" type="button" style="margin-top:0.5rem">Retry</button>`;
    setResearchStatusStrip(
      "portfolioStatusStrip",
      "partial",
      "Positions loaded; risk unavailable.",
      safeText(hint),
    );
    document.getElementById("portfolioRiskRetryBtn")?.addEventListener("click", () => void loadPortfolioRisk());
    return;
  }
  const d = out.data;
  state.lastPortfolioRiskData = d;
  if (!d.position_count) {
    const emptyRec = d.recommendation || {};
    panel.innerHTML = `
      <div class="muted">No positions to analyze.</div>
      <div class="risk-recommendation-card" style="margin-top:0.65rem">
        <div class="risk-section-title">Recommendation</div>
        <div>${safeText(emptyRec.headline || "Build a diversified starter allocation")}</div>
        <div class="muted small">${safeText(emptyRec.suggested_action || "When adding positions, spread exposure across multiple sectors and avoid oversized initial positions.")}</div>
      </div>`;
    setResearchStatusStrip(
      "portfolioStatusStrip",
      "empty",
      "No positions to analyze.",
      safeText(emptyRec.headline || "Build a diversified starter allocation."),
    );
    return;
  }

  const conc = d.concentration || {};
  const concColor = conc.hhi > 2500 ? "var(--bad)" : conc.hhi > 1500 ? "var(--warn)" : "var(--good)";
  const dayColor = d.day_pl_total >= 0 ? "var(--good)" : "var(--bad)";

  let html = `
    <div class="risk-kpi-row">
      <div class="risk-kpi">
        <div class="risk-kpi-value">${formatMoney(d.total_value)}</div>
        <div class="risk-kpi-label">Total Value</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value" style="color:${dayColor}">${d.day_pl_total >= 0 ? "+" : ""}${formatMoney(d.day_pl_total)}</div>
        <div class="risk-kpi-label">Day P/L</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value" style="color:${concColor}">${safeText(conc.hhi_label || "N/A")}</div>
        <div class="risk-kpi-label">Concentration (HHI ${safeText(conc.hhi)})</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${safeText(conc.top_position_pct)}%</div>
        <div class="risk-kpi-label">Largest Position</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${safeText(conc.top_5_pct)}%</div>
        <div class="risk-kpi-label">Top 5 Weight</div>
      </div>
      <div class="risk-kpi">
        <div class="risk-kpi-value">${safeText(conc.sector_count)}</div>
        <div class="risk-kpi-label">Sectors</div>
      </div>
    </div>`;

  if (d.recommendation) {
    const rec = d.recommendation;
    const priority = String(rec.priority || "low").toLowerCase();
    const priorityColor = priority === "high" ? "var(--bad)" : priority === "medium" ? "var(--warn)" : "var(--good)";
    html += `
      <div class="risk-section-title">Recommendation</div>
      <div class="risk-recommendation-card">
        <div style="font-weight:600;color:${priorityColor}">${safeText(rec.headline || "Portfolio recommendation")}</div>
        <div class="muted" style="margin-top:0.2rem">${safeText(rec.reason || "")}</div>
        <div class="muted small" style="margin-top:0.35rem">${safeText(rec.suggested_action || "")}</div>
      </div>`;
  }

  if (d.sector_allocation && d.sector_allocation.length) {
    const maxSector = Math.max(1, ...d.sector_allocation.map((s) => s.weight_pct));
    html += `<div class="risk-section-title">Sector Allocation</div><div class="risk-sector-bars">`;
    d.sector_allocation.forEach((s) => {
      const barW = Math.max(2, Math.round((s.weight_pct / maxSector) * 100));
      html += `
        <div class="risk-sector-row">
          <span class="risk-sector-name">${safeText(s.sector)}</span>
          <div class="risk-sector-bar-track">
            <div class="risk-sector-bar-fill" style="width:${barW}%"></div>
          </div>
          <span class="risk-sector-pct mono-nums">${formatDecimal(s.weight_pct, 2)}%</span>
          <span class="risk-sector-val muted mono-nums">${formatMoney(s.value)}</span>
        </div>`;
    });
    html += `</div>`;
  }

  if (d.positions_weighted && d.positions_weighted.length) {
    const maxW = Math.max(1, ...d.positions_weighted.map((p) => p.weight_pct));
    html += `<div class="risk-section-title">Position Weights</div><div class="risk-weight-grid">`;
    d.positions_weighted.slice(0, 15).forEach((p) => {
      const barW = Math.max(2, Math.round((p.weight_pct / maxW) * 100));
      const plColor = p.pl_pct >= 0 ? "var(--good)" : "var(--bad)";
      html += `
        <div class="risk-weight-row">
          <span class="risk-weight-sym">${safeText(p.symbol)}</span>
          <div class="risk-sector-bar-track">
            <div class="risk-weight-bar-fill" style="width:${barW}%"></div>
          </div>
          <span class="risk-sector-pct mono-nums">${formatDecimal(p.weight_pct, 2)}%</span>
          <span class="mono-nums" style="color:${plColor};min-width:52px;text-align:right">${p.pl_pct >= 0 ? "+" : ""}${formatDecimal(p.pl_pct, 2)}%</span>
        </div>`;
    });
    html += `</div>`;
  }

  if (d.day_pl_breakdown && d.day_pl_breakdown.length) {
    html += `<div class="risk-section-title">Day P/L Movers</div><div class="risk-pl-list">`;
    d.day_pl_breakdown.slice(0, 8).forEach((p) => {
      const color = p.day_pl >= 0 ? "var(--good)" : "var(--bad)";
      html += `
        <div class="risk-pl-row">
          <span class="risk-weight-sym">${safeText(p.symbol)}</span>
          <span class="mono-nums" style="color:${color}">${p.day_pl >= 0 ? "+" : ""}${formatMoney(p.day_pl)}</span>
        </div>`;
    });
    html += `</div>`;
  }

  panel.innerHTML = html;
  setResearchStatusStrip(
    "portfolioStatusStrip",
    "success",
    `${safeText(d.position_count)} position(s) analyzed.`,
    `Concentration ${safeText(d.concentration?.hhi_label || "—")} · Day P/L ${formatMoney(d.day_pl_total)}.`,
  );
}
