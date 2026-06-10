/* Settings screen controller.
 *
 * Owns one-time wiring (init) and screen-prime data loading (prime) for the
 * Settings screen: profile apply, feature guide, live-trading enablement,
 * trading halt, billing, and preset/rank-explain selectors. Dependencies are
 * injected via ctx from app.js so behavior is identical to the previous
 * inline code. Rollout flag: screen_controllers (see wiki
 * [[section-migration-map]]).
 */

export function createSettingsController(ctx) {
  const {
    bindEvent,
    applyProfile,
    openFeatureGuide,
    closeFeatureGuide,
    markFeatureGuideSeen,
    submitEnableLiveTrading,
    submitTradingHaltSave,
    beginBillingCheckout,
    openBillingPortal,
    loadProfiles,
    setRankExplainMode,
    renderPresetApplyPreview,
    runLazyApi,
  } = ctx;

  function init() {
    bindEvent("applyProfileBtn", "click", applyProfile);
    document.getElementById("featureGuideBtn")?.addEventListener("click", () => openFeatureGuide({ markSeen: true }));
    document.getElementById("featureGuideCloseBtn")?.addEventListener("click", closeFeatureGuide);
    document.getElementById("featureGuideDialog")?.addEventListener("close", markFeatureGuideSeen);
    document.getElementById("featureGuideDialog")?.addEventListener("click", (e) => {
      if (e.target?.id === "featureGuideDialog") closeFeatureGuide();
    });
    document.getElementById("enableLiveTradingBtn")?.addEventListener("click", () => void submitEnableLiveTrading());
    document.getElementById("saveTradingHaltBtn")?.addEventListener("click", () => void submitTradingHaltSave());
    document.getElementById("billingCheckoutBtn")?.addEventListener("click", () => void beginBillingCheckout());
    document.getElementById("billingPortalBtn")?.addEventListener("click", () => void openBillingPortal());
    bindEvent("settingsModeSelect", "change", loadProfiles);
    document.getElementById("rankExplainModeSelect")?.addEventListener("change", (e) => {
      setRankExplainMode(e.currentTarget?.value);
    });
    document.getElementById("profileSelect")?.addEventListener("change", renderPresetApplyPreview);
    document.getElementById("automationOptIn")?.addEventListener("change", renderPresetApplyPreview);
  }

  function prime() {
    void runLazyApi("onboarding");
    void runLazyApi("profiles");
  }

  return { id: "settings", init, prime };
}
