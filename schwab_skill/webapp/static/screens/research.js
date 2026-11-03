/* Research screen controller.
 *
 * Owns one-time wiring (init) and screen-prime data loading (prime) for the
 * Research screen. Prime is tab-scoped: Portfolio loads the book; Quick check
 * waits for a ticker. Backtest lives on its own screen.
 */

import { getActiveResearchTab } from "../modules/researchTabs.js";
import {
  RESEARCH_TICKER_RAIL_IDS,
  syncResearchTickerRail,
} from "../modules/researchTickerFlow.js";
import {
  applyEventProbabilityTickerStale,
  initEventProbabilityPanel,
  loadEventProbability,
} from "../panels/eventProbability.js";
import { initLiquidPositioningPanel } from "../panels/liquidPositioning.js";
import {
  applyPricingModelTickerStale,
  initPricingModelPanel,
  loadPricingModel,
} from "../panels/pricingModel.js";
import {
  applySharadarBundleTickerStale,
  initSharadarBundlePanel,
  loadSharadarBundle,
} from "../panels/sharadarFundamentals.js";
import { initSharadarComparePanel } from "../panels/sharadarCompare.js";
import { applyQuickCheckTickerStale } from "../panels/quickCheck.js";
import { applyDossierTickerStale, loadResearchDossierPreflight, syncDossierExportEnabled } from "../panels/dossier.js";
import { applyReportTickerStale } from "../panels/report.js";
import { applySecCompareTickerStale } from "../panels/sec.js";

const TICKER_RAIL_IDS = RESEARCH_TICKER_RAIL_IDS;
const PREFLIGHT_DEBOUNCE_MS = 400;

let _preflightTimer = 0;

function scheduleDossierPreflight() {
  if (typeof window === "undefined") return;
  if (_preflightTimer) window.clearTimeout(_preflightTimer);
  _preflightTimer = window.setTimeout(() => {
    _preflightTimer = 0;
    void loadResearchDossierPreflight();
  }, PREFLIGHT_DEBOUNCE_MS);
}

function onResearchTickerChange(ticker) {
  const sym = String(ticker || "").trim().toUpperCase();
  applyQuickCheckTickerStale(sym);
  applyEventProbabilityTickerStale(sym);
  applyPricingModelTickerStale(sym);
  applySharadarBundleTickerStale(sym);
  applyDossierTickerStale(sym);
  applyReportTickerStale(sym);
  applySecCompareTickerStale(sym);
  scheduleDossierPreflight();
}

