/**
 * Plain-English backtest labels. Machine theory_name / strategy_ids stay as stored.
 * Server stamps display_label; this module is the optimistic-UI fallback.
 */

import { humanizeKey } from "../modules/humanize.js";

export const KNOB_LABELS = {
  stage2_52w_pct: "Near 52-week high",
  stage2_sma_upward_days: "200 SMA rising days",
  vcp_days: "VCP quiet days",
  quality_min_signal_score: "Min signal score",
  sma_days: "SMA days",
  quality_breakout_volume_min_ratio: "Breakout volume vs 50-day avg",
  quality_require_breakout_volume: "Require breakout volume",
  scan_vcp_gate_mode: "VCP gate",
  quality_gates_mode: "Quality gates",
};

const HIDDEN_KNOBS = new Set(["skip_mirofish", "forensic_enabled", "forensic_filter_mode"]);

export const STATUS_LABELS = {
  queued: "Waiting",
  running: "Running",
  success: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export const EXIT_REASON_LABELS = {
  trailing_stop: "Trailing stop",
  exits_trailing_stop: "Trailing stop",
  time_exit: "Time exit",
  exits_time_exit: "Time exit",
  sma50_break: "50-day SMA break",
  exits_sma50_break: "50-day SMA break",
  vcp_invalidation: "VCP invalidation",
  exits_vcp_invalidation: "VCP invalidation",
  final_liquidation: "End-of-window liquidation",
  exits_final_liquidation: "End-of-window liquidation",
};

const WAVE_RE = /^w(\d+)$/i;
const YEARS_RE = /^(\d+)y$/i;
const CAMPAIGN_RE = /^(?:wknd\s+)?w\d+\b/i;
const PRESET_THEORY_RE = /^last\s+\d+\s+years?$/i;
export const LARGE_N_TRADES = 100;

function catalogRows(catalog, key) {
  const rows = catalog && Array.isArray(catalog[key]) ? catalog[key] : [];
  return rows;
}

export function humanizeToken(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  if (KNOB_LABELS[text]) return KNOB_LABELS[text];
  if (STATUS_LABELS[text]) return STATUS_LABELS[text];
  if (EXIT_REASON_LABELS[text]) return EXIT_REASON_LABELS[text];
  return humanizeKey(text.replace(/-/g, "_"));
}

export function statusLabel(status) {
  const key = String(status || "").trim().toLowerCase();
  return STATUS_LABELS[key] || humanizeToken(key) || "Unknown";
}

export function exitReasonLabel(reason) {
  const key = String(reason || "").trim();
  if (!key) return "";
  return EXIT_REASON_LABELS[key] || EXIT_REASON_LABELS[key.toLowerCase()] || humanizeToken(key);
}

export function universeDisplayName(universeId, catalog) {
  const key = String(universeId || "").trim().toLowerCase();
  if (!key || key === "unknown" || key === "watchlist") return "";
  if (key === "tickers" || key === "custom") return "Custom tickers";
  const row = catalogRows(catalog, "universes").find((u) => String(u.id) === key);
  if (row?.display_name) return String(row.display_name);
  return humanizeToken(key);
}

export function strategyDisplayName(strategyId, catalog) {
  const key = String(strategyId || "").trim().toLowerCase();
  if (!key) return "";
  const rows = catalogRows(catalog, "strategies");
  const direct = rows.find((s) => String(s.id) === key);
  if (direct?.display_name) return String(direct.display_name);
  const aliased = rows.find((s) => Array.isArray(s.match_ids) && s.match_ids.map(String).includes(key));
  if (aliased?.display_name) return String(aliased.display_name);
  return humanizeToken(key);
}

function formatKnobValue(value) {
  if (value === true) return "";
  if (value === false) return "off";
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "");
  }
  const text = String(value ?? "").trim();
  const lower = text.toLowerCase();
  if (["true", "on", "1"].includes(lower)) return "";
  if (["false", "off", "0"].includes(lower)) return "off";
  return text;
}

export function formatKnob(key, value) {
  const name = String(key || "").trim();
  if (!name || HIDDEN_KNOBS.has(name)) return "";
  const label = KNOB_LABELS[name] || humanizeToken(name);
  const pretty = formatKnobValue(value);
  return pretty ? `${label} ${pretty}` : label;
}

export function windowYearsFromSpec(spec) {
  const payload = spec && typeof spec === "object" ? spec : {};
  const theory = String(payload.theory_name || "");
  const yearMatch = theory.match(/\b(\d+)y\b/i);
  if (yearMatch) {
    const n = Number(yearMatch[1]);
    if (Number.isFinite(n) && n > 0) return n;
  }
  const start = String(payload.start_date || "").slice(0, 10);
  const end = String(payload.end_date || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) return null;
  const startMs = Date.parse(`${start}T00:00:00Z`);
  const endMs = Date.parse(`${end}T00:00:00Z`);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return null;
  const days = (endMs - startMs) / 86400000;
  return Math.max(1, Math.round(days / 365.25));
}

export function windowLabel(years) {
  if (years == null || !Number.isFinite(Number(years))) return "";
  const n = Number(years);
  return n === 1 ? "1 year" : `${n} years`;
}

