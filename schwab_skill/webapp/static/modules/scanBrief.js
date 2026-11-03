/**
 * Local decision-brief builder for Evaluate setup.
 *
 * The Today-tab brief dropdowns should stay populated from the scan row even
 * when `/api/decision-card` is slow or misses a hydrated last_scan ticker.
 * Local and remote brief lines are unioned so a sparse decision-card
 * payload cannot blank a richer scan-row disclosure.
 */

import { formatFilterReasons } from "./filterReasons.js";
import { formatMoney, formatStrategyLabel } from "./format.js";
import {
  formatConfidenceLabel,
  getCalibratedPUp,
  getCompositeScore,
  getConvictionScore,
  optionalNum,
} from "./signalScores.js";

function rawText(value) {
  if (value === null || value === undefined) return "";
  const txt = String(value).trim();
  return txt === "—" ? "" : txt;
}

function foldLine(txt) {
  return String(txt || "")
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

const RISK_FILLER =
  /no hard risk blockers|no forensic flags|were not attached|not attached beyond|sec risk tag: unknown/i;

const GLANCE_SKIP =
  /primary strategy signal:|scan horizon:|sector etf:|sector relative-strength rank:/i;

/** Lines that change the call. Default "nothing here" notes stay in the full list. */
export function materialBriefLines(items) {
  const out = [];
  for (const raw of items || []) {
    const txt = rawText(raw);
    if (!txt || RISK_FILLER.test(txt)) continue;
    out.push(txt);
  }
  return out;
}

/** Catalyst lines worth the glance row. Horizon and strategy labels stay in full notes. */
export function glanceLines(items, limit = 2) {
  const out = [];
  for (const raw of items || []) {
    const txt = rawText(raw);
    if (!txt || GLANCE_SKIP.test(txt)) continue;
    out.push(txt);
    if (out.length >= limit) break;
  }
  return out;
}

function trimLines(items, limit = 8) {
  const out = [];
  const seen = new Set();
  for (const raw of items || []) {
    const txt = rawText(raw);
    if (!txt) continue;
    const key = foldLine(txt);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(txt);
    if (out.length >= limit) break;
  }
  return out;
}

function asPercentPoints(value) {
  const n = optionalNum(value);
  if (n === null) return null;
  // Scanner fields here are fractions, including surprises larger than 100%.
  return n * 100;
}

function volumeRatio(row = {}) {
  const direct =
    optionalNum(row.volume_ratio) ??
    optionalNum(row.rel_volume) ??
    optionalNum(row.rvol) ??
    optionalNum(row.vol_ratio);
  if (direct !== null) return direct;
  const latest = optionalNum(row.latest_volume ?? row.volume);
  const avg = optionalNum(row.avg_vol_50 ?? row.avg_volume_50 ?? row.average_volume);
  if (latest === null || avg === null || avg <= 0) return null;
  return latest / avg;
}

function strategyLabel(row = {}) {
  return formatStrategyLabel((row.strategy_attribution || {}).top_live || "unknown");
}

function mergeLists(local, remote, key, limit = 8) {
  const left = Array.isArray(local?.[key]) ? local[key] : [];
  const right = Array.isArray(remote?.[key]) ? remote[key] : [];
  return trimLines([...left, ...right], limit);
}

function pickRicherText(...values) {
  const texts = values.map((v) => rawText(v)).filter(Boolean);
  if (!texts.length) return "";
  return texts.reduce((best, cur) => (cur.length > best.length ? cur : best));
}

export function mergeScanBriefs(local, remote) {
  const base = local && typeof local === "object" ? local : {};
  const over = remote && typeof remote === "object" ? remote : {};
  const setup = pickRicherText(over.setup_summary, base.setup_summary);
  const expected =
    pickRicherText(over.expected_move_window, base.expected_move_window) || "10 trading days";
  return {
    setup_summary: setup,
    key_risks: mergeLists(base, over, "key_risks"),
    catalyst_notes: mergeLists(base, over, "catalyst_notes"),
    forensic_flags: mergeLists(base, over, "forensic_flags"),
    sec_notes: mergeLists(base, over, "sec_notes"),
    expected_move_window: expected,
    entry_stop_ideas: mergeLists(base, over, "entry_stop_ideas", 8),
  };
}

export function buildLocalScanBrief(row = {}) {
  const ticker = rawText(row.ticker || row.symbol || "?").toUpperCase();
  const score = getCompositeScore(row);
  const confidence = formatConfidenceLabel(
    (row.advisory || {}).confidence_bucket ?? row.confidence_bucket ?? row.advisory_confidence,
  );
  const strategy = strategyLabel(row);
  const vol = volumeRatio(row);
  const volBit = vol === null ? "" : `, relative volume ${vol.toFixed(2)}x`;
  const setup_summary =
    `${ticker} remains a breakout candidate with score ${score === null ? "—" : score.toFixed(1)}, ` +
    `${confidence === "—" ? "unknown" : confidence.toLowerCase()} confidence, and strategy ${
      strategy === "—" ? "n/a" : strategy
    }${volBit}.`;

  const key_risks = [];
  const filterStatus = rawText(row._filter_status || "kept");
  if (filterStatus && filterStatus !== "kept") {
    key_risks.push(`Scan disposition: ${filterStatus.replace(/[_-]+/g, " ")}.`);
  }
  for (const reason of formatFilterReasons(row._filter_reasons)) {
    key_risks.push(`Filter: ${reason}.`);
  }
  const secTag = rawText(row.sec_risk_tag || "unknown").toLowerCase();
  if (secTag === "high" || secTag === "medium") key_risks.push(`SEC risk tag: ${secTag}.`);
  const confRaw = rawText((row.advisory || {}).confidence_bucket || row.confidence_bucket || "").toLowerCase();
  if (confRaw === "low" || confRaw === "unknown" || confidence === "—") {
    key_risks.push("Advisory confidence is low/unknown.");
  }
  const eventRisk = row.event_risk && typeof row.event_risk === "object" ? row.event_risk : {};
  if (eventRisk.flagged) {
    const bits = Array.isArray(eventRisk.reasons) ? eventRisk.reasons.filter(Boolean).join(", ") : "";
    key_risks.push(`Event risk flagged${bits ? `: ${bits}` : ""}.`);
  }
  if (row.used_fallback_data) {
    const why = rawText(row.fallback_reason);
    key_risks.push(`Using fallback market data${why ? ` (${why})` : ""}.`);
  }
  const dq = rawText(row._data_quality || row.data_quality).toLowerCase();
  if (["degraded", "fail", "failed", "error", "stale"].includes(dq)) {
    key_risks.push(`Data quality is ${dq}.`);
  }
  if (row.recent_8k) key_risks.push("Recent 8-K filing in the lookback window.");
  const flags = Array.isArray(row.forensic_flags) ? row.forensic_flags.map((x) => String(x)) : [];
  if (flags.includes("beneish_manipulator")) key_risks.push("Forensic: Beneish manipulator flag.");
  if (flags.includes("altman_distress")) key_risks.push("Forensic: Altman distress zone.");
  if (flags.includes("sloan_high")) key_risks.push("Forensic: high Sloan accrual ratio.");
  if (!key_risks.length) key_risks.push("No hard risk blockers on this scan row.");

  const catalyst_notes = [];
  const peadPts = asPercentPoints(row.pead_surprise_pct);
  if (peadPts !== null) {
    if (row.pead_beat === true) catalyst_notes.push(`Positive earnings surprise: ${peadPts.toFixed(1)}%.`);
    else if (row.pead_beat === false) catalyst_notes.push(`Negative earnings surprise: ${peadPts.toFixed(1)}%.`);
    else catalyst_notes.push(`Recent earnings surprise: ${peadPts.toFixed(1)}%.`);
  } else if (row.pead_beat === true) {
    catalyst_notes.push("Recent earnings beat (surprise size not attached).");
  } else if (row.pead_beat === false) {
    catalyst_notes.push("Recent earnings miss (surprise size not attached).");
  }
  const guidance = rawText(row.guidance_signal).toLowerCase();
  if (guidance && !["neutral", "unknown", "none"].includes(guidance)) {
    catalyst_notes.push(`Filing guidance signal: ${guidance}.`);
  }
  if (row.breakout_confirmed === true) catalyst_notes.push("Breakout confirmed on the latest bar.");
  else if (row.breakout_confirmed === false) catalyst_notes.push("Breakout is not confirmed on the latest bar.");
  if (vol !== null) catalyst_notes.push(`Relative volume vs 50-day average: ${vol.toFixed(2)}x.`);
  const sector = rawText(row.sector_etf);
  if (sector) catalyst_notes.push(`Sector ETF: ${sector}.`);
  const rs = row.sector_rs_rank ?? row.sector_relative_strength_rank;
  if (rs !== null && rs !== undefined && rawText(rs)) {
    catalyst_notes.push(`Sector relative-strength rank: ${rs}.`);
  }
  const tf = rawText(row.strategy_attribution?.timeframe || row.scan_timeframe || row.timeframe);
  if (tf) catalyst_notes.push(`Scan horizon: ${tf}.`);
  catalyst_notes.push(`Primary strategy signal: ${strategy === "—" ? "unknown" : strategy}.`);

  const forensic_flags = flags.length ? [...flags] : ["No forensic flags on this scan row."];
  const sloan = optionalNum(row.forensic_sloan);
  const beneish = optionalNum(row.forensic_beneish);
  const altman = optionalNum(row.forensic_altman);
  if (sloan !== null) forensic_flags.push(`Sloan accrual ratio: ${sloan.toFixed(3)}.`);
  if (beneish !== null) forensic_flags.push(`Beneish M-score: ${beneish.toFixed(3)}.`);
  if (altman !== null) forensic_flags.push(`Altman Z-score: ${altman.toFixed(2)}.`);
  if (sloan === null && beneish === null && altman === null) {
    forensic_flags.push("Sloan / Beneish / Altman ratios were not attached to this scan row.");
  }

  const sec_notes = [`SEC risk tag: ${secTag || "unknown"}.`];
  const recency = optionalNum(row.filing_recency_days);
  if (recency !== null) sec_notes.push(`Most recent filing context age: ${Math.round(recency)} day(s).`);
  if (row.recent_8k) sec_notes.push("Recent 8-K is present in the lookback window.");
  for (const reason of row.sec_risk_reasons || []) {
    const txt = rawText(reason);
    if (txt) sec_notes.push(txt.endsWith(".") ? txt : `${txt}.`);
  }
  if (guidance && !["neutral", "unknown", "none", ""].includes(guidance)) {
    sec_notes.push(`Guidance from latest 10-Q compare: ${guidance}.`);
  }
  if (secTag === "unknown" && recency === null && !row.recent_8k && !(row.sec_risk_reasons || []).length) {
    sec_notes.push("SEC enrichment was not attached beyond the default unknown tag.");
  }

  const pUp = getCalibratedPUp(row);
  const conviction = getConvictionScore(row);
  const expectedBits = ["10 trading days"];
  if (pUp !== null) expectedBits.push(`P(up) ${(pUp * 100).toFixed(1)}%`);
  if (conviction !== null) expectedBits.push(`conviction ${conviction.toFixed(1)}`);

  const price = optionalNum(row.price ?? row.current_price);
  const sma200 = optionalNum(row.sma_200);
  const sma50 = optionalNum(row.sma_50);
  const entry_stop_ideas = [];
  const smaStop = sma200 && sma200 > 0 ? sma200 * 0.97 : null;
  const bandStop = price !== null && price > 0 ? price * 0.93 : null;
  if (smaStop !== null) {
    entry_stop_ideas.push(`Stop / invalidation: ${smaStop.toFixed(2)} — 3% under the 200-day.`);
  } else if (bandStop !== null) {
    entry_stop_ideas.push(`Stop / invalidation: ${bandStop.toFixed(2)} — 7% under price, 200-day missing.`);
  } else {
    entry_stop_ideas.push("Stop / invalidation not computed (no price or SMA-200).");
  }
  if (price !== null && price > 0) entry_stop_ideas.push(`Last scan price: ${formatMoney(price)}.`);
  else entry_stop_ideas.push("Last scan price unavailable — scan row has no price.");
  if (sma50 && sma50 > 0) entry_stop_ideas.push(`50-day SMA: ${sma50.toFixed(2)}.`);
  if (sma200 && sma200 > 0) entry_stop_ideas.push(`200-day SMA: ${sma200.toFixed(2)}.`);
  const shadow = row.entry_timing_shadow || row.entry_timing_at_stage2 || {};
  const buf = asPercentPoints(shadow.breakout_buffer_pct);
  if (buf !== null) entry_stop_ideas.push(`Breakout buffer vs prior high: ${buf.toFixed(1)}%.`);

  return {
    setup_summary,
    key_risks: trimLines(key_risks),
    catalyst_notes: trimLines(catalyst_notes),
    forensic_flags: trimLines(forensic_flags),
    sec_notes: trimLines(sec_notes),
    expected_move_window: expectedBits.join(" · "),
    entry_stop_ideas: trimLines(entry_stop_ideas, 8),
  };
}
