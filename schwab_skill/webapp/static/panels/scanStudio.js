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

const FALLBACK_CATALOG = Object.freeze({
  timeframes: [
    { id: "intraday", display_name: "Intraday", description: "Live-quote confirmation of a daily setup.", default_strategy_id: "breakout_confirm" },
    { id: "daily", display_name: "Daily", description: "Primary live engine on daily bars.", default_strategy_id: "trend_breakout" },
    { id: "weekly", display_name: "Weekly", description: "Multi-week horizon on the daily engine.", default_strategy_id: "weekly_swing" },
    { id: "monthly", display_name: "Monthly", description: "Position-style horizon on the daily engine.", default_strategy_id: "monthly_position" },
  ],
  strategies: [
    {
      id: "trend_breakout",
      display_name: "Stage 2 / VCP breakout",
      timeframe: "daily",
      status: "live",
      runnable: true,
      origin: "iterated",
      primary_for_timeframe: true,
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
      return `<button type="button" class="scan-studio-tf${active ? " is-active" : ""}" role="tab" data-scan-timeframe="${escapeHtml(tf.id)}" aria-selected="${active ? "true" : "false"}" aria-pressed="${active ? "true" : "false"}">${escapeHtml(tf.display_name)}</button>`;
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
      <p class="muted small scan-studio-lede">One primary per horizon. Paper is opt-in. Live execution stays daily Stage 2 / VCP.</p>
    </div>
    <div class="scan-studio-tf-row" role="tablist" aria-label="Strategy timeframe">${renderTimeframeTabs(prefs)}</div>
    <p class="muted small scan-studio-tf-desc">${escapeHtml(tf?.description || "")}</p>
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
  if (!state.scanCatalog) state.scanCatalog = FALLBACK_CATALOG;
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
