/**
 * Cmd-K command palette.
 *
 * Action callbacks (`runLazyApi`, `applyDisplayMode`, `applyScreenMode`, `openTradeDrawer`)
 * are injected by the caller so this module stays decoupled from the
 * larger `app.js` graph.
 *
 * `setupCommandPalette({ runLazyApi, applyDisplayMode, applyScreenMode, openTradeDrawer })`
 * must be called once at bootstrap. Without the deps it falls back to
 * no-ops, which keeps the palette navigable but disables the lazy-loaded
 * section jumps and drawer entry points.
 */

import { safeText } from "./format.js";
import { activateResearchTabForSection } from "./researchTabs.js";

let _actions = [];

function buildActions({ runLazyApi, applyDisplayMode, applyScreenMode, openTradeDrawer }) {
  const lazyJump = (key, sectionId, screenMode) => () => {
    if (typeof applyScreenMode === "function" && screenMode) {
      applyScreenMode(screenMode, { updateUrl: true });
    }
    if (typeof runLazyApi === "function") runLazyApi(key);
    activateResearchTabForSection(sectionId);
    document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth" });
  };
  const sectionJump = (sectionId) => () => {
    activateResearchTabForSection(sectionId);
    document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth" });
  };
  const setDisplayMode = (mode) => () => {
    if (typeof applyDisplayMode === "function") applyDisplayMode(mode);
    const sel = document.getElementById("displayModeSelect");
    if (sel) sel.value = mode;
  };
  const setScreenMode = (mode) => () => {
    if (typeof applyScreenMode === "function") applyScreenMode(mode, { updateUrl: true });
  };
  return [
    { id: "scan", label: "Run Scan", shortcut: "S", icon: "search", action: () => document.getElementById("scanBtn")?.click() },
    { id: "refresh", label: "Refresh All", shortcut: "R", icon: "refresh", action: () => document.getElementById("refreshBtn")?.click() },
    { id: "ticker", label: "Quick Ticker Check", shortcut: "T", icon: "chart", action: () => { document.getElementById("tickerInput")?.focus(); document.getElementById("quickCheckSection")?.scrollIntoView({ behavior: "smooth" }); } },
    { id: "pending", label: "Go to Pending Trades", icon: "list", action: () => document.getElementById("pendingSection")?.scrollIntoView({ behavior: "smooth" }) },
    { id: "workflow", label: "Go to Workflow Kanban", icon: "list", action: () => { setScreenMode("operations")(); document.getElementById("workflowPrimary")?.scrollIntoView({ behavior: "smooth" }); } },
    { id: "health", label: "Go to Health Ribbon", icon: "pulse", action: () => { setScreenMode("diagnostics")(); document.getElementById("healthRibbon")?.scrollIntoView({ behavior: "smooth" }); } },
    { id: "shadow", label: "Go to Shadow Scoreboard", icon: "pulse", action: lazyJump("shadowScoreboard", "shadowScoreboardSection", "diagnostics") },
    { id: "review", label: "Go to Review Loop", icon: "pulse", action: lazyJump("reviewLoop", "reviewLoopSection", "diagnostics") },
    { id: "status-details", label: "Go to Status Details", icon: "pulse", action: () => { setScreenMode("diagnostics")(); document.getElementById("statusDetailsPanel")?.scrollIntoView({ behavior: "smooth" }); } },
    { id: "portfolio", label: "Go to Portfolio", icon: "wallet", action: lazyJump("portfolio", "portfolioSection", "research") },
    { id: "sectors", label: "Go to Sectors", icon: "grid", action: lazyJump("sectors", "sectorsSection", "research") },
    { id: "backtest", label: "Go to Backtests", icon: "clock", action: lazyJump("backtest", "backtestSection", "research") },
    { id: "performance", label: "Go to Performance", icon: "trending", action: lazyJump("performance", "performanceSection", "research") },
    { id: "onboarding", label: "Go to Connections & Settings", icon: "settings", action: lazyJump("onboarding", "onboardingSection") },
    { id: "calibration", label: "Go to Calibration", icon: "tune", action: lazyJump("calibration", "calibrationSection") },
    { id: "sec", label: "SEC Filing Compare", icon: "file", action: sectionJump("secCompareSection") },
    { id: "report", label: "Full Report", icon: "doc", action: sectionJump("reportSectionCard") },
    { id: "decision", label: "Decision Card (drawer)", icon: "card", action: () => (typeof openTradeDrawer === "function" ? openTradeDrawer({ tab: "decision" }) : document.getElementById("decisionSection")?.scrollIntoView({ behavior: "smooth" })) },
    { id: "recovery", label: "Failure Recovery (drawer)", icon: "first-aid", action: () => (typeof openTradeDrawer === "function" ? openTradeDrawer({ tab: "recovery" }) : document.getElementById("recoverySection")?.scrollIntoView({ behavior: "smooth" })) },
    { id: "profiles", label: "Strategy Presets", icon: "sliders", action: lazyJump("profiles", "settingsSection") },
    { id: "screen-operations", label: "Switch Screen: Today", shortcut: "Ctrl+1", icon: "home", action: setScreenMode("operations") },
    { id: "screen-research", label: "Switch Screen: Research", shortcut: "Ctrl+2", icon: "flask", action: setScreenMode("research") },
    { id: "screen-diagnostics", label: "Switch Screen: System", shortcut: "Ctrl+3", icon: "pulse", action: setScreenMode("diagnostics") },
    { id: "screen-settings", label: "Switch Screen: Settings", shortcut: "Ctrl+4", icon: "settings", action: setScreenMode("settings") },
    { id: "simple-view", label: "Switch to Simple view", icon: "eye", action: setDisplayMode("simple") },
    { id: "standard-view", label: "Switch to Standard view", icon: "eye", action: setDisplayMode("standard") },
    { id: "pro-view", label: "Switch to Pro view", icon: "eye", action: setDisplayMode("pro") },
    { id: "connect", label: "Open Connect Schwab", icon: "key", action: () => { window.location.href = "/?section=connect"; } },
    { id: "top", label: "Scroll to Top", icon: "arrow-up", action: () => window.scrollTo({ top: 0, behavior: "smooth" }) },
  ];
}

