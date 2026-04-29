/**
 * Global keyboard shortcuts. Decoupled from the rest of the dashboard:
 * the caller injects the side effects it wants the keys to trigger.
 *
 *   setupKeyboardShortcuts({
 *     openCommandPalette,
 *     closeCommandPalette,
 *     showToast,
 *     applyDisplayMode,
 *     applyScreenMode,
 *   })
 *
 * Shortcuts are no-op when the focus is in a form element (`INPUT`,
 * `TEXTAREA`, `SELECT`) or when a modifier other than Ctrl/Meta+K is held.
 */

export function setupKeyboardShortcuts({
  openCommandPalette,
  closeCommandPalette,
  showToast,
  applyDisplayMode,
  applyScreenMode,
} = {}) {
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
      e.preventDefault();
      const dialog = document.getElementById("cmdPaletteDialog");
      if (dialog?.classList.contains("open")) closeCommandPalette?.();
      else openCommandPalette?.();
      return;
    }

    if ((e.ctrlKey || e.metaKey) && ["1", "2", "3", "4"].includes(e.key)) {
      e.preventDefault();
      const screenMap = { "1": "operations", "2": "research", "3": "diagnostics", "4": "settings" };
      const mode = screenMap[e.key];
      if (mode) {
        applyScreenMode?.(mode, { updateUrl: true });
        const pretty = mode.charAt(0).toUpperCase() + mode.slice(1);
        showToast?.(`Switched to ${pretty}`, "info", 1800);
      }
      return;
    }

    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    switch (e.key) {
      case "r":
      case "R":
        e.preventDefault();
        document.getElementById("refreshBtn")?.click();
        showToast?.("Refreshing all data...", "info", 2000);
        break;
      case "s":
      case "S":
        e.preventDefault();
        document.getElementById("scanBtn")?.click();
        break;
      case "t":
      case "T":
        e.preventDefault();
        document.getElementById("tickerInput")?.focus();
        document.getElementById("quickCheckSection")?.scrollIntoView({ behavior: "smooth" });
        break;
      case "?":
        e.preventDefault();
        showToast?.("Shortcuts: Ctrl+K palette, Ctrl/Cmd+1..4 screens, R refresh, S scan, T ticker, 1-3 detail", "info", 5500);
        break;
      case "1":
        e.preventDefault();
        applyDisplayMode?.("simple");
        document.getElementById("displayModeSelect").value = "simple";
        showToast?.("Switched to Simple view", "info", 2000);
        break;
      case "2":
        e.preventDefault();
        applyDisplayMode?.("standard");
        document.getElementById("displayModeSelect").value = "standard";
        showToast?.("Switched to Standard view", "info", 2000);
        break;
      case "3":
        e.preventDefault();
        applyDisplayMode?.("pro");
        document.getElementById("displayModeSelect").value = "pro";
        showToast?.("Switched to Pro view", "info", 2000);
        break;
    }
  });
}
