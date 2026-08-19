/**
 * Scan studio: timeframe grouping, strategy descriptions, and universe picker.
 *
 * The live engine is still daily Stage 2 + VCP. This panel lets operators
 * choose a horizon, read what each sleeve does, and scan a universe other
 * than S&P 1500. Weekly/monthly resample the same daily bars (Friday week /
 * month-end). Intraday gap/range sleeves use session structure on the latest
 * daily bar, not a minute-bar book.
 */

import { state, SCAN_STUDIO_PREFS_KEY, LEGACY_SCAN_STUDIO_PREFS_KEY } from "../modules/state.js";
import { api } from "../modules/api.js";
import { escapeHtml, safeText } from "../modules/format.js";

function _fb(id, display_name, timeframe, status, origin, description, extra = {}) {
  return { id, display_name, timeframe, status, runnable: true, origin, description, ...extra };
}

const FALLBACK_CATALOG = Object.freeze({
  timeframes: [
    { id: "intraday", display_name: "Intraday", description: "Live-quote confirmation of a daily setup.", default_strategy_id: "breakout_confirm" },
    { id: "daily", display_name: "Daily", description: "Primary live engine on daily bars.", default_strategy_id: "trend_breakout" },
    { id: "weekly", display_name: "Weekly", description: "Multi-week horizon on the daily engine.", default_strategy_id: "weekly_swing" },
    { id: "monthly", display_name: "Monthly", description: "Position-style horizon on the daily engine.", default_strategy_id: "monthly_position" },
  ],
  strategies: [
    _fb("breakout_confirm", "Intraday breakout confirm", "intraday", "live_overlay", "iterated", "Live-quote overlay on the daily Stage 2 name.", { primary_for_timeframe: true }),
    _fb("gap_and_go", "Gap and go", "intraday", "shadow", "iterated", "Session-structure gap-and-hold on the last completed daily bar."),
    _fb("range_expansion", "Range expansion", "intraday", "shadow", "iterated", "Opening-drive proxy: close through the prior high with expanded range."),
    _fb("opening_range_breakout", "RVOL strong-close (ORB proxy)", "intraday", "research", "literature", "Completed-daily RVOL strong-close screen — not a 5-minute ORB engine."),
    _fb("trend_breakout", "Stage 2 / VCP breakout", "daily", "live", "iterated", "Weinstein Stage 2 uptrend plus volume contraction. This is the live book.", { primary_for_timeframe: true }),
    _fb("pullback", "Trend pullback", "daily", "shadow", "iterated", "Uptrend names pulling back toward the 50-day SMA."),
    _fb("pead_primary", "PEAD earnings drift", "daily", "shadow", "iterated", "Post-earnings announcement drift. Paper / canary only."),
    _fb("donchian_20", "Donchian 20-day breakout", "daily", "shadow", "iterated", "Close through the prior 20-day high with a 200-day SMA filter."),
    _fb("nr7_breakout", "NR7 breakout", "daily", "shadow", "iterated", "Narrowest range of the last 7 sessions, then a break of that high."),
    _fb("st_reversal_5d", "5-day loser bounce (screen)", "daily", "research", "literature", "Long-only 1-week bounce screen on completed daily bars."),
    _fb("overnight_gap_fade", "Gap-down recovery (EOD label)", "daily", "research", "literature", "EOD label of a gap-down that recovered by the completed close."),
    _fb("weekly_swing", "Weekly Stage 2", "weekly", "research", "iterated", "Weinstein-style weekly Stage 2 on resampled Friday bars.", { primary_for_timeframe: true }),
    _fb("weekly_vcp", "Weekly volume dryness", "weekly", "research", "iterated", "Multi-week volume dryness above the 30-week SMA."),
    _fb("weekly_breakout", "Weekly breakout", "weekly", "research", "iterated", "Weekly close through the prior week's high."),
    _fb("weekly_reversal", "Weekly loser bounce (screen)", "weekly", "research", "literature", "Prior completed week down, this completed week turns up."),
    _fb("monthly_position", "10-month SMA (Faber)", "monthly", "research", "iterated", "Month-end close above a rising 10-month SMA.", { primary_for_timeframe: true }),
    _fb("monthly_52w_high", "Monthly 52-week high", "monthly", "research", "iterated", "Month-end close near the 52-week high, above the 10-month SMA."),
    _fb("monthly_pullback", "Monthly SMA pullback", "monthly", "research", "iterated", "Uptrend pullback toward the 10-month SMA."),
    _fb("momentum_12_1", "12-1 strength screen", "monthly", "research", "literature", "12-month formation skipping the most recent month."),
    _fb("tsmom_12m", "12-month trend screen", "monthly", "research", "literature", "Completed month-end close above the close 12 months ago."),
  ],
  universes: [
    { id: "sp1500", display_name: "S&P 1500", description: "Default live universe.", available: true },
    { id: "sp500", display_name: "S&P 500", description: "Large-cap US names only.", available: true },
    { id: "nasdaq100", display_name: "Nasdaq-100", description: "Nasdaq-100 constituents.", available: true },
    { id: "sector_etfs", display_name: "Sector ETFs", description: "Liquid sector and index ETFs.", available: true },
    { id: "focused", display_name: "S&P 1500 sample", description: "Deterministic subset of S&P 1500.", available: true },
    { id: "custom", display_name: "Custom tickers", description: "Paste your own symbols.", available: true },
  ],
  defaults: { timeframe: "daily", universe_preset: "sp1500", strategy_ids: ["trend_breakout"] },
  notes: { weekly_monthly: "Weekly/monthly resample daily bars (Friday week / month-end). Research only — not a live book." },
});