function parseCampaignTokens(theory, catalog) {
  const rest = String(theory || "").replace(/^wknd\s+/i, "").trim();
  let wave = null;
  let universeId = "";
  const strategyIds = [];
  let years = null;
  const knobs = [];
  const universeIds = new Set(catalogRows(catalog, "universes").map((u) => String(u.id)));
  const strategyIdsKnown = new Set(catalogRows(catalog, "strategies").map((s) => String(s.id)));
  for (const bit of rest.split(/\s+/).filter(Boolean)) {
    const waveMatch = bit.match(WAVE_RE);
    if (waveMatch) {
      wave = Number(waveMatch[1]);
      continue;
    }
    const yearsMatch = bit.match(YEARS_RE);
    if (yearsMatch) {
      years = Number(yearsMatch[1]);
      continue;
    }
    if (bit.includes("=")) {
      const idx = bit.indexOf("=");
      knobs.push([bit.slice(0, idx), bit.slice(idx + 1)]);
      continue;
    }
    if (bit.includes("+")) {
      strategyIds.push(...bit.split("+").map((p) => p.trim()).filter(Boolean));
      continue;
    }
    const lower = bit.toLowerCase();
    if (universeIds.has(lower) || ["sp1500", "sp500", "nasdaq100", "sector_etfs", "focused", "custom"].includes(lower)) {
      universeId = lower;
      continue;
    }
    if (strategyIdsKnown.has(lower)) {
      strategyIds.push(lower);
      continue;
    }
    if (!universeId) universeId = lower;
    else strategyIds.push(lower);
  }
  return { wave, universeId, strategyIds, years, knobs };
}

function stripUnderscores(text) {
  return String(text || "").replace(/_/g, " ").trim();
}

export function formatBacktestLabel(spec, catalog) {
  const payload = spec && typeof spec === "object" ? spec : {};
  const theory = String(payload.theory_name || "").trim();
  let universeId = String(payload.universe_preset || payload.universe_mode || "").trim();
  let strategyIds = Array.isArray(payload.strategy_ids)
    ? payload.strategy_ids.map((s) => String(s).trim()).filter(Boolean)
    : [];
  let years = windowYearsFromSpec(payload);
  let knobs = [];
  let wave = null;
  const isCampaign = CAMPAIGN_RE.test(theory);

  if (theory && isCampaign) {
    const parsed = parseCampaignTokens(theory, catalog);
    wave = parsed.wave;
    if (parsed.universeId) universeId = parsed.universeId;
    if (parsed.strategyIds.length) strategyIds = parsed.strategyIds;
    if (parsed.years) years = parsed.years;
    knobs = parsed.knobs.map(([k, v]) => formatKnob(k, v)).filter(Boolean);
  } else if (payload.overrides && typeof payload.overrides === "object") {
    knobs = Object.entries(payload.overrides)
      .map(([k, v]) => formatKnob(k, v))
      .filter(Boolean);
  }

  let universeName = universeDisplayName(universeId, catalog);
  if (payload.universe_mode === "tickers" || universeId === "custom" || universeId === "tickers") {
    const n = Array.isArray(payload.tickers) ? payload.tickers.length : 0;
    universeName = n ? `Custom tickers (${n})` : "Custom tickers";
  }
  const strategyNames = strategyIds.map((id) => strategyDisplayName(id, catalog)).filter(Boolean);
  const yearsText = windowLabel(years);

  const titleBits = [];
  if (wave != null && Number.isFinite(wave)) titleBits.push(`Wave ${wave}`);
  if (universeName) titleBits.push(universeName);
  if (strategyNames.length) titleBits.push(strategyNames.join(" + "));
  if (yearsText) titleBits.push(yearsText);

  let title;
  const isPreset = PRESET_THEORY_RE.test(theory);
  if (titleBits.length) {
    title = titleBits.join(" · ");
    if (theory && !isCampaign && !isPreset && !theory.includes("_") && !knobs.includes(theory)) {
      knobs = [theory, ...knobs];
    }
  } else if (theory && !isPreset && !isCampaign && !theory.includes("_")) {
    title = theory;
  } else if (theory) {
    title = humanizeToken(theory.replace(/^wknd\s+/i, "").trim()) || "Backtest";
  } else {
    title = "Backtest";
  }

  return {
    title: stripUnderscores(title),
    subtitle: stripUnderscores(knobs.join(" · ")),
    parts: {
      wave,
      universe: universeName,
      universe_id: universeId,
      strategies: strategyNames,
      strategy_ids: strategyIds,
      window_years: years,
      window_label: yearsText,
      knobs,
    },
  };
}

export function runDisplayTitle(run, catalog) {
  const stamped = String(run?.display_label || "").trim();
  if (stamped) return stripUnderscores(stamped);
  return formatBacktestLabel(run?.spec, catalog).title;
}

export function runDisplaySubtitle(run, catalog) {
  if (run?.display_subtitle) return stripUnderscores(run.display_subtitle);
  return formatBacktestLabel(run?.spec, catalog).subtitle;
}

export function runStatusLabel(run) {
  if (run?.status_label) return run.status_label;
  return statusLabel(run?.status);
}

export function filterLeaderboardRows(rows, windowYears, extras = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const filters = extras && typeof extras === "object" && !Array.isArray(extras) ? extras : {};
  const wantWindow = Number(windowYears);
  const universe = String(filters.universeId || "").trim().toLowerCase();
  const minTrades = Number(filters.minTrades);
  return list.filter((row) => {
    if (Number.isFinite(wantWindow) && wantWindow > 0 && Number(row?.window_years) !== wantWindow) return false;
    if (universe && universe !== "all" && String(row?.universe_id || "").toLowerCase() !== universe) return false;
    if (Number.isFinite(minTrades) && minTrades > 0 && Number(row?.total_trades || 0) < minTrades) return false;
    return true;
  });
}
