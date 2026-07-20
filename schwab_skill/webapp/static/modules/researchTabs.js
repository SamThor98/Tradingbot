/**
 * Research workspace sub-tabs — Portfolio → Quick check → Backtest.
 * Diligence (dossier / SEC) lives under Quick check Brief/Deep.
 */

export const DEFAULT_RESEARCH_TAB = "portfolio";

const TAB_SECTIONS = Object.freeze({
  portfolio: ["portfolioSection", "performanceSection", "cockpitMergedPanel", "cockpitSection"],
  check: [
    "quickCheckSection",
    "sectorsSection",
    "moversSection",
    "researchAdvancedTools",
    "reportSectionCard",
    "secCompareSection",
  ],
  backtest: ["backtestSection"],
});

/** Sections only visible when Quick check is in Deep mode. */
const CHECK_DEEP_SECTIONS = Object.freeze(["reportSectionCard", "secCompareSection"]);

/** Legacy stored tab keys from older layouts. */
const TAB_ALIASES = Object.freeze({
  validate: "backtest",
  advanced: "check",
  diligence: "check",
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
  // Portfolio sub-tab panels live inside portfolioSection.
  portfolioPanelRisk: "portfolio",
  portfolioPanelPositions: "portfolio",
  portfolioPanelBook: "portfolio",
  "book-calendar": "portfolio",
  "book-tax": "portfolio",
  "book-journal": "portfolio",
});

/** Sections that should open Quick check in Deep mode. */
const DEEP_SECTION_IDS = new Set(["reportSectionCard", "secCompareSection"]);

const DENSITY_KEY = "tradingbot.ui.research_density";
const CHECK_MODE_KEY = "tradingbot.ui.research_check_mode";

export function researchTabForSection(sectionId) {
  return SECTION_TO_TAB[sectionId] || null;
}

export function normalizeResearchTab(tab) {
  const key = String(tab || DEFAULT_RESEARCH_TAB).trim().toLowerCase();
  const aliased = TAB_ALIASES[key] || key;
  return TAB_SECTIONS[aliased] ? aliased : DEFAULT_RESEARCH_TAB;
}

function normalizeCheckMode(mode) {
  const key = String(mode || "brief").trim().toLowerCase();
  return key === "deep" ? "deep" : "brief";
}

function normalizeDensity(density) {
  const key = String(density || "comfortable").trim().toLowerCase();
  return key === "dense" ? "dense" : "comfortable";
}

function applyCheckModeVisibility(tabKey, mode) {
  const deep = normalizeCheckMode(mode) === "deep";
  CHECK_DEEP_SECTIONS.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    // Hidden unless Quick check tab is active AND Deep mode is on.
    const show = tabKey === "check" && deep;
    el.classList.toggle("research-tab-hidden", !show);
    el.classList.toggle("research-check-deep-only", true);
  });
  document.querySelectorAll("[data-research-check-mode-btn]").forEach((btn) => {
    const active = btn.getAttribute("data-research-check-mode-btn") === normalizeCheckMode(mode);
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
  const modeBar = document.getElementById("researchCheckModeBar");
  if (modeBar) {
    modeBar.hidden = tabKey !== "check";
    modeBar.setAttribute("aria-hidden", tabKey === "check" ? "false" : "true");
  }
  if (typeof document !== "undefined" && document.body) {
    document.body.dataset.researchCheckMode = normalizeCheckMode(mode);
  }
}

function readStoredCheckMode() {
  try {
    return normalizeCheckMode(localStorage.getItem(CHECK_MODE_KEY));
  } catch {
    return "brief";
  }
}

function writeStoredCheckMode(mode) {
  try {
    localStorage.setItem(CHECK_MODE_KEY, normalizeCheckMode(mode));
  } catch {
    /* ignore quota / private mode */
  }
}

export function applyResearchCheckMode(mode, { persist = true } = {}) {
  const next = normalizeCheckMode(mode);
  if (persist) writeStoredCheckMode(next);
  const activeTab =
    document.querySelector("[data-research-tab-btn].active")?.getAttribute("data-research-tab-btn") ||
    DEFAULT_RESEARCH_TAB;
  applyCheckModeVisibility(normalizeResearchTab(activeTab), next);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("research_check_mode_change", { detail: { mode: next } }));
  }
}

function readStoredDensity() {
  try {
    return normalizeDensity(localStorage.getItem(DENSITY_KEY));
  } catch {
    return "comfortable";
  }
}