function catalogFromBootstrap() {
  if (typeof document === "undefined") return null;
  const el = document.getElementById("scanCatalogBootstrap");
  if (!el) return null;
  try {
    const parsed = JSON.parse(el.textContent || "");
    if (parsed && Array.isArray(parsed.strategies) && parsed.strategies.length) return parsed;
  } catch {
    /* unreplaced token or invalid JSON */
  }
  return null;
}

function catalogHasStrategies(value) {
  return Boolean(value && typeof value === "object" && Array.isArray(value.strategies) && value.strategies.length);
}

function adoptCatalog(next) {
  if (!catalogHasStrategies(next)) return false;
  const currentCount = Array.isArray(state.scanCatalog?.strategies) ? state.scanCatalog.strategies.length : 0;
  if (next.strategies.length < currentCount) return false;
  state.scanCatalog = next;
  return true;
}

function catalog() {
  if (catalogHasStrategies(state.scanCatalog)) return state.scanCatalog;
  const boot = catalogFromBootstrap();
  if (boot) return boot;
  return FALLBACK_CATALOG;
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

function prefsFromParsed(parsed, base, { resetStrategyIds = false } = {}) {
  const timeframe = safeText(parsed.timeframe || base.timeframe).toLowerCase() || base.timeframe;
  const universe_preset =
    safeText(parsed.universe_preset || base.universe_preset).toLowerCase() || base.universe_preset;
  let ids = Array.isArray(parsed.strategy_ids)
    ? parsed.strategy_ids.map((s) => String(s || "").trim().toLowerCase()).filter(Boolean)
    : [...base.strategy_ids];
  if (resetStrategyIds) {
    const primary = primaryStrategyIdForTimeframe(timeframe);
    ids = primary ? [primary] : [...base.strategy_ids];
  }
  return {
    timeframe,
    universe_preset,
    strategy_ids: ids.length ? ids : [...base.strategy_ids],
    tickersText: typeof parsed.tickersText === "string" ? parsed.tickersText : "",
  };
}

export function loadScanStudioPrefs() {
  const base = defaultPrefs();
  try {
    const rawV2 = localStorage.getItem(SCAN_STUDIO_PREFS_KEY);
    if (rawV2) {
      const parsed = JSON.parse(rawV2);
      if (!parsed || typeof parsed !== "object") return { ...base, strategy_ids: [...base.strategy_ids] };
      return prefsFromParsed(parsed, base);
    }
    const rawV1 = localStorage.getItem(LEGACY_SCAN_STUDIO_PREFS_KEY);
    if (!rawV1) return { ...base, strategy_ids: [...base.strategy_ids] };
    const parsed = JSON.parse(rawV1);
    if (!parsed || typeof parsed !== "object") return { ...base, strategy_ids: [...base.strategy_ids] };
    const migrated = prefsFromParsed(parsed, base, { resetStrategyIds: true });
    saveScanStudioPrefs(migrated);
    return migrated;
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
      const n = strategiesForTimeframe(tf.id).length;
      const label = n ? `${tf.display_name} · ${n}` : tf.display_name;
      return `<button type="button" class="scan-studio-tf${active ? " is-active" : ""}" role="tab" data-scan-timeframe="${escapeHtml(tf.id)}" aria-selected="${active ? "true" : "false"}" aria-pressed="${active ? "true" : "false"}">${escapeHtml(label)}</button>`;
    })
    .join("");
}

