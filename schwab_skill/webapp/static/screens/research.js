/* Research screen controller.
 *
 * Owns one-time wiring (init) and screen-prime data loading (prime) for the
 * Research screen: quick check, backtest hub + strategy chat, dossiers/reports,
 * SEC compare, recovery/learning tools, portfolio and performance. Dependencies are injected via ctx from app.js so behavior is
 * identical to the previous inline wireEvents/maybePrimeScreenData code.
 * Rollout flag: screen_controllers (see wiki [[section-migration-map]]).
 */

export function createResearchController(ctx) {
  const {
    bindEvent,
    state,
    api,
    safeText,
    updateActionCenter,
    restoreBacktestFormFromStorage,
    setDefaultBacktestDates,
    syncBtUniverseRow,
    wireBacktestFormPersistence,
    renderStrategyChatMessages,
    switchBacktestHubTab,
    applyBacktestPresetYears,
    sendStrategyChat,
    queueUserBacktest,
    refreshBacktestRuns,
    resetBacktestFormToDefaults,
    quickCheck,
    runReport,
    runResearchDossier,
    downloadResearchDossier,
    downloadResearchFundamentalWorkbook,
    loadResearchDossierPreflight,
    runSecCompare,
    applySecCompareMode,
    resetSecCompareProfileOverride,
    renderSecCompareVisual,
    wireSecCompareActions,
    applyReportViewMode,
    mapRecovery,
    refreshPerformance,
    loadPortfolioRisk,
    renderEvolvePanel,
    renderChallengerPanel,
    runLazyApi,
  } = ctx;

  function init() {
    restoreBacktestFormFromStorage();
    setDefaultBacktestDates();
    syncBtUniverseRow();
    wireBacktestFormPersistence();
    renderStrategyChatMessages();
    document.getElementById("btHubTabForm")?.addEventListener("click", () => switchBacktestHubTab("form"));
    document.getElementById("btHubTabChat")?.addEventListener("click", () => switchBacktestHubTab("chat"));
    document.getElementById("btUniverse")?.addEventListener("change", syncBtUniverseRow);
    document.querySelectorAll(".bt-preset").forEach((btn) => {
      btn.addEventListener("click", () => {
        const y = btn.getAttribute("data-years");
        if (y) applyBacktestPresetYears(y);
      });
    });
    document.querySelectorAll(".sc-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        const t = btn.getAttribute("data-text") || "";
        const input = document.getElementById("scInput");
        if (input) input.value = t;
        input?.focus();
      });
    });
    document.getElementById("scInput")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendStrategyChat();
      }
    });
    document.getElementById("btQueueBtn")?.addEventListener("click", queueUserBacktest);
    document.getElementById("btRefreshListBtn")?.addEventListener("click", refreshBacktestRuns);
    document.getElementById("btResetFormBtn")?.addEventListener("click", resetBacktestFormToDefaults);
    document.getElementById("scSendBtn")?.addEventListener("click", sendStrategyChat);
    bindEvent("checkBtn", "click", quickCheck);
    bindEvent("reportBtn", "click", runReport);
    bindEvent("dossierBtn", "click", runResearchDossier);
    bindEvent("reportTickerInput", "change", loadResearchDossierPreflight);
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
          message: `${template} preset applied to Run Report controls. Click Run Report (advanced) or Generate Dossier when ready.`,
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
    bindEvent("toggleReportViewBtn", "click", () => {
      state.reportRawView = !state.reportRawView;
      applyReportViewMode();
    });
    bindEvent("recoveryBtn", "click", mapRecovery);
    bindEvent("performanceRefreshBtn", "click", refreshPerformance);
    document.getElementById("portfolioRiskPanel")?.addEventListener("toggle", (e) => {
      if (e.target.open) void loadPortfolioRisk();
    });
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
    void runLazyApi("backtest");
    void runLazyApi("portfolio");
    void runLazyApi("performance");
    void ctx.primeCockpitPanel?.();
    void runLazyApi("sectors");
    void runLazyApi("movers");
    void refreshBacktestRuns().then(() => ctx.updateResearchSummaryLanding?.());
    ctx.updateResearchSummaryLanding?.();
  }

  return { id: "research", init, prime };
}
