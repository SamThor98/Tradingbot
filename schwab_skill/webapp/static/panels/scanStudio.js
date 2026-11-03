/**
 * Scan studio: timeframe grouping, strategy descriptions, and universe picker.
 *
 * The live engine is still daily Stage 2 + VCP. This panel lets operators
 * choose a horizon, read what each sleeve does, and scan a universe other
 * than S&P 1500. Weekly/monthly resample the same daily bars (Friday week /
 * month-end). Session-structure sleeves are not a Scan studio horizon.
 */

import { state, SCAN_STUDIO_PREFS_KEY, SCAN_STUDIO_PREFS_V2_KEY, LEGACY_SCAN_STUDIO_PREFS_KEY } from "../modules/state.js";
import { api } from "../modules/api.js";
import { escapeHtml, safeText } from "../modules/format.js";

function _fb(id, display_name, timeframe, status, origin, description, extra = {}) {
  const profile = FALLBACK_RETURN_PROFILES[id];
  return {
    id,
    display_name,
    timeframe,
    status,
    runnable: true,
    origin,
    description,
    ...(profile ? { return_profile: profile } : {}),
    ...extra,
  };
}

const FALLBACK_RETURN_PROFILES = Object.freeze({
  breakout_confirm: {
    style: "Overlay confirm",
    hold: "Same session as the daily setup — does not change the multi-week Stage 2 hold.",
    payoff: "Cuts false breaks; it is a filter, not a separate P&L stream.",
    edge: "Live-quote confirmation that the breakout is trading through.",
  },
  gap_and_go: {
    style: "Gap continuation",
    hold: "Same day to a few sessions. Daily-bar proxy — not a 1-minute book.",
    payoff: "Held gaps can run; faded gaps give back quickly. Fill quality is unknown on daily bars.",
    edge: "Opening gap that holds in an uptrend, with volume confirmation.",
  },
  range_expansion: {
    style: "Range expansion",
    hold: "Days, not minutes — this is a completed daily bar, not an opening-drive tape.",
    payoff: "Wide-range closes can continue; failed expansions reverse the next session.",
    edge: "True-range expansion through the prior high in an uptrend.",
  },
  opening_range_breakout: {
    style: "High-RVOL proxy",
    hold: "The paper is same-day 5-minute ORB. This screen is a completed daily bar.",
    payoff: "Literature: trend days in 'stocks in play'. This proxy is not that book.",
    edge: "Relative volume + strong close — not an opening-range entry.",
  },
  trend_breakout: {
    style: "Momentum breakout",
    hold: "Multi-week. Live time-stop is about 40 sessions; trail/partials wait ~15 days.",
    payoff: "Right-skew: many small stops vs fewer trend runners. Not a day-trade.",
    edge: "Stage 2 + VCP continuation. This is the only live order-routing sleeve.",
  },
  pullback: {
    style: "Trend pullback",
    hold: "Days to a few weeks if the 50-day bounce holds.",
    payoff: "Tighter than a fresh breakout; failed 50-day tags become quick losers.",
    edge: "Buy strength on a dip in a Stage 2 trend — not a falling-knife catch.",
  },
  pead_primary: {
    style: "Post-event drift",
    hold: "Days after the print. Canary only — this sleeve does not own live entries.",
    payoff: "Slow drift after a surprise; the next print can gap against you.",
    edge: "PEAD literature. Stage 2 still owns executable entries here.",
  },
  donchian_20: {
    style: "Channel breakout",
    hold: "Weeks while price holds the new-high regime.",
    payoff: "Turtle-style skew: frequent whipsaws, occasional long trends.",
    edge: "20-day high plus a 200-day filter. Shadow only.",
  },
  nr7_breakout: {
    style: "Compression breakout",
    hold: "Short swing after the range expands.",
    payoff: "A few expansions run; many NR7s go nowhere or fake out.",
    edge: "Narrowest 7-day range, then a close through that high.",
  },
  st_reversal_5d: {
    style: "Short-term mean reversion",
    hold: "About one week on the paper portfolios. This is a single-name screen.",
    payoff: "Small bounce vs leftover downtrend. Costs eat the microcap versions.",
    edge: "Loser rebound after −8% in five days, with a $2M ADV floor.",
  },
  overnight_gap_fade: {
    style: "Gap recovery label",
    hold: "The paper edge is overnight. This only labels a completed recovery.",
    payoff: "Not a tradable open fade in this implementation.",
    edge: "Gap-down that recovered ≥50% by the close — diagnostic, not an entry.",
  },
  weekly_swing: {
    style: "Weekly momentum",
    hold: "Multi-week to a few months on Friday bars.",
    payoff: "Same right-skew idea as daily Stage 2, slower and fewer trades.",
    edge: "Weekly 10w > 30w rising, near 52-week highs. Research — not live.",
  },
  weekly_vcp: {
    style: "Volume dry-up",
    hold: "Weeks after dryness, if the next expansion is up.",
    payoff: "Quiet volume that resolves higher can run; a breakdown after dryness fails fast.",
    edge: "Five quiet weeks above the 30-week SMA — not Minervini range contraction.",
  },
  weekly_breakout: {
    style: "Weekly breakout",
    hold: "Weeks while the weekly close holds the new high.",
    payoff: "Weekly whipsaws vs slower trend continuation.",
    edge: "Weekly close through last week's high, above the 30-week SMA.",
  },
  weekly_reversal: {
    style: "Weekly mean reversion",
    hold: "About one to two weeks on completed Friday bars.",
    payoff: "Short bounce after a down week; strong downtrends keep falling.",
    edge: "Prior week ≤ −6%, this week turns up. Single-name screen, not a loser portfolio.",
  },
  monthly_position: {
    style: "Trend timing",
    hold: "Months — in above a rising 10-month SMA, out below it.",
    payoff: "Captures long bull legs and sits out deep bears; lags at turns.",
    edge: "Faber-style month-end close above a rising 10-month SMA. Single-name analog, not the ETF GTAA book.",
  },
  monthly_52w_high: {
    style: "Position momentum",
    hold: "Months while the name stays near highs.",
    payoff: "Strength can persist; late-cycle highs round over.",
    edge: "Month-end close within 5% of the 52-week high and above the 10-month SMA.",
  },
  monthly_pullback: {
    style: "Position pullback",
    hold: "Months if the 10-month bounce holds.",
    payoff: "Better entry than chasing highs; failed SMA tags continue down.",
    edge: "Rising 10-month SMA, this month tagged it, close still holds above.",
  },
  momentum_12_1: {
    style: "12-1 momentum",
    hold: "Paper WML holds ~1 month. This is a single-name strength screen, then top-10 of hits.",
    payoff: "Literature: winners keep winning after skipping last month. Not a WML book here.",
    edge: "12-month return ≥20% skipping last month, above the 200-day SMA.",
  },
  tsmom_12m: {
    style: "Time-series trend",
    hold: "Months on a 12-month lookback.",
    payoff: "Paper TSMOM is vol-scaled futures. This is a single-name analog, not crisis alpha.",
    edge: "Month-end close above the close 12 months earlier.",
  },
});

