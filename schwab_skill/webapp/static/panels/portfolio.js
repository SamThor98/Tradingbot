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
import { getPortfolioSource, renderManualPositionsPanel, loadManualBook } from "./portfolioManual.js";
import { openBookJournalForSymbol } from "./portfolioBook.js";

function protectionTone(status) {
  if (status === "FULL") return "good";
  if (status === "PARTIAL") return "warn";
  if (status === "NO STOP SEEN" || status === "LOCKED") return "bad";
  return "neutral";
}

function protectionCell(position) {
  const prot = position.protection || {};
  const status = String(prot.status || "");
  if (!status || status === "NOT_APPLICABLE") return "—";
  const tone = protectionTone(status);
  const label = safeText(prot.label || status);
  const detail = safeText(prot.detail || "");
  return `<span class="pill ${tone}" title="${detail}">${label}</span>`;
}

function ambiguityButtons(ticker) {
  const symbol = safeText(ticker);
  return `<button type="button" class="btn small secondary" data-ambiguity-resolve="${symbol}" data-outcome="landed">Landed</button>
    <button type="button" class="btn small secondary" data-ambiguity-resolve="${symbol}" data-outcome="absent">Absent</button>`;
}

async function resolveAmbiguity(ticker, outcome) {
  const out = await api.post("/api/execution/ambiguity/resolve", { ticker, outcome });
  if (!out.ok) {
    logEvent({
      kind: "system",
      severity: "warn",
      message: `Order resolve failed: ${out.user_message || out.error || "request failed"}`,
    });
    return;
  }
  await refreshPortfolio();
}

function bindAmbiguityButtons(root) {
  root?.querySelectorAll("[data-ambiguity-resolve]").forEach((button) => {
    button.addEventListener("click", () => {
      const ticker = button.getAttribute("data-ambiguity-resolve");
      const outcome = button.getAttribute("data-outcome");
      if (ticker && outcome) void resolveAmbiguity(ticker, outcome);
    });
  });
}

function paintAmbiguityBanner(data) {
  const banner = document.getElementById("portfolioAmbiguityBanner");
  if (!banner) return;
  const locks = Array.isArray(data.order_ambiguity) ? data.order_ambiguity : [];
  const ledgerError = data.order_ambiguity_error ? safeText(data.order_ambiguity_error) : "";
  if (!locks.length && !ledgerError) {
    banner.classList.add("hidden");
    banner.replaceChildren();
    return;
  }
  banner.classList.remove("hidden");
  const rows = locks
    .map((lock) => {
      const ticker = safeText(lock.ticker);
      const reason = safeText(lock.reason || "Order outcome unknown");
      return `<div class="portfolio-ambiguity-row"><span class="pill bad">LOCKED</span> <strong>${ticker}</strong> <span class="muted">${reason}</span> ${ambiguityButtons(lock.ticker)}</div>`;
    })
    .join("");
  banner.innerHTML = `${ledgerError ? `<div>${ledgerError}</div>` : ""}${rows}`;
  bindAmbiguityButtons(banner);
}

