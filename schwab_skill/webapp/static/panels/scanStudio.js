/**
 * Scan studio: timeframe grouping, strategy descriptions, and universe picker.
 *
 * The live engine is still daily Stage 2 + VCP. This panel lets operators
 * choose a horizon, read what each sleeve does, and scan a universe other
 * than S&P 1500. Weekly/monthly resample the same daily bars (Friday week /
 * month-end). Intraday gap/range sleeves use session structure on the latest
 * daily bar, not a minute-bar book.
 */

import { state, SCAN_STUDIO_PREFS_KEY } from "../modules/state.js";
import { api } from "../modules/api.js";
import { escapeHtml, safeText } from "../modules/format.js";

const FALLBACK_CATALOG = Object.freeze({
  timeframes: [
    { id: "intraday", display_name: "Intraday", description: "Live-quote confirmation of a daily setup." },
    { id: "daily", display_name: "Daily", description: "Primary live engine on daily bars." },
    { id: "weekly", display_name: "Weekly", description: "Multi-week horizon on the daily engine." },
    { id: "monthly", display_name: "Monthly", description: "Position-style horizon on the daily engine." },
  ],
  strategies: [
    {
      id: "trend_breakout",
      display_name: "Stage 2 / VCP breakout",
      timeframe: "daily",
      status: "live",
      runnable: true,
      description: "Weinstein Stage 2 uptrend plus volume contraction. This is the live book.",
    },
  ],
  universes: [
    { id: "sp1500", display_name: "S&P 1500", description: "Default live universe.", available: true },
    { id: "custom", display_name: "Custom tickers", description: "Paste your own symbols.", available: true },
  ],
  defaults: { timeframe: "daily", universe_preset: "sp1500", strategy_ids: ["trend_breakout"] },
  notes: { weekly_monthly: "Weekly/monthly still use daily bars." },
});

function catalog() {
  return state.scanCatalog && typeof state.scanCatalog === "object" ? state.scanCatalog : FALLBACK_CATALOG;
}

function defaultPrefs() {
  const defaults = catalog().defaults || {};
  return {
    timeframe: defaults.timeframe || "daily",
    universe_preset: defaults.universe_preset || "sp1500",
    strategy_ids: Array.isArray(defaults.strategy_ids) ? [...defaults.strategy_ids] : ["trend_breakout"],
    tickersText: "",
  };
}

export function loadScanStudioPrefs() {
  const base = defaultPrefs();
  try {
    const raw = localStorage.getItem(SCAN_STUDIO_PREFS_KEY);
    if (!raw) return { ...base, strategy_ids: [...base.strategy_ids] };
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return { ...base, strategy_ids: [...base.strategy_ids] };
    const ids = Array.isArray(parsed.strategy_ids)
      ? parsed.strategy_ids.map((s) => String(s || "").trim().toLowerCase()).filter(Boolean)
      : [...base.strategy_ids];
    return {
      timeframe: safeText(parsed.timeframe || base.timeframe).toLowerCase() || base.timeframe,
      universe_preset: safeText(parsed.universe_preset || base.universe_preset).toLowerCase() || base.universe_preset,
      strategy_ids: ids.length ? ids : [...base.strategy_ids],
      tickersText: typeof parsed.tickersText === "string" ? parsed.tickersText : "",
    };
  } catch {
    return { ...base, strategy_ids: [...base.strategy_ids] };
  }
}

