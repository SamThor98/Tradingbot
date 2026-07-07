/**
 * Portfolio panel: positions table (Positions sub-tab).
 *
 * `refreshPortfolio` paints the positions table and shows setup guidance
 * when no account positions are available.
 *
 * The dedicated Risk sub-tab (correlation heatmap, risk contribution,
 * concentration limits, stress testing) lives in `panels/portfolioRisk.js`.
 */

import { api } from "../modules/api.js";
import {
  safeText,
  formatMoney,
  formatDecimal,
  formatCount,
  formatSignedDelta,
} from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import { state } from "../modules/state.js";
import { applyFreshness, markUnavailable, clearUnavailable } from "../modules/freshness.js";
import { setResearchPanelStatus } from "../modules/researchStatus.js";
import { buildOperatorAlertHtml } from "../modules/asyncState.js";

function paintPortfolioSurface(stateName, title, detail, extras = {}) {
  return setResearchPanelStatus({
    stripId: "portfolioStatusStrip",
    snapshotId: "portfolioSnapshot",
    sectionId: "portfolioSection",
    stateName,
    title,
    detail,
    hint: extras.hint || "positions · value · P/L · risk",
    output: extras.output,
    data: extras.data,
    action: extras.action,
    confidence: extras.confidence,
    lines: extras.lines,
  });
}

export async function refreshPortfolio() {
  const card = document.getElementById("portfolioSection");
  const body = document.getElementById("portfolioBody");
  const meta = document.getElementById("portfolioMeta");
  if (!body) return;
  if (card) card.setAttribute("data-async-state", "loading");
  paintPortfolioSurface(
    "loading",
    "Loading portfolio.",
    "Fetching positions, market value, and account exposure.",
    {
      output: { value: "…", sub: "positions table" },
      data: { value: "…", sub: "broker feed" },
      action: { value: "Wait", sub: "hold" },
      confidence: 28,
    },
  );
  body.innerHTML = `<tr><td colspan="7" class="muted">
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
      paintPortfolioSurface(
        "error",
        "Sign in required.",
        "Sign in to load tenant-scoped portfolio data.",
        {
          output: { value: "—", sub: "positions" },
          data: { value: "—", sub: "auth" },
          action: { value: "Sign in", sub: "required", tone: "bad" },
          confidence: 0,
        },
      );
      if (meta) {
        markUnavailable(meta, "signed out");
        meta.textContent = "Sign in to load positions.";
      }
      body.innerHTML = `<tr><td colspan="7">
        <div class="signed-out-banner" role="status">
          <strong>Signed out.</strong>
          <span>Sign in to load tenant-scoped portfolio data.</span>
          <a class="btn small secondary" href="#supabaseAuthBlock">Sign in</a>
        </div>
      </td></tr>`;
      return;
    }
    if (card) card.setAttribute("data-async-state", "error");
    paintPortfolioSurface("error", "Portfolio unavailable.", reason, {
      output: { value: "—", sub: "positions" },
      data: { value: "—", sub: "broker feed" },
      action: { value: "Retry", sub: "reload", tone: "bad" },
      confidence: 0,
    });
    if (meta) {
      markUnavailable(meta, reason);
      meta.textContent = `Portfolio unavailable: ${reason}`;
    }
    const hint = out.status === 409
      ? " Link Schwab account + market data in Settings, then retry."
      : "";
    body.innerHTML = `<tr><td colspan="7">
      ${buildOperatorAlertHtml({
        tone: "bad",
        headline: "Data unavailable",
        detail: `${reason}${hint}`,
        retry: true,
        retryAttr: "data-portfolio-retry",
      })}
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
    paintPortfolioSurface(
      "empty",
      "No open positions.",
      "Connect Schwab or add positions before analyzing exposure.",
      {
        output: { value: "None", sub: "positions" },
        data: { value: "Clear", sub: "account" },
        action: { value: "Setup", sub: "link account" },
        confidence: 0,
      },
    );
    body.innerHTML = `
      <tr>
        <td colspan="7" class="muted">
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
  paintPortfolioSurface(
    "success",
    `${formatCount(data.positions_count, "0")} position(s) loaded.`,
    `${formatMoney(data.total_market_value)} total market value. Open the Risk tab for correlation, concentration, and stress tests.`,
    {
      output: { value: "Ready", sub: "positions" },
      data: { value: "Fresh", sub: "broker" },
      action: { value: "Pass", sub: "risk optional" },
      confidence: 78,
    },
  );
  const totalValue = Number(data.total_market_value) || 0;
  data.positions.slice(0, 25).forEach((p) => {
    const weightPct = totalValue > 0 ? (Number(p.market_value) / totalValue) * 100 : NaN;
    const dayPl = Number(p.day_pl);
    const dayPlCell = Number.isFinite(dayPl)
      ? `<span class="mono-nums" style="color:${dayPl >= 0 ? "var(--good)" : "var(--bad)"}">${dayPl >= 0 ? "+" : ""}${formatMoney(dayPl)}</span>`
      : "—";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${safeText(p.symbol)}</td>
      <td>${formatCount(p.qty, "—")}</td>
      <td>${formatMoney(p.last)}</td>
      <td>${formatMoney(p.market_value)}</td>
      <td class="mono-nums">${Number.isFinite(weightPct) ? `${formatDecimal(weightPct, 1)}%` : "—"}</td>
      <td>${dayPlCell}</td>
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
}
