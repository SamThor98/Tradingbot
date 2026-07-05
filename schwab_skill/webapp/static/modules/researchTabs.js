/**
 * Research workspace sub-tabs — one focused panel per analysis workflow.
 */

const TAB_SECTIONS = Object.freeze({
  check: ["quickCheckSection", "sectorsSection", "moversSection", "researchAdvancedTools"],
  backtest: ["backtestSection"],
  diligence: ["reportSectionCard", "secCompareSection"],
  portfolio: ["portfolioSection", "performanceSection", "cockpitMergedPanel", "cockpitSection"],
});

/** Legacy stored tab keys from older layouts. */
const TAB_ALIASES = Object.freeze({
  validate: "backtest",
  advanced: "check",
});

const SECTION_TO_TAB = Object.freeze({
  ...Object.entries(TAB_SECTIONS).reduce((acc, [tabKey, ids]) => {
    ids.forEach((id) => {
      acc[id] = tabKey;
    });
    return acc;
  }, {}),
  recoverySection: "check",
  learningSection: "check",
});

export function researchTabForSection(sectionId) {
  return SECTION_TO_TAB[sectionId] || null;
}

export function normalizeResearchTab(tab) {
  const key = String(tab || "check").trim().toLowerCase();
  const aliased = TAB_ALIASES[key] || key;
  return TAB_SECTIONS[aliased] ? aliased : "check";
}

function setResearchTab(tab) {
  const key = normalizeResearchTab(tab);
  document.querySelectorAll("[data-research-tab-btn]").forEach((btn) => {
    const active = btn.getAttribute("data-research-tab-btn") === key;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  Object.entries(TAB_SECTIONS).forEach(([tabKey, ids]) => {
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.toggle("research-tab-hidden", tabKey !== key);
    });
  });
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("research_tab_change", { detail: { tab: key } }));
  }
}

export function initResearchTabs() {
  const nav = document.getElementById("researchTabNav");
  if (!nav || nav.dataset.wired) return;
  nav.dataset.wired = "1";
  nav.querySelectorAll("[data-research-tab-btn]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setResearchTab(btn.getAttribute("data-research-tab-btn") || "check");
    });
  });
  document.querySelectorAll("[data-research-tab-link]").forEach((link) => {
    link.addEventListener("click", () => {
      const tab = link.getAttribute("data-research-tab-link");
      if (tab) setResearchTab(tab);
    });
  });
  setResearchTab("check");
}

export function applyResearchTab(tab) {
  setResearchTab(tab);
}

export function activateResearchTabForSection(sectionId) {
  const tab = researchTabForSection(sectionId);
  if (tab) applyResearchTab(tab);
}