export function saveScanStudioPrefs(prefs) {
  try {
    localStorage.setItem(SCAN_STUDIO_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* ignore quota / private-mode */
  }
  state.scanStudioPrefs = prefs;
}

function parseTickerText(text) {
  return String(text || "")
    .split(/[\s,;]+/)
    .map((t) => t.trim().toUpperCase())
    .filter((t) => /^[A-Z0-9.\-]{1,16}$/.test(t));
}

function strategiesForTimeframe(timeframe) {
  const rows = Array.isArray(catalog().strategies) ? catalog().strategies : [];
  return rows.filter((s) => String(s.timeframe || "") === timeframe);
}

function universeRow(id) {
  const rows = Array.isArray(catalog().universes) ? catalog().universes : [];
  return rows.find((u) => String(u.id) === id) || null;
}

function timeframeRow(id) {
  const rows = Array.isArray(catalog().timeframes) ? catalog().timeframes : [];
  return rows.find((t) => String(t.id) === id) || null;
}

export function scanStudioProgressLabel(prefs = state.scanStudioPrefs) {
  const p = prefs || loadScanStudioPrefs();
  const uni = universeRow(p.universe_preset);
  const tf = timeframeRow(p.timeframe);
  const uniLabel = uni?.display_name || "S&P 1500";
  const tfLabel = tf?.display_name || "Daily";
  if (p.universe_preset === "custom") {
    const n = parseTickerText(p.tickersText).length;
    return `Scanning ${n} custom ticker${n === 1 ? "" : "s"} · ${tfLabel}…`;
  }
  return `Scanning ${uniLabel} · ${tfLabel}…`;
}

export function readScanStudioBody() {
  const prefs = state.scanStudioPrefs || loadScanStudioPrefs();
  const body = {
    scan_timeframe: prefs.timeframe || "daily",
    universe_preset: prefs.universe_preset || "sp1500",
    strategy_ids: Array.isArray(prefs.strategy_ids) ? [...prefs.strategy_ids] : ["trend_breakout"],
  };
  if (!body.strategy_ids.length) {
    return { error: "Select at least one strategy in this timeframe." };
  }
  if (prefs.universe_preset === "custom") {
    const tickers = parseTickerText(prefs.tickersText);
    if (!tickers.length) return { error: "Add at least one ticker for a custom universe." };
    body.universe_mode = "tickers";
    body.tickers = tickers;
  }
  return { body };
}

function renderTimeframeTabs(prefs) {
  const frames = Array.isArray(catalog().timeframes) ? catalog().timeframes : [];
  return frames
    .map((tf) => {
      const active = tf.id === prefs.timeframe;
      return `<button type="button" class="scan-studio-tf${active ? " is-active" : ""}" data-scan-timeframe="${escapeHtml(tf.id)}" aria-pressed="${active ? "true" : "false"}">${escapeHtml(tf.display_name)}</button>`;
    })
    .join("");
}

function statusChip(status) {
  const raw = safeText(status || "research").toLowerCase();
  const label =
    raw === "live" ? "Live" : raw === "live_overlay" ? "Overlay" : raw === "shadow" ? "Shadow" : "Research";
  return `<span class="scan-studio-status scan-studio-status--${escapeHtml(raw)}">${escapeHtml(label)}</span>`;
}

function renderStrategyCards(prefs) {
  const rows = strategiesForTimeframe(prefs.timeframe);
  if (!rows.length) {
    return `<p class="muted small">No strategies in this timeframe yet.</p>`;
  }
  const selected = new Set(prefs.strategy_ids || []);
  return rows
    .map((s) => {
      const id = String(s.id);
      const checked = selected.has(id);
      const disabled = s.available === false || s.runnable === false;
      return `<label class="scan-studio-strategy${checked ? " is-selected" : ""}${disabled ? " is-disabled" : ""}">
        <input type="checkbox" data-scan-strategy="${escapeHtml(id)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
        <span class="scan-studio-strategy-copy">
          <span class="scan-studio-strategy-head">
            <strong>${escapeHtml(s.display_name || id)}</strong>
            ${statusChip(s.status)}
          </span>
          <span class="scan-studio-strategy-desc">${escapeHtml(s.description || "")}</span>
        </span>
      </label>`;
    })
    .join("");
}

function renderUniverseOptions(prefs) {
  const rows = Array.isArray(catalog().universes) ? catalog().universes : [];
  return rows
    .map((u) => {
      const disabled = u.available === false;
      const selected = u.id === prefs.universe_preset && !disabled;
      const title = u.description || "";
      return `<option value="${escapeHtml(u.id)}" title="${escapeHtml(title)}" ${selected ? "selected" : ""} ${disabled ? "disabled" : ""}>${escapeHtml(u.display_name)}${disabled ? " (soon)" : ""}</option>`;
    })
    .join("");
}

function horizonNote(prefs) {
  const tf = String(prefs.timeframe || "daily");
  if (tf === "weekly" || tf === "monthly") {
    const note = catalog().notes?.weekly_monthly || "Weekly/monthly resample daily bars (Friday week / month-end). Research only — not a live book.";
    return `<p class="scan-studio-note" role="status">${escapeHtml(note)}</p>`;
  }
  if (tf === "intraday") {
    return `<p class="scan-studio-note" role="status">Intraday confirm is a live-quote overlay. Gap-and-go and range expansion use the latest daily bar as session structure — not a separate minute-bar scanner.</p>`;
  }
  return "";
}

export function renderScanStudio() {
  const root = document.getElementById("scanStudioPanel");
  if (!root) return;
  const prefs = state.scanStudioPrefs || loadScanStudioPrefs();
  state.scanStudioPrefs = prefs;
  const uni = universeRow(prefs.universe_preset);
  const tf = timeframeRow(prefs.timeframe);
  const custom = prefs.universe_preset === "custom";
  root.innerHTML = `
    <div class="scan-studio-head">
      <div>
        <h3 class="scan-studio-title">Scan studio</h3>
        <p class="muted small">Pick a horizon, read the sleeve, and choose a universe. Live execution stays the daily Stage 2 / VCP engine; extra sleeves are shadow or research only.</p>
      </div>
    </div>
    <div class="scan-studio-tf-row" role="tablist" aria-label="Strategy timeframe">${renderTimeframeTabs(prefs)}</div>
    <p class="muted small scan-studio-tf-desc">${escapeHtml(tf?.description || "")}</p>
    ${horizonNote(prefs)}
    <div class="scan-studio-strategies" aria-label="Strategies in this timeframe">${renderStrategyCards(prefs)}</div>
    <div class="scan-studio-universe">
      <label class="scan-studio-field" for="scanUniverseSelect">
        <span class="muted small">Universe</span>
        <select id="scanUniverseSelect">${renderUniverseOptions(prefs)}</select>
      </label>
      <p class="muted small" id="scanUniverseHint">${escapeHtml(uni?.description || "")}</p>
      <label class="scan-studio-field${custom ? "" : " hidden"}" id="scanCustomTickersWrap" for="scanCustomTickers">
        <span class="muted small">Custom tickers</span>
        <input id="scanCustomTickers" type="text" placeholder="AAPL, MSFT, NVDA" value="${escapeHtml(prefs.tickersText || "")}" />
      </label>
    </div>
  `;
}

function syncPrefsFromDom() {
  const prefs = state.scanStudioPrefs || loadScanStudioPrefs();
  const tfBtn = document.querySelector(".scan-studio-tf.is-active");
  if (tfBtn?.dataset.scanTimeframe) prefs.timeframe = tfBtn.dataset.scanTimeframe;
  const uni = document.getElementById("scanUniverseSelect");
  if (uni?.value) prefs.universe_preset = uni.value;
  const tickers = document.getElementById("scanCustomTickers");
  if (tickers) prefs.tickersText = tickers.value;
  const boxes = document.querySelectorAll("[data-scan-strategy]");
  if (boxes.length) {
    prefs.strategy_ids = [...boxes]
      .filter((el) => el.checked && !el.disabled)
      .map((el) => String(el.getAttribute("data-scan-strategy") || ""));
  }
  saveScanStudioPrefs(prefs);
  return prefs;
}

function selectTimeframe(timeframe) {
  const prefs = state.scanStudioPrefs || loadScanStudioPrefs();
  prefs.timeframe = timeframe;
  const inFrame = new Set(strategiesForTimeframe(timeframe).map((s) => String(s.id)));
  const kept = (prefs.strategy_ids || []).filter((id) => inFrame.has(id));
  prefs.strategy_ids = kept.length ? kept : strategiesForTimeframe(timeframe).filter((s) => s.runnable !== false).map((s) => String(s.id));
  saveScanStudioPrefs(prefs);
  renderScanStudio();
}

export function bindScanStudio() {
  const root = document.getElementById("scanStudioPanel");
  if (!root || root.dataset.bound === "1") return;
  root.dataset.bound = "1";
  root.addEventListener("click", (ev) => {
    const btn = ev.target?.closest?.("[data-scan-timeframe]");
    if (!btn) return;
    selectTimeframe(btn.getAttribute("data-scan-timeframe"));
  });
  root.addEventListener("change", (ev) => {
    const t = ev.target;
    if (!t) return;
    if (t.id === "scanUniverseSelect" || t.id === "scanCustomTickers" || t.hasAttribute("data-scan-strategy")) {
      syncPrefsFromDom();
      if (t.id === "scanUniverseSelect") renderScanStudio();
    }
  });
}

export async function loadScanCatalog() {
  const out = await api.get("/api/scan-catalog", { timeoutMs: 15000 });
  if (out.ok && out.data && typeof out.data === "object") {
    state.scanCatalog = out.data;
  } else {
    state.scanCatalog = FALLBACK_CATALOG;
  }
  if (!state.scanStudioPrefs) {
    state.scanStudioPrefs = loadScanStudioPrefs();
  }
  renderScanStudio();
  return state.scanCatalog;
}
