/* Backtest screen controller — landing, queue, selected run, related evidence. */

export function createBacktestController(ctx) {
  const {
    restoreBacktestFormFromStorage,
    setDefaultBacktestDates,
    syncBtUniverseRow,
    wireBacktestFormPersistence,
    renderStrategyChatMessages,
    adaptLocalBacktestChatUi,
    switchBacktestHubTab,
    applyBacktestPresetYears,
    sendStrategyChat,
    queueUserBacktest,
    refreshBacktestRuns,
    resetBacktestFormToDefaults,
    refreshBacktestHub,
    queueSpec,
    queueBacktestWindow,
    extrasFromQueueButton,
    renderBacktestQueueNowSummary,
    applyNamedIndexQueueDefaults,
    applySpecToForm,
    openBacktestComposer,
    getSelectedBacktestRun,
    collectBacktestSpecFromForm,
    mountBacktestComposerCatalog,
    refreshBacktestComposerCatalog,
  } = ctx;

  function handleCta(cta) {
    if (!cta || typeof cta !== "object") return;
    if (cta.kind === "queue_spec" && cta.spec) {
      void queueSpec?.(cta.spec);
      return;
    }
    if (cta.kind === "scroll" && cta.target) {
      document.getElementById(cta.target)?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (cta.kind === "open_form") {
      const run = getSelectedBacktestRun?.();
      if (run?.spec) applySpecToForm?.(run.spec);
      openBacktestComposer?.();
    }
  }

  function init() {
    restoreBacktestFormFromStorage?.();
    setDefaultBacktestDates?.();
    wireBacktestFormPersistence?.();
    mountBacktestComposerCatalog?.();
    void refreshBacktestComposerCatalog?.();
    syncBtUniverseRow?.();
    renderStrategyChatMessages?.();
    adaptLocalBacktestChatUi?.();
    renderBacktestQueueNowSummary?.();
    document.getElementById("btHubTabForm")?.addEventListener("click", () => switchBacktestHubTab?.("form"));
    document.getElementById("btHubTabChat")?.addEventListener("click", () => switchBacktestHubTab?.("chat"));
    document.getElementById("btUniverse")?.addEventListener("change", syncBtUniverseRow);
    document.querySelectorAll(".bt-preset").forEach((btn) => {
      btn.addEventListener("click", () => {
        const y = btn.getAttribute("data-years");
        if (y) applyBacktestPresetYears?.(y);
      });
    });
    document.querySelectorAll("[data-bt-queue-years]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const parsed = extrasFromQueueButton?.(btn) || {
          years: btn.getAttribute("data-bt-queue-years"),
          extras: {},
        };
        void queueBacktestWindow?.(parsed.years, parsed.extras);
      });
    });
    document.querySelectorAll(".sc-chip:not([data-bt-queue-years])").forEach((btn) => {
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
        sendStrategyChat?.();
      }
    });
    document.getElementById("btQueueBtn")?.addEventListener("click", queueUserBacktest);
    document.getElementById("btQueueUseIndexBtn")?.addEventListener("click", () => applyNamedIndexQueueDefaults?.());
    document.getElementById("btQueueCopyBtn")?.addEventListener("click", () => {
      const run = getSelectedBacktestRun?.();
      if (run?.spec && Object.keys(run.spec).length) {
        void queueSpec?.(run.spec);
        return;
      }
      const spec = collectBacktestSpecFromForm?.();
      if (spec) void queueSpec?.(spec);
    });
    document.getElementById("btRefreshListBtn")?.addEventListener("click", () => {
      void refreshBacktestRuns?.();
      void refreshBacktestHub?.();
    });
    document.getElementById("btResetFormBtn")?.addEventListener("click", resetBacktestFormToDefaults);
    document.getElementById("scSendBtn")?.addEventListener("click", sendStrategyChat);
    document.getElementById("backtestHubRefreshBtn")?.addEventListener("click", () => void refreshBacktestHub?.());
    document.getElementById("btOpenFormBtn")?.addEventListener("click", () => {
      openBacktestComposer?.();
      document.getElementById("btQueueBtn")?.focus();
    });
    window.addEventListener("backtest_run_updated", (ev) => {
      void refreshBacktestHub?.({ followRunId: ev.detail?.runId || "" });
    });
    window.addEventListener("backtest_cta", (ev) => {
      handleCta(ev.detail);
    });
  }

  function prime() {
    void refreshBacktestHub?.();
    void refreshBacktestRuns?.();
    renderBacktestQueueNowSummary?.();
    adaptLocalBacktestChatUi?.();
  }

  return { id: "backtest", init, prime };
}