function statusChip(status) {
  const raw = safeText(status || "research").toLowerCase();
  const label =
    raw === "live" ? "Live" : raw === "live_overlay" ? "Overlay" : raw === "shadow" ? "Shadow" : "Research";
  return `<span class="scan-studio-status scan-studio-status--${escapeHtml(raw)}">${escapeHtml(label)}</span>`;
}

function originChip(origin) {
  const raw = safeText(origin || "").toLowerCase();
  if (raw === "literature") {
    return `<span class="scan-studio-origin scan-studio-origin--literature">Paper</span>`;
  }
  if (raw === "iterated") {
    return `<span class="scan-studio-origin scan-studio-origin--iterated">Yours</span>`;
  }
  return "";
}

function evidenceLine(ev) {
  if (!ev || typeof ev !== "object") return "";
  const cites = Array.isArray(ev.citations) ? ev.citations.map((c) => String(c || "").trim()).filter(Boolean) : [];
  const caveat = safeText(ev.caveat || "");
  const strength = safeText(ev.strength || "");
  if (!cites.length && !caveat) return "";
  const head = strength ? `Evidence (${strength})` : "Evidence";
  const citeText = cites.length ? `${cites.join("; ")}.` : "";
  return `<span class="scan-studio-evidence">${escapeHtml(head)}: ${escapeHtml(citeText)}${caveat ? ` ${escapeHtml(caveat)}` : ""}</span>`;
}

function isPaperStrategy(row) {
  return safeText(row?.origin || "").toLowerCase() === "literature";
}