const FALLBACK_CATALOG = Object.freeze({
  timeframes: [
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
  let timeframe = safeText(parsed.timeframe || base.timeframe).toLowerCase() || base.timeframe;
  let droppedHorizon = false;
  if (!timeframeRow(timeframe)) {
    timeframe = base.timeframe || "daily";
    droppedHorizon = true;
  }
  const universe_preset =
    safeText(parsed.universe_preset || base.universe_preset).toLowerCase() || base.universe_preset;
  let ids = Array.isArray(parsed.strategy_ids)
    ? parsed.strategy_ids.map((s) => String(s || "").trim().toLowerCase()).filter(Boolean)
    : [...base.strategy_ids];
  if (resetStrategyIds || droppedHorizon) {
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
    const rawV3 = localStorage.getItem(SCAN_STUDIO_PREFS_KEY);
    if (rawV3) {
      const parsed = JSON.parse(rawV3);
      if (!parsed || typeof parsed !== "object") return { ...base, strategy_ids: [...base.strategy_ids] };
      return prefsFromParsed(parsed, base);
    }
    // v2/v1 leftover monthly/paper checks must not become the boot default.
    const stale = localStorage.getItem(SCAN_STUDIO_PREFS_V2_KEY) || localStorage.getItem(LEGACY_SCAN_STUDIO_PREFS_KEY);
    if (stale) {
      const own = { ...base, strategy_ids: [...base.strategy_ids] };
      saveScanStudioPrefs(own);
      return own;
    }
    return { ...base, strategy_ids: [...base.strategy_ids] };
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

function countSelectedInTimeframe(prefs, timeframe) {
  const ids = new Set((prefs?.strategy_ids || []).map((id) => String(id)));
  return strategiesForTimeframe(timeframe).filter((row) => ids.has(String(row.id || ""))).length;
}

function selectedStrategyIds(prefs) {
  const known = new Set(
    (Array.isArray(catalog().strategies) ? catalog().strategies : []).map((row) => String(row.id || "")),
  );
  const out = [];
  const seen = new Set();
  for (const raw of prefs?.strategy_ids || []) {
    const id = String(raw || "").trim();
    if (!id || seen.has(id)) continue;
    if (known.size && !known.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

function timeframeForSelectedIds(prefs, ids) {
  const tfs = new Set();
  const rows = Array.isArray(catalog().strategies) ? catalog().strategies : [];
  const byId = new Map(rows.map((row) => [String(row.id || ""), row]));
  for (const id of ids || []) {
    const tf = safeText(byId.get(String(id))?.timeframe || "").toLowerCase();
    if (tf) tfs.add(tf);
  }
  if (tfs.size === 1) return [...tfs][0];
  return String(prefs?.timeframe || "daily");
}

function selectedStrategyLabels(prefs, ids) {
  const rows = Array.isArray(catalog().strategies) ? catalog().strategies : [];
  const byId = new Map(rows.map((row) => [String(row.id || ""), row]));
  return (ids || []).map((id) => {
    const row = byId.get(String(id));
    return row?.display_name || id;
  });
}

export function readScanStudioBody() {
  if (typeof document !== "undefined" && document.getElementById?.("scanStudioPanel")) {
    try {
      syncPrefsFromDom();
    } catch {
      // Tests and first paint may not have a bound panel.
    }
  }
  const prefs = state.scanStudioPrefs || loadScanStudioPrefs();
  const strategyIds = selectedStrategyIds(prefs);
  const body = {
    scan_timeframe: timeframeForSelectedIds(prefs, strategyIds),
    universe_preset: prefs.universe_preset || "sp1500",
    strategy_ids: strategyIds.length ? strategyIds : [],
  };
  if (!body.strategy_ids.length) {
    return { error: "Select at least one strategy." };
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
      const selectedN = countSelectedInTimeframe(prefs, tf.id);
      const count = selectedN
        ? `<span class="scan-studio-tf-count">${selectedN} selected</span>`
        : "";
      return `<button type="button" class="scan-studio-tf${active ? " is-active" : ""}" role="tab" id="scanTf-${escapeHtml(tf.id)}" data-scan-timeframe="${escapeHtml(tf.id)}" aria-controls="scanHorizon-${escapeHtml(tf.id)}" aria-selected="${active ? "true" : "false"}" aria-pressed="${active ? "true" : "false"}"${active ? ' aria-current="true"' : ""}>${escapeHtml(tf.display_name)}${count}</button>`;
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

function returnProfileBlock(row) {
  const rp = row?.return_profile && typeof row.return_profile === "object" ? row.return_profile : null;
  if (!rp) return "";
  const hold = safeText(rp.hold || "");
  const payoff = safeText(rp.payoff || "");
  const edge = safeText(rp.edge || "");
  if (!hold && !payoff && !edge) return "";
  const rows = [
    hold ? `<div><dt>Hold</dt><dd>${escapeHtml(hold)}</dd></div>` : "",
    payoff ? `<div><dt>Payoff</dt><dd>${escapeHtml(payoff)}</dd></div>` : "",
    edge ? `<div><dt>Edge</dt><dd>${escapeHtml(edge)}</dd></div>` : "",
  ]
    .filter(Boolean)
    .join("");
  return `<dl class="scan-studio-profile" aria-label="Return profile">${rows}</dl>`;
}

function styleChip(style) {
  const label = safeText(style || "");
  if (!label) return "";
  return `<span class="scan-studio-style-chip">${escapeHtml(label)}</span>`;
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
    id === primaryId ? `<span class="scan-studio-primary-chip">Primary for this horizon</span>` : "";
  const how =
    status === "live"
      ? "Included in live order routing."
      : status === "live_overlay"
        ? "Confirms the live book with a quote overlay."
        : status === "shadow"
          ? "Runs in this scan as a filter. Does not go live."
          : "Paper / research screen. Opt-in filter only.";
  return `<label class="scan-studio-strategy ${mods}">
      <input class="scan-studio-strategy-input" type="checkbox" data-scan-strategy="${escapeHtml(id)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
      <span class="scan-studio-strategy-copy">
        <span class="scan-studio-strategy-head">
          <strong>${escapeHtml(row.display_name || id)}</strong>
          <span class="scan-studio-selected-flag">Selected</span>
          ${primaryChip}
          ${styleChip(row.return_profile?.style)}
          ${statusChip(row.status)}
          ${originChip(row.origin)}
        </span>
        <span class="scan-studio-strategy-desc">${escapeHtml(row.description || "")}</span>
        <span class="muted small scan-studio-strategy-how">${escapeHtml(how)}${disabled ? " Unavailable right now." : ""}</span>
        ${returnProfileBlock(row)}
        ${evidenceLine(row.evidence)}
      </span>
    </label>`;
}

function renderStrategyGroup(title, hint, rows, prefs, primaryId, extraClass) {
  if (!rows.length) return "";
  return `<fieldset class="scan-studio-group${extraClass ? ` ${extraClass}` : ""}">
      <legend class="scan-studio-group-head">
        <span class="scan-studio-kicker">${escapeHtml(title)}</span>
        ${hint ? `<span class="muted small scan-studio-group-hint">${escapeHtml(hint)}</span>` : ""}
      </legend>
      <div class="scan-studio-strategies">${rows.map((row) => renderStrategyCard(row, prefs, primaryId)).join("")}</div>
    </fieldset>`;
}

function renderHorizonBoard(tf, prefs) {
  const tfId = String(tf.id || "");
  const rows = strategiesForTimeframe(tfId);
  if (!rows.length) return "";
  const primaryId = primaryStrategyIdForTimeframe(tfId);
  const yours = rows.filter((row) => !isPaperStrategy(row));
  const paper = rows.filter(isPaperStrategy);
  yours.sort((a, b) => {
    if (String(a.id) === primaryId) return -1;
    if (String(b.id) === primaryId) return 1;
    return 0;
  });
  const active = tfId === prefs.timeframe;
  const selectedN = countSelectedInTimeframe(prefs, tfId);
  const selectedLabel = selectedN ? `${selectedN} selected` : "None selected";
  const groups = `${renderStrategyGroup("Your strategies", "Check a row to include it in Run scan for this horizon.", yours, prefs, primaryId, "")}
      ${renderStrategyGroup("Paper screens", "Literature screens — opt-in only, not the live book.", paper, prefs, primaryId, "scan-studio-group--paper")}`;
  if (active) {
    return `<section class="scan-studio-horizon is-active" id="scanHorizon-${escapeHtml(tfId)}" data-scan-horizon="${escapeHtml(tfId)}" role="tabpanel" aria-labelledby="scanTf-${escapeHtml(tfId)}">
      <div class="scan-studio-horizon-head">
        <h4 class="scan-studio-horizon-title">${escapeHtml(tf.display_name || tfId)} strategies</h4>
        <p class="muted small scan-studio-horizon-desc">${escapeHtml(tf.description || "")} <span data-scan-horizon-selected>${escapeHtml(selectedLabel)}</span> in this horizon.</p>
      </div>
      ${groups}
    </section>`;
  }
  return `<details class="scan-studio-horizon" id="scanHorizon-${escapeHtml(tfId)}" data-scan-horizon="${escapeHtml(tfId)}">
      <summary class="scan-studio-horizon-summary">
        <span class="scan-studio-horizon-summary-title">${escapeHtml(tf.display_name || tfId)} · ${rows.length} strategies</span>
        <span class="scan-studio-horizon-sel" data-scan-horizon-selected>${escapeHtml(selectedLabel)}</span>
      </summary>
      <p class="muted small scan-studio-horizon-desc">${escapeHtml(tf.description || "")} Open this group to pre-check strategies for the next time you pick this horizon.</p>
      ${groups}
    </details>`;
}

function renderStrategyCards(prefs) {
  const frames = Array.isArray(catalog().timeframes) ? catalog().timeframes : [];
  const boards = frames.map((tf) => renderHorizonBoard(tf, prefs)).filter(Boolean);
  if (boards.length) return boards.join("");
  const rows = strategiesForTimeframe(prefs.timeframe);
  if (!rows.length) {
    return `<p class="muted small">No strategies in this timeframe yet.</p>`;
  }
  return renderHorizonBoard({ id: prefs.timeframe, display_name: prefs.timeframe }, prefs);
}

function renderUniverseOptions(prefs) {
  const rows = Array.isArray(catalog().universes) ? catalog().universes : [];
  return rows
    .filter((u) => u.available !== false)
    .map((u) => {
      const selected = u.id === prefs.universe_preset;
      const title = u.description || "";
      return `<label class="scan-studio-uni${selected ? " is-selected" : ""}">
          <input type="radio" name="scanUniverse" value="${escapeHtml(u.id)}" ${selected ? "checked" : ""} />
          <span class="scan-studio-uni-name">${escapeHtml(u.display_name)}</span>
          <span class="scan-studio-uni-desc">${escapeHtml(title)}</span>
        </label>`;
    })
    .join("");
}

function catalogLoadNote(prefs) {
  const err = safeText(state.scanCatalogError || "");
  if (err) {
    return `<p class="scan-studio-note" role="status">Catalog refresh failed (${escapeHtml(err)}). Showing the bundled strategy list — hard-refresh if this stays stale.</p>`;
  }
  const total = Array.isArray(catalog().strategies) ? catalog().strategies.length : 0;
  if (total) {
    return `<p class="muted small scan-studio-tf-desc">${total} ${total === 1 ? "strategy is" : "strategies are"} available. The horizon tab focuses that group; other horizons stay collapsed with a selected count.</p>`;
  }
  return "";
}

function horizonNote(prefs) {
  const tf = String(prefs.timeframe || "daily");
  if (tf === "weekly" || tf === "monthly") {
    const note = catalog().notes?.weekly_monthly || "Weekly/monthly resample daily bars (Friday week / month-end). Research only — not a live book.";
    return `<p class="scan-studio-note" role="status">${escapeHtml(note)}</p>`;
  }
  return "";
}

function selectedHorizonLabels(prefs, ids) {
  const wanted = new Set(ids || []);
  const labels = [];
  const frames = Array.isArray(catalog().timeframes) ? catalog().timeframes : [];
  for (const tf of frames) {
    const hit = strategiesForTimeframe(tf.id).some((row) => wanted.has(String(row.id || "")));
    if (hit) labels.push(tf.display_name || tf.id);
  }
  return labels;
}

function renderSetupSummary(prefs) {
  const ids = selectedStrategyIds(prefs);
  const labels = selectedStrategyLabels(prefs, ids);
  const horizons = selectedHorizonLabels(prefs, ids);
  let uniDetail = universeRow(prefs.universe_preset)?.display_name || "S&P 1500";
  if (prefs.universe_preset === "custom") {
    const n = parseTickerText(prefs.tickersText).length;
    uniDetail = n ? `Custom (${n} ticker${n === 1 ? "" : "s"})` : "Custom — add tickers";
  }
  const strategyText = labels.length ? labels.join(", ") : "None selected";
  const horizonText = horizons.length ? horizons.join(" + ") : timeframeRow(prefs.timeframe)?.display_name || "Daily";
  return `
    <aside class="scan-studio-setup" id="scanStudioSetupSummary" aria-live="polite">
      <p class="scan-studio-kicker">This scan will use</p>
      <dl class="scan-studio-setup-dl">
        <div class="scan-studio-setup-item">
          <dt>Universe</dt>
          <dd>${escapeHtml(uniDetail)}</dd>
        </div>
        <div class="scan-studio-setup-item">
          <dt>Horizon</dt>
          <dd>${escapeHtml(horizonText)}</dd>
        </div>
        <div class="scan-studio-setup-item">
          <dt>Strategies</dt>
          <dd>${escapeHtml(strategyText)}</dd>
        </div>
      </dl>
    </aside>
  `;
}

function renderStatusLegend() {
  return `<ul class="scan-studio-legend" aria-label="Strategy status meanings">
      <li><span class="scan-studio-status scan-studio-status--live">Live</span> executable book</li>
      <li><span class="scan-studio-status scan-studio-status--live_overlay">Overlay</span> quote confirm</li>
      <li><span class="scan-studio-status scan-studio-status--shadow">Shadow</span> runs, not live</li>
      <li><span class="scan-studio-status scan-studio-status--research">Research</span> paper screen</li>
    </ul>`;
}

export function scanStudioMarkup(prefs) {
  const uni = universeRow(prefs.universe_preset);
  const tf = timeframeRow(prefs.timeframe);
  const custom = prefs.universe_preset === "custom";
  return `
    <div class="scan-studio-head">
      <p class="scan-studio-kicker workspace-eyebrow">Scan setup</p>
      <h3 class="scan-studio-title">Scan studio</h3>
      <p class="muted small scan-studio-lede">Run Scan uses every checked sleeve, including Daily, Weekly, and Monthly together. Unchecked rows are skipped. A weekly or PEAD-only scan is not a leftover of Stage 2 / VCP. Live orders still route through Daily Stage 2 / VCP.</p>
    </div>
    ${renderSetupSummary(prefs)}
    <div class="scan-studio-steps" aria-label="Scan setup">
      <div class="scan-studio-step">
        <div class="scan-studio-step-head visually-hidden">
          <h4 class="scan-studio-step-title" id="scanUniverseStepTitle">Universe</h4>
          <p class="muted small" id="scanUniverseHow">Which names to scan. The highlighted card is the current universe.</p>
        </div>
        <div class="scan-studio-universe-row" role="radiogroup" aria-labelledby="scanUniverseStepTitle" aria-describedby="scanUniverseHow">${renderUniverseOptions(prefs)}</div>
        <p class="muted small" id="scanUniverseHint">${escapeHtml(uni?.description || "")}</p>
        <label class="scan-studio-field${custom ? "" : " hidden"}" id="scanCustomTickersWrap" for="scanCustomTickers">
          <span class="scan-studio-kicker">Custom tickers</span>
          <input id="scanCustomTickers" type="text" placeholder="AAPL, MSFT, NVDA" value="${escapeHtml(prefs.tickersText || "")}" aria-describedby="scanCustomTickersHint" />
          <span class="muted small" id="scanCustomTickersHint">Separate symbols with spaces or commas.</span>
        </label>
      </div>
      <div class="scan-studio-step">
        <div class="scan-studio-step-head visually-hidden">
          <h4 class="scan-studio-step-title" id="scanHorizonStepTitle">Horizon</h4>
          <p class="muted small" id="scanHorizonHow">Focuses the strategy list. Checked sleeves on other horizons still run. A count appears on a tab when that horizon already has strategies checked.</p>
        </div>
        <div class="scan-studio-tf-row" role="tablist" aria-labelledby="scanHorizonStepTitle">${renderTimeframeTabs(prefs)}</div>
        <p class="muted small scan-studio-tf-desc">${escapeHtml(tf?.description || "")}</p>
        ${catalogLoadNote(prefs)}
        ${horizonNote(prefs)}
      </div>
      <div class="scan-studio-step">
        <div class="scan-studio-step-head visually-hidden">
          <h4 class="scan-studio-step-title" id="scanStrategyStepTitle">Strategies</h4>
          <p class="muted small" id="scanStrategyHow">Check every strategy to include in Run scan. Unchecked rows are skipped. Shadow and Research still filter results; they do not place live orders.</p>
        </div>
        ${renderStatusLegend()}
        <div class="scan-studio-board" aria-labelledby="scanStrategyStepTitle">${renderStrategyCards(prefs)}</div>
      </div>
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
  const root = document.getElementById("scanStudioPanel");
  const tfBtn = root?.querySelector(".scan-studio-tf.is-active");
  if (tfBtn?.dataset.scanTimeframe) prefs.timeframe = tfBtn.dataset.scanTimeframe;
  const uniRadio = root?.querySelector('[name="scanUniverse"]:checked');
  if (uniRadio?.value) prefs.universe_preset = uniRadio.value;
  const tickers = document.getElementById("scanCustomTickers");
  if (tickers) prefs.tickersText = tickers.value;
  const boxes = (root || document).querySelectorAll("[data-scan-strategy]");
  if (boxes.length) {
    prefs.strategy_ids = [...boxes]
      .filter((el) => el.checked && !el.disabled)
      .map((el) => String(el.getAttribute("data-scan-strategy") || ""));
  }
  saveScanStudioPrefs(prefs);
  return prefs;
}

function paintSelectionChrome() {
  const prefs = state.scanStudioPrefs || loadScanStudioPrefs();
  const summary = document.getElementById("scanStudioSetupSummary");
  if (summary) {
    const wrap = document.createElement("div");
    wrap.innerHTML = renderSetupSummary(prefs);
    const next = wrap.firstElementChild;
    if (next) summary.replaceWith(next);
  }
  document.querySelectorAll(".scan-studio-strategy").forEach((label) => {
    const input = label.querySelector("[data-scan-strategy]");
    label.classList.toggle("is-selected", Boolean(input?.checked && !input.disabled));
  });
  document.querySelectorAll("[data-scan-horizon]").forEach((el) => {
    const tfId = el.getAttribute("data-scan-horizon");
    const n = countSelectedInTimeframe(prefs, tfId);
    const badge = el.querySelector("[data-scan-horizon-selected]");
    if (badge) badge.textContent = n ? `${n} selected` : "None selected";
  });
  document.querySelectorAll("[data-scan-timeframe]").forEach((btn) => {
    const tfId = btn.getAttribute("data-scan-timeframe");
    const tf = timeframeRow(tfId);
    const n = countSelectedInTimeframe(prefs, tfId);
    const name = tf?.display_name || tfId || "";
    const countHtml = n ? ` <span class="scan-studio-tf-count">${n} selected</span>` : "";
    btn.innerHTML = `${escapeHtml(name)}${countHtml}`;
  });
  const uniHint = document.getElementById("scanUniverseHint");
  const uni = universeRow(prefs.universe_preset);
  if (uniHint) uniHint.textContent = uni?.description || "";
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
  const inTf = new Set(strategiesForTimeframe(timeframe).map((row) => String(row.id || "")));
  const kept = (prefs.strategy_ids || []).filter((id) => inTf.has(String(id)));
  if (!kept.length) {
    const primary = primaryStrategyIdForTimeframe(timeframe);
    const others = (prefs.strategy_ids || []).filter((id) => !inTf.has(String(id)));
    prefs.strategy_ids = primary ? [...others, primary] : others;
  }
  saveScanStudioPrefs(prefs);
  renderScanStudio();
  document.getElementById(`scanTf-${timeframe}`)?.focus();
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
    if (t.name === "scanUniverse") {
      syncPrefsFromDom();
      renderScanStudio();
      return;
    }
    if (t.id === "scanCustomTickers" || t.hasAttribute("data-scan-strategy")) {
      syncPrefsFromDom();
      paintSelectionChrome();
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
