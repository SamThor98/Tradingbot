/**
 * Trade-ticket facts for the decision drawer and the pending blotter.
 *
 * R is |entry − stop| × quantity from numbers already on the row or the
 * decision card. Equity percent uses a loaded account value. Open risk is
 * the sum of the same R on held positions that already carry a stop. Missing
 * inputs stay blank. This module does not size, rank, or send orders.
 */

import { formatFilterReasons } from "./filterReasons.js";
import { isScanSignalStageable } from "./signalProvenance.js";
import { stageHoldReasons } from "./stageEvidence.js";

const CLEAR_STATUS = "clear to stage";

function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function firstNum(...values) {
  for (const value of values) {
    const n = num(value);
    if (n !== null) return n;
  }
  return null;
}

function clean(value) {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  if (!text || text === "—") return "";
  return text;
}

function round2(value) {
  return Math.round(value * 100) / 100;
}

/**
 * Same advisory stop the decision card already publishes:
 * 3% under the 200-day average when that average exists, otherwise 7% under price.
 */
export function advisoryStop(price, sma200) {
  const sma = num(sma200);
  const px = num(price);
  if (sma !== null && sma > 0) return round2(sma * 0.97);
  if (px !== null && px > 0) return round2(px * 0.93);
  return null;
}

export function riskDollars(entry, stop, qty) {
  const e = num(entry);
  const s = num(stop);
  const q = num(qty);
  if (e === null || s === null || q === null || q <= 0) return null;
  return round2(Math.abs(e - s) * q);
}

/** Percent of equity. `rDollars` and `equity` are dollars. */
export function riskPctOfEquity(rDollars, equity) {
  const r = num(rDollars);
  const eq = num(equity);
  if (r === null || eq === null || eq <= 0) return null;
  return (r / eq) * 100;
}

/**
 * Dollar risk already open. Counts a position only when that position
 * carries its own stop. Returns null when no such position is present.
 */
export function openRiskDollars(positions) {
  if (!Array.isArray(positions) || !positions.length) return null;
  let sum = 0;
  let any = false;
  for (const pos of positions) {
    const stop = num(pos?.stop ?? pos?.stop_invalidation);
    const entry = num(pos?.avg_cost ?? pos?.last ?? pos?.price);
    const qty = num(pos?.qty);
    if (stop === null || entry === null || qty === null || qty === 0) continue;
    sum += Math.abs(entry - stop) * Math.abs(qty);
    any = true;
  }
  return any ? round2(sum) : null;
}

export function bookHoldsSector({ sector, weightPct, positions } = {}) {
  const weight = num(weightPct);
  if (weight !== null) return weight > 0 ? "Yes" : "No";
  const wanted = clean(sector).toUpperCase();
  if (!wanted || !Array.isArray(positions) || !positions.length) return "";
  const labeled = positions.filter((pos) => clean(pos?.sector_etf || pos?.sector));
  if (!labeled.length) return "";
  const held = labeled.some((pos) => {
    const etf = clean(pos?.sector_etf).toUpperCase();
    const name = clean(pos?.sector).toUpperCase();
    return etf === wanted || name === wanted;
  });
  return held ? "Yes" : "No";
}

export function guardrailText(checklist, signal) {
  const plain = checklist?.block_reasons_plain;
  if (Array.isArray(plain)) {
    const first = clean(plain[0]);
    if (first) return first;
  }
  const direct = clean(signal?.guardrail_error || signal?.execution_guardrail);
  if (direct) return direct;
  const codes = checklist?.block_reasons;
  if (Array.isArray(codes)) {
    const code = clean(codes[0]);
    if (code) return code;
  }
  return "";
}

export function systemStageBlockers(snapshot = {}) {
  const blockers = [];
  if (snapshot.killSwitch) {
    blockers.push("Platform kill switch is active — new risk-increasing orders are blocked.");
  }
  if (snapshot.tradingHalted) {
    blockers.push("Trading pause is on — clear it in Settings before approving live orders.");
  }
  if (snapshot.liveOff) {
    blockers.push("Live trading is off — enable in Strategy Presets after reviewing risk.");
  }
  return blockers;
}