function protectionSummary(data) {
  const counts = data.protection_counts || {};
  const parts = [];
  if (counts.FULL) parts.push(`${formatCount(counts.FULL)} protected`);
  if (counts.PARTIAL) parts.push(`${formatCount(counts.PARTIAL)} partial`);
  if (counts["NO STOP SEEN"]) parts.push(`${formatCount(counts["NO STOP SEEN"])} no stop`);
  if (counts.LOCKED) parts.push(`${formatCount(counts.LOCKED)} locked`);
  return parts.join(" · ");
}

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
  // Manual source: the editor grid owns the Positions panel — no Schwab fetch.
  if (getPortfolioSource() === "manual") {
    paintAmbiguityBanner({});
    renderManualPositionsPanel();
    const book = loadManualBook();
    const tickers = book.positions.filter((p) => p.ticker).length;
    state.lastPortfolioData = {
      positions_count: tickers,
      positions: book.positions,
      source: "manual",
    };
    state.lastPortfolioAt = new Date().toISOString();
    window.dispatchEvent(new CustomEvent("research_summary_refresh"));
    if (card) card.setAttribute("data-async-state", tickers ? "success" : "empty");
    paintPortfolioSurface(
      tickers ? "success" : "empty",
      tickers ? `Manual book: ${tickers} ticker(s).` : "Manual book is empty.",
      tickers
        ? "Update prices for a valued snapshot, or open the Risk tab for the full risk dashboard."
        : "Add tickers and share counts below — the book stays in this browser only.",
      {
        output: { value: tickers ? "Ready" : "None", sub: "manual book" },
        data: { value: "Local", sub: "this browser" },
        action: { value: tickers ? "Pass" : "Add", sub: tickers ? "risk optional" : "add tickers" },
      },
    );
    return;
  }
  if (card) card.setAttribute("data-async-state", "loading");
  paintPortfolioSurface(
    "loading",
    "Loading portfolio.",
    "Fetching positions, market value, and account exposure.",
    {
      output: { value: "…", sub: "positions table" },
      data: { value: "…", sub: "broker feed" },
      action: { value: "Wait", sub: "hold" },
    },
  );
  body.innerHTML = `<tr><td colspan="9" class="muted">
    <div class="async-state async-state--loading">
      <span class="async-spinner" aria-hidden="true"></span>
      <span>Loading positions…</span>
    </div>
  </td></tr>`;
  const out = await api.get("/api/portfolio");
  if (!out.ok) {
    paintAmbiguityBanner({});
    state.lastPortfolioData = null;
    window.dispatchEvent(new CustomEvent("research_summary_refresh"));
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
      body.innerHTML = `<tr><td colspan="9">
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
    body.innerHTML = `<tr><td colspan="9">
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
  state.lastPortfolioAt = new Date().toISOString();
  window.dispatchEvent(new CustomEvent("research_summary_refresh"));
  if (meta) {
    clearUnavailable(meta);
    meta.textContent = `${formatCount(data.positions_count, "0")} position(s) • ${formatMoney(data.total_market_value)}`;
    const protNote = protectionSummary(data);
    if (protNote) meta.textContent += ` • ${protNote}`;
  }
  paintAmbiguityBanner(data);
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
        <td colspan="9" class="muted">
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
    },
  );
  const totalValue = Number(data.total_market_value) || 0;
  data.positions.slice(0, 25).forEach((p) => {
    const weightPct = totalValue > 0 ? (Number(p.market_value) / totalValue) * 100 : NaN;
    const dayPl = Number(p.day_pl);
    const dayTone = !Number.isFinite(dayPl) || dayPl === 0 ? "neutral" : dayPl > 0 ? "ok" : "bad";
    const dayPlCell = Number.isFinite(dayPl)
      ? `<span class="mono-nums" data-tone="${dayTone}">${dayPl >= 0 ? "+" : ""}${formatMoney(dayPl)}</span>`
      : "—";
    const plPct = Number(p.pl_pct);
    const plTone = !Number.isFinite(plPct) || plPct === 0 ? "neutral" : plPct > 0 ? "ok" : "bad";
    const weight = Number.isFinite(weightPct) ? Math.max(0, Math.min(100, weightPct)) : null;
    const weightCell = weight === null
      ? "—"
      : `<span class="ol-weight"><span class="ol-weight-track" aria-hidden="true"><span class="ol-weight-fill" style="width:${weight.toFixed(1)}%"></span></span><span class="mono-nums">${formatDecimal(weightPct, 1)}%</span></span>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${safeText(p.symbol)}</td>
      <td>${formatCount(p.qty, "—")}</td>
      <td>${formatMoney(p.last)}</td>
      <td>${formatMoney(p.market_value)}</td>
      <td class="mono-nums">${weightCell}</td>
      <td>${dayPlCell}</td>
      <td class="mono-nums" data-tone="${plTone}">${formatSignedDelta(p.pl_pct, (n) => formatDecimal(n, 2, "0.00"))}%</td>
      <td>${protectionCell(p)}</td>
      <td>
        <button type="button" class="btn small secondary" data-check-sym="${safeText(p.symbol)}" aria-label="Check ${safeText(p.symbol)}">Check</button>
        <button type="button" class="btn small secondary" data-journal-sym="${safeText(p.symbol)}">Journal</button>
        ${(p.protection || {}).status === "LOCKED" ? ambiguityButtons(p.symbol) : ""}
      </td>
    `;
    tr.querySelector("[data-check-sym]")?.addEventListener("click", () => {
      window.dispatchEvent(new CustomEvent("research_open_ticker", { detail: { ticker: p.symbol } }));
    });
    tr.querySelector("[data-journal-sym]")?.addEventListener("click", () => {
      openBookJournalForSymbol(p.symbol);
    });
    bindAmbiguityButtons(tr);
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