/** Element that had focus before the palette opened; restored on close. */
let _returnFocusEl = null;

export function openCommandPalette() {
  const dialog = document.getElementById("cmdPaletteDialog");
  if (!dialog) return;
  _returnFocusEl =
    document.activeElement instanceof HTMLElement ? document.activeElement : null;
  dialog.classList.add("open");
  const input = document.getElementById("cmdPaletteInput");
  if (input) { input.value = ""; input.focus(); }
  renderCommandResults("");
}

export function closeCommandPalette() {
  const dialog = document.getElementById("cmdPaletteDialog");
  if (dialog) dialog.classList.remove("open");
  // Return focus to whatever opened the palette (WCAG 2.4.3).
  if (_returnFocusEl?.isConnected) _returnFocusEl.focus();
  _returnFocusEl = null;
}

/** Sync .selected class, aria-selected, and the input's active descendant. */
function setSelectedItem(items, idx) {
  items.forEach((b, i) => {
    const active = i === idx;
    b.classList.toggle("selected", active);
    b.setAttribute("aria-selected", active ? "true" : "false");
  });
  const input = document.getElementById("cmdPaletteInput");
  if (input && items[idx]) input.setAttribute("aria-activedescendant", items[idx].id);
}

export function renderCommandResults(query) {
  const list = document.getElementById("cmdPaletteList");
  if (!list) return;
  const q = query.trim().toLowerCase();
  const filtered = q
    ? _actions.filter((a) => a.label.toLowerCase().includes(q) || a.id.includes(q))
    : _actions;
  list.innerHTML = filtered
    .map(
      (a, i) =>
        `<button class="cmd-palette-item${i === 0 ? " selected" : ""}" id="cmdPaletteOpt${i}" role="option" aria-selected="${i === 0 ? "true" : "false"}" data-idx="${i}" type="button" tabindex="-1">
          <span class="cmd-palette-label">${safeText(a.label)}</span>
          ${a.shortcut ? `<kbd class="cmd-palette-kbd">${safeText(a.shortcut)}</kbd>` : ""}
        </button>`
    )
    .join("");
  const items = Array.from(list.querySelectorAll(".cmd-palette-item"));
  const input = document.getElementById("cmdPaletteInput");
  if (input) input.setAttribute("aria-activedescendant", items.length ? "cmdPaletteOpt0" : "");
  items.forEach((btn, idx) => {
    btn.addEventListener("click", () => {
      closeCommandPalette();
      filtered[idx]?.action();
    });
    btn.addEventListener("mouseenter", () => setSelectedItem(items, idx));
  });
}

export function setupCommandPalette(deps = {}) {
  _actions = buildActions(deps);
  const input = document.getElementById("cmdPaletteInput");
  if (!input) return;
  input.addEventListener("input", () => renderCommandResults(input.value));
  input.addEventListener("keydown", (e) => {
    const list = document.getElementById("cmdPaletteList");
    const items = list ? Array.from(list.querySelectorAll(".cmd-palette-item")) : [];
    const cur = items.findIndex((b) => b.classList.contains("selected"));
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = Math.min(cur + 1, items.length - 1);
      setSelectedItem(items, next);
      items[next]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = Math.max(cur - 1, 0);
      setSelectedItem(items, prev);
      items[prev]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      const sel = items[cur >= 0 ? cur : 0];
      if (sel) sel.click();
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeCommandPalette();
    } else if (e.key === "Tab") {
      // Focus trap: the input is the palette's only tab stop; results are
      // driven by arrow keys per the combobox pattern.
      e.preventDefault();
    }
  });
  document.getElementById("cmdPaletteDialog")?.addEventListener("click", (e) => {
    if (e.target.id === "cmdPaletteDialog") closeCommandPalette();
  });
}