export function rowStageBlockers(row) {
  const blockers = [];
  const sig = row?.signal || {};
  if (Object.keys(sig).length && !isScanSignalStageable(sig)) {
    const reasons = formatFilterReasons(sig._filter_reasons);
    blockers.push(
      clean(reasons[0]) ||
        "Filtered at scan time — re-stage from a tradeable scan row before approving.",
    );
  }
  const holds = stageHoldReasons(sig);
  if (holds.length) blockers.push(clean(holds[0]));
  const eventRisk = sig.event_risk;
  if (
    eventRisk &&
    eventRisk.mode === "live" &&
    eventRisk.flagged &&
    eventRisk.action === "block"
  ) {
    const bits = Array.isArray(eventRisk.reasons)
      ? eventRisk.reasons.map((item) => clean(item)).filter(Boolean).join(", ")
      : "";
    blockers.push(bits || "event_risk_block");
  }
  return blockers.filter(Boolean);
}

/**
 * One status string. Blockers win, then the guardrail error, then a
 * data_quality value other than ok. Otherwise the row is clear to stage.
 */
export function ticketStatus({
  guardrail = "",
  dataQuality = "",
  blockers = [],
  systemBlockers = [],
} = {}) {
  for (const blocker of systemBlockers) {
    const text = clean(blocker);
    if (text) return { clear: false, status: text };
  }
  const guard = clean(guardrail);
  if (guard) return { clear: false, status: guard };
  for (const blocker of blockers) {
    const text = clean(blocker);
    if (text) return { clear: false, status: text };
  }
  const dq = clean(dataQuality);
  if (dq && dq.toLowerCase() !== "ok") return { clear: false, status: dq };
  return { clear: true, status: CLEAR_STATUS };
}

export function formatHeat(rPct, openPct) {
  const left = rPct === null ? "—" : `${rPct.toFixed(2)}%`;
  const right = openPct === null ? "—" : `${openPct.toFixed(2)}%`;
  return `${left} · open ${right}`;
}

export function buildTicketModel({
  card = null,
  row = null,
  equity = null,
  positions = null,
  blockers = [],
  systemBlockers = [],
} = {}) {
  const signal = row?.signal && typeof row.signal === "object" ? row.signal : {};
  const checklist = card?.checklist && typeof card.checklist === "object" ? card.checklist : {};
  const qty = firstNum(row?.qty, card?.size?.qty);
  const limit = firstNum(row?.price, card?.entry_price, signal.price, signal.current_price);
  const stop = firstNum(
    card?.stop_invalidation,
    signal.stop_invalidation,
    signal.stop,
    advisoryStop(limit, signal.sma_200),
  );
  const side = clean(row?.side || card?.side || signal.side || signal.order_side) || "BUY";
  const sector = clean(signal.sector_etf || checklist.portfolio_sector_etf);
  const dataQuality = clean(card?.data_quality || signal._data_quality || signal.data_quality);
  const guardrail = guardrailText(checklist, signal);
  const accountEquity = firstNum(checklist.portfolio_total_value_usd, equity);
  const r = riskDollars(limit, stop, qty);
  const rPct = riskPctOfEquity(r, accountEquity);
  const openR = openRiskDollars(positions);
  const openPct = riskPctOfEquity(openR, accountEquity);
  const book = bookHoldsSector({
    sector,
    weightPct: checklist.portfolio_sector_weight_pct,
    positions,
  });
  const status = ticketStatus({ guardrail, dataQuality, blockers, systemBlockers });
  const rFormula =
    limit !== null && stop !== null && qty !== null
      ? `${limit.toFixed(2)} - ${stop.toFixed(2)} x ${qty}`
      : "";
  return {
    side,
    qty,
    limit,
    stop,
    r,
    rPct,
    openPct,
    heat: formatHeat(rPct, openPct),
    sector,
    book,
    dataQuality,
    guardrail,
    rFormula,
    ...status,
  };
}