export function createResearchController(ctx) {
  const {
    bindEvent,
    state,
    api,
    safeText,
    updateActionCenter,
    quickCheck,
    runReport,
    runResearchDossier,
    downloadResearchDossier,
    downloadResearchFundamentalWorkbook,
    runSecCompare,
    applySecCompareMode,
    resetSecCompareProfileOverride,
    renderSecCompareVisual,
    wireSecCompareActions,
    openTradeDrawer,
    mapRecovery,
    refreshPerformance,
    wirePortfolioSubtabs,
    renderEvolvePanel,
    renderChallengerPanel,
    runLazyApi,
  } = ctx;

  function wireTickerRail() {
    TICKER_RAIL_IDS.forEach((id) => {
      const el = document.getElementById(id);
      if (!el || el.dataset.tickerRailWired) return;
      el.dataset.tickerRailWired = "1";
      el.addEventListener("input", () => {
        syncResearchTickerRail(el.value, { sourceId: id });
      });
      el.addEventListener("change", () => {
        syncResearchTickerRail(el.value, { sourceId: id });
      });
    });
    const tickerInput = document.getElementById("tickerInput");
    if (tickerInput && !tickerInput.dataset.enterWired) {
      tickerInput.dataset.enterWired = "1";
      tickerInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          void quickCheck();
        }
      });
    }
  }

  function primeTab(tabKey) {
    const key = tabKey || getActiveResearchTab();
    if (key === "portfolio") {
      void runLazyApi("portfolio");
      ctx.updateResearchSummaryLanding?.();
    }
  }

  function init() {
    wireTickerRail();
    bindEvent("checkBtn", "click", quickCheck);
    bindEvent("reportBtn", "click", runReport);
    bindEvent("dossierBtn", "click", runResearchDossier);
    bindEvent("dossierDownloadJsonBtn", "click", () => downloadResearchDossier("json"));
    bindEvent("dossierDownloadMdBtn", "click", () => downloadResearchDossier("md"));
    bindEvent("dossierDownloadPdfBtn", "click", () => downloadResearchDossier("pdf"));
    bindEvent("dossierDownloadModelWorkbookBtn", "click", downloadResearchFundamentalWorkbook);
    document.querySelectorAll("#reportTemplateButtons button[data-template]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const node = e.currentTarget;
        const template = node.getAttribute("data-template") || "institutional_quick_read";
        const section = node.getAttribute("data-section") || "";
        const skipMirofish = node.getAttribute("data-skip-mirofish") === "true";
        const skipEdgar = node.getAttribute("data-skip-edgar") === "true";
        const sectionEl = document.getElementById("reportSection");
        const skipMiroEl = document.getElementById("skipMirofish");
        const skipEdgarEl = document.getElementById("skipEdgar");
        const templateMeta = document.getElementById("reportTemplateMeta");
        const advanced = document.getElementById("reportAdvancedOptions");
        if (sectionEl) sectionEl.value = section;
        if (skipMiroEl) skipMiroEl.checked = skipMirofish;
        if (skipEdgarEl) skipEdgarEl.checked = skipEdgar;
        if (templateMeta) {
          const sectionLabel = section || "all sections";
          templateMeta.textContent = `Template loaded: ${template} (${sectionLabel}, skip_mirofish=${skipMirofish}, skip_edgar=${skipEdgar})`;
        }
        if (advanced && (section || skipMirofish || skipEdgar)) {
          advanced.open = true;
        }
        updateActionCenter({
          title: "Report Template Loaded",
          message: `${template} preset applied to advanced Run Report. Use Generate full report for the operator package.`,
          severity: "info",
        });
      });
    });
    bindEvent("secCompareBtn", "click", runSecCompare);
    bindEvent("secCompareMode", "change", applySecCompareMode);
    bindEvent("secCompareResetProfileBtn", "click", resetSecCompareProfileOverride);
    wireSecCompareActions({ openTradeDrawer });
    bindEvent("secCompareRuthlessMode", "change", () => {
      state.secRuthlessMode = Boolean(document.getElementById("secCompareRuthlessMode")?.checked);
      if (state.secCompareResult) renderSecCompareVisual(state.secCompareResult);
    });
    document.querySelectorAll("#secComparePresetButtons button[data-a]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const node = e.currentTarget;
        const mode = node.getAttribute("data-mode") || "ticker_vs_ticker";
        const a = node.getAttribute("data-a") || "";
        const b = node.getAttribute("data-b") || "";
        document.getElementById("secCompareMode").value = mode;
        document.getElementById("secCompareTickerA").value = a;
        document.getElementById("secCompareTickerB").value = b;
        syncResearchTickerRail(a, { sourceId: "secCompareTickerA" });
        applySecCompareMode();
        if (e.shiftKey) {
          void runSecCompare();
          return;
        }
        updateActionCenter({
          title: "Preset Loaded",
          message: `${a}${b ? ` vs ${b}` : " over time"} template loaded. Click Run SEC Compare (Shift+click to run immediately).`,
          severity: "info",
        });
      });
    });
    bindEvent("recoveryBtn", "click", mapRecovery);
    bindEvent("performanceRefreshBtn", "click", refreshPerformance);
    // Positions | Risk sub-tabs; the Risk tab lazy-loads the risk dashboard.
    wirePortfolioSubtabs();
    document.getElementById("cockpitMergedPanel")?.addEventListener("toggle", () => {
      const el = document.getElementById("cockpitMergedPanel");
      if (el?.open) void ctx.primeCockpitPanel?.();
    });
    if (typeof window !== "undefined") {
      window.addEventListener("research_tab_change", (e) => {
        primeTab(e.detail?.tab);
      });
      window.addEventListener("research_open_ticker", (e) => {
        const ticker = e.detail?.ticker;
        if (ticker) ctx.openResearchForTicker?.(ticker);
      });
      window.addEventListener("research_ticker_change", (e) => {
        onResearchTickerChange(e.detail?.ticker);
      });
      window.addEventListener("research_quick_check_complete", (e) => {
        const ticker = e.detail?.ticker;
        if (ticker) {
          void loadEventProbability(ticker);
          void loadPricingModel(ticker);
          void loadSharadarBundle(ticker);
        }
      });
    }
    initEventProbabilityPanel();
    initLiquidPositioningPanel();
    initPricingModelPanel();
    initSharadarBundlePanel();
    initSharadarComparePanel();
    syncDossierExportEnabled();
    document.getElementById("evolveBtn")?.addEventListener("click", async () => {
      const btn = document.getElementById("evolveBtn");
      const panel = document.getElementById("learningPanel");
      if (btn) { btn.disabled = true; btn.textContent = "Analyzing..."; }
      try {
        const out = await api.post("/api/evolve/run");
        if (out.ok) {
          renderEvolvePanel(panel, out.data);
        } else {
          if (panel) panel.innerHTML = `<div class="panel-error">${safeText(out.error || "Analysis failed")}</div>`;
        }
      } catch (e) {
        if (panel) panel.innerHTML = `<div class="panel-error">Error: ${safeText(String(e))}</div>`;
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Run Post-Mortem Analysis"; }
      }
    });
    document.getElementById("challengerBtn")?.addEventListener("click", async () => {
      const btn = document.getElementById("challengerBtn");
      const panel = document.getElementById("challengerPanel");
      if (btn) { btn.disabled = true; btn.textContent = "Running scans..."; }
      try {
        const out = await api.post("/api/challenger/run");
        if (out.ok && out.data && out.data.comparison) {
          renderChallengerPanel(panel, { available: true, latest: out.data.comparison, win_rate: out.data.win_rate || {} });
        } else {
          if (panel) panel.innerHTML = `<div class="panel-error">${safeText((out.data && out.data.message) || out.error || "Challenger scan failed")}</div>`;
        }
      } catch (e) {
        if (panel) panel.innerHTML = `<div class="panel-error">Error: ${safeText(String(e))}</div>`;
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Run Challenger Scan"; }
      }
    });
  }

  function prime() {
    primeTab(getActiveResearchTab());
  }

  return { id: "research", init, prime };
}