function writeStoredDensity(density) {
  try {
    localStorage.setItem(DENSITY_KEY, normalizeDensity(density));
  } catch {
    /* ignore */
  }
}

export function applyResearchDensity(density, { persist = true } = {}) {
  const next = normalizeDensity(density);
  if (persist) writeStoredDensity(next);
  if (typeof document !== "undefined" && document.body) {
    document.body.dataset.density = next;
  }
  document.querySelectorAll("[data-research-density-btn]").forEach((btn) => {
    const active = btn.getAttribute("data-research-density-btn") === next;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("research_density_change", { detail: { density: next } }));
  }
}

function setResearchTab(tab, { checkMode } = {}) {
  const key = normalizeResearchTab(tab);
  document.querySelectorAll("[data-research-tab-btn]").forEach((btn) => {
    const active = btn.getAttribute("data-research-tab-btn") === key;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
    // Roving tabindex: only the active tab is a tab stop; arrows move focus.
    btn.tabIndex = active ? 0 : -1;
  });
  Object.entries(TAB_SECTIONS).forEach(([tabKey, ids]) => {
    ids.forEach((id) => {
      if (CHECK_DEEP_SECTIONS.includes(id)) return; // handled by check mode
      const el = document.getElementById(id);
      if (!el) return;
      el.classList.toggle("research-tab-hidden", tabKey !== key);
    });
  });
  const mode = checkMode != null ? normalizeCheckMode(checkMode) : readStoredCheckMode();
  applyCheckModeVisibility(key, mode);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("research_tab_change", { detail: { tab: key } }));
  }
}

export function initResearchTabs() {
  const nav = document.getElementById("researchTabNav");
  if (!nav || nav.dataset.wired) return;
  nav.dataset.wired = "1";

  applyResearchDensity(readStoredDensity(), { persist: false });

  nav.querySelectorAll("[data-research-tab-btn]").forEach((btn) => {
    btn.addEventListener("click", () => {
      setResearchTab(btn.getAttribute("data-research-tab-btn") || DEFAULT_RESEARCH_TAB);
    });
  });
  // Arrow-key navigation between tabs (same pattern as the topbar screen tabs).
  nav.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const btns = Array.from(nav.querySelectorAll("[data-research-tab-btn]"));
    const cur = btns.indexOf(document.activeElement);
    if (cur === -1) return;
    e.preventDefault();
    const delta = e.key === "ArrowRight" ? 1 : -1;
    const next = btns[(cur + delta + btns.length) % btns.length];
    setResearchTab(next.getAttribute("data-research-tab-btn") || DEFAULT_RESEARCH_TAB);
    next.focus();
  });
  document.querySelectorAll("[data-research-tab-link]").forEach((link) => {
    link.addEventListener("click", () => {
      const tab = link.getAttribute("data-research-tab-link");
      const mode = link.getAttribute("data-research-check-mode");
      if (tab === "diligence" || mode === "deep") {
        writeStoredCheckMode("deep");
        setResearchTab("check", { checkMode: "deep" });
        return;
      }
      if (tab) setResearchTab(tab);
    });
  });
  document.querySelectorAll("[data-research-density-btn]").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyResearchDensity(btn.getAttribute("data-research-density-btn") || "comfortable");
    });
  });
  document.querySelectorAll("[data-research-check-mode-btn]").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyResearchCheckMode(btn.getAttribute("data-research-check-mode-btn") || "brief");
    });
  });
  // Respect a tab already activated by a ?section= deep link during boot.
  const preselected = document
    .querySelector("[data-research-tab-btn].active")
    ?.getAttribute("data-research-tab-btn");
  setResearchTab(preselected || DEFAULT_RESEARCH_TAB);
}

export function applyResearchTab(tab) {
  const key = normalizeResearchTab(tab);
  if (key === "check" && (tab === "diligence" || String(tab).toLowerCase() === "diligence")) {
    writeStoredCheckMode("deep");
    setResearchTab("check", { checkMode: "deep" });
    return;
  }
  setResearchTab(key);
}

export function activateResearchTabForSection(sectionId) {
  const tab = researchTabForSection(sectionId);
  if (!tab) return;
  if (tab === "check" && DEEP_SECTION_IDS.has(sectionId)) {
    writeStoredCheckMode("deep");
    setResearchTab("check", { checkMode: "deep" });
    return;
  }
  applyResearchTab(tab);
}