function renderStrategyCard(row, prefs, primaryId) {
  const id = String(row.id || "");
  const selected = new Set(prefs.strategy_ids || []);
  const checked = selected.has(id);
  const disabled = row.available === false || row.runnable === false;
  const paper = isPaperStrategy(row);
  const status = safeText(row.status || "research").toLowerCase();
  const mods = [
    checked ? "is-selected" : "",
    disabled ? "is-disabled" : "",
    id === primaryId ? "scan-studio-strategy--primary" : "",
    paper ? "scan-studio-strategy--paper" : "scan-studio-strategy--yours",
    status === "live" || status === "live_overlay" ? "scan-studio-strategy--live" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const primaryChip =
    id === primaryId ? `<span class="scan-studio-primary-chip">Primary</span>` : "";
  return `<label class="scan-studio-strategy ${mods}">
      <input class="scan-studio-strategy-input" type="checkbox" data-scan-strategy="${escapeHtml(id)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
      <span class="scan-studio-check" aria-hidden="true"></span>
      <span class="scan-studio-strategy-copy">
        <span class="scan-studio-strategy-head">
          <strong>${escapeHtml(row.display_name || id)}</strong>
          ${primaryChip}
          ${statusChip(row.status)}
          ${originChip(row.origin)}
        </span>
        <span class="scan-studio-strategy-desc">${escapeHtml(row.description || "")}</span>
        ${evidenceLine(row.evidence)}
      </span>
    </label>`;
}

function renderStrategyGroup(title, hint, rows, prefs, primaryId, extraClass) {
  if (!rows.length) return "";
  return `<div class="scan-studio-group${extraClass ? ` ${extraClass}` : ""}">
      <div class="scan-studio-group-head">
        <p class="scan-studio-kicker">${escapeHtml(title)}</p>
        ${hint ? `<p class="muted small scan-studio-group-hint">${escapeHtml(hint)}</p>` : ""}
      </div>
      <div class="scan-studio-strategies">${rows.map((row) => renderStrategyCard(row, prefs, primaryId)).join("")}</div>
    </div>`;
}

function renderStrategyCards(prefs) {
  const rows = strategiesForTimeframe(prefs.timeframe);
  if (!rows.length) {
    return `<p class="muted small">No strategies in this timeframe yet.</p>`;
  }
  const primaryId = primaryStrategyIdForTimeframe(prefs.timeframe);
  const yours = rows.filter((row) => !isPaperStrategy(row));
  const paper = rows.filter(isPaperStrategy);
  yours.sort((a, b) => {
    if (String(a.id) === primaryId) return -1;
    if (String(b.id) === primaryId) return 1;
    return 0;
  });
  return `${renderStrategyGroup("Yours", "Iterated sleeves. The primary is this tab’s default.", yours, prefs, primaryId, "")}
    ${renderStrategyGroup("Paper", "Literature screens — opt-in, not the published books.", paper, prefs, primaryId, "scan-studio-group--paper")}`;
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

function catalogLoadNote(prefs) {
  const err = safeText(state.scanCatalogError || "");
  if (err) {
    return `<p class="scan-studio-note" role="status">Catalog refresh failed (${escapeHtml(err)}). Showing the bundled sleeve list — hard-refresh if this stays stale.</p>`;
  }
  const total = Array.isArray(catalog().strategies) ? catalog().strategies.length : 0;
  const here = strategiesForTimeframe(prefs?.timeframe || catalog().defaults?.timeframe || "daily").length;
  if (total > here) {
    return `<p class="muted small scan-studio-tf-desc">This tab has ${here} of ${total} sleeves. Weekly, monthly, and intraday tabs hold the rest.</p>`;
  }
  return "";
}

function horizonNote(prefs) {
  const tf = String(prefs.timeframe || "daily");
  if (tf === "weekly" || tf === "monthly") {
    const note = catalog().notes?.weekly_monthly || "Weekly/monthly resample daily bars (Friday week / month-end). Research only — not a live book.";
    return `<p class="scan-studio-note" role="status">${escapeHtml(note)}</p>`;
  }
  if (tf === "intraday") {
    return `<p class="scan-studio-note" role="status">Intraday confirm is a live-quote overlay. Gap-and-go / range expansion are your session-structure sleeves on completed daily bars. RVOL strong-close is a daily proxy of the 5-minute ORB literature — not a minute-bar book.</p>`;
  }
  return "";
}

export function scanStudioMarkup(prefs) {
  const uni = universeRow(prefs.universe_preset);
  const tf = timeframeRow(prefs.timeframe);
  const custom = prefs.universe_preset === "custom";
  return `
    <div class="scan-studio-head">
      <p class="scan-studio-kicker workspace-eyebrow">Scan lens</p>
      <h3 class="scan-studio-title">Scan studio</h3>
      <p class="muted small scan-studio-lede">One primary per horizon. Paper is opt-in. Live execution stays daily Stage 2 / VCP. Switch timeframe tabs to see all 20 sleeves.</p>
    </div>
    <div class="scan-studio-tf-row" role="tablist" aria-label="Strategy timeframe">${renderTimeframeTabs(prefs)}</div>
    <p class="muted small scan-studio-tf-desc">${escapeHtml(tf?.description || "")}</p>
    ${catalogLoadNote(prefs)}
    ${horizonNote(prefs)}
    <div class="scan-studio-board" aria-label="Strategies in this timeframe">${renderStrategyCards(prefs)}</div>
    <div class="scan-studio-universe">
      <label class="scan-studio-field" for="scanUniverseSelect">
        <span class="scan-studio-kicker">Universe</span>
        <select id="scanUniverseSelect">${renderUniverseOptions(prefs)}</select>
      </label>
      <p class="muted small" id="scanUniverseHint">${escapeHtml(uni?.description || "")}</p>
      <label class="scan-studio-field${custom ? "" : " hidden"}" id="scanCustomTickersWrap" for="scanCustomTickers">
        <span class="scan-studio-kicker">Custom tickers</span>
        <input id="scanCustomTickers" type="text" placeholder="AAPL, MSFT, NVDA" value="${escapeHtml(prefs.tickersText || "")}" />
      </label>
    </div>
  `;
}

export function renderScanStudio() {
  const root = document.getElementById("scanStudioPanel");
  if (!root) return;
  const prefs = state.scanStudioPrefs || loadScanStudioPrefs();
  state.scanStudioPrefs = prefs;
  root.innerHTML = scanStudioMarkup(prefs);
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

function primaryStrategyIdForTimeframe(timeframe) {
  const tf = timeframeRow(timeframe);
  const fromTf = safeText(tf?.default_strategy_id || "").toLowerCase();
  if (fromTf) return fromTf;
  const marked = strategiesForTimeframe(timeframe).find((s) => s.primary_for_timeframe);
  if (marked?.id) return String(marked.id);
  const rows = strategiesForTimeframe(timeframe).filter((s) => s.runnable !== false);
  return rows[0] ? String(rows[0].id) : "trend_breakout";
}

export { primaryStrategyIdForTimeframe };

function selectTimeframe(timeframe) {
  const prefs = state.scanStudioPrefs || loadScanStudioPrefs();
  prefs.timeframe = timeframe;
  const inFrame = new Set(strategiesForTimeframe(timeframe).map((s) => String(s.id)));
  const kept = (prefs.strategy_ids || []).filter((id) => inFrame.has(id));
  prefs.strategy_ids = kept.length ? kept : [primaryStrategyIdForTimeframe(timeframe)];
  saveScanStudioPrefs(prefs);
  renderScanStudio();
}

export function bindScanStudio() {
  const root = document.getElementById("scanStudioPanel");
  if (!root || root.dataset.bound === "1") return;
  root.dataset.bound = "1";
  if (!catalogHasStrategies(state.scanCatalog)) {
    state.scanCatalog = catalogFromBootstrap() || FALLBACK_CATALOG;
  }
  if (!state.scanStudioPrefs) state.scanStudioPrefs = loadScanStudioPrefs();
  renderScanStudio();
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
  if (!catalogHasStrategies(state.scanCatalog)) {
    adoptCatalog(catalogFromBootstrap() || FALLBACK_CATALOG);
  } else {
    adoptCatalog(catalogFromBootstrap());
  }
  const out = await api.get("/api/scan-catalog", { timeoutMs: 15000 });
  if (out.ok && catalogHasStrategies(out.data)) {
    adoptCatalog(out.data);
    state.scanCatalogError = "";
  } else {
    state.scanCatalogError = safeText(out.error || out.user_message || "Scan catalog unavailable");
    if (!catalogHasStrategies(state.scanCatalog)) {
      adoptCatalog(FALLBACK_CATALOG);
    }
  }
  if (!state.scanStudioPrefs) {
    state.scanStudioPrefs = loadScanStudioPrefs();
  }
  renderScanStudio();
  return state.scanCatalog;
}
