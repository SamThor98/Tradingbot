/**
 * Three sentences for the Today morning note.
 * Sentence 1 is whether scans are allowed. Sentence 2 names the first
 * rendered row. Sentence 3 is the first skip-risk on that row, or why
 * a clean name is the one to open. No scores and no second ticker.
 */

function clean(value) {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  return text === "—" ? "" : text;
}

function allowance({ killSwitch, schwabDisconnected, scanBlocked }) {
  if (killSwitch) return "Scans are blocked: trading halt.";
  if (schwabDisconnected) return "Scans are blocked: Schwab is not connected.";
  if (scanBlocked) return "Scans are blocked: regime.";
  return "Scans are allowed.";
}

function skipRisk(row = {}) {
  const dq = clean(row._data_quality || row.data_quality).toLowerCase();
  if (["degraded", "stale", "fail", "failed", "error"].includes(dq)) {
    return `Skip it: data quality is ${dq}.`;
  }
  if (row.used_fallback_data) return "Skip it: this row is using fallback market data.";
  const status = clean(row._filter_status || "kept").toLowerCase();
  if (status && status !== "kept") {
    return `Skip it: scan disposition is ${status.replace(/[_-]+/g, " ")}.`;
  }
  const eventRisk = row.event_risk && typeof row.event_risk === "object" ? row.event_risk : null;
  if (eventRisk?.flagged) return "Skip it: event risk is flagged.";
  const sec = clean(row.sec_risk_tag).toLowerCase();
  if (sec === "high" || sec === "medium") {
    return `Skip it if you will not hold through a ${sec} SEC-risk tag.`;
  }
  const flags = Array.isArray(row.forensic_flags) ? row.forensic_flags.map((flag) => String(flag)) : [];
  if (flags.includes("beneish_manipulator")) return "Skip it: forensic Beneish manipulator flag.";
  if (flags.includes("altman_distress")) return "Skip it: forensic Altman distress zone.";
  if (flags.includes("sloan_high")) return "Skip it: forensic high Sloan accrual ratio.";
  if (row.breakout_confirmed === false) return "Skip it: breakout is not confirmed on the latest bar.";
  const confidence = clean(
    (row.advisory || {}).confidence_bucket || row.confidence_bucket || row.advisory_confidence,
  ).toLowerCase();
  if (!confidence || confidence === "unknown") return "Skip it: advisory confidence is unknown.";
  if (confidence === "low") return "Skip it: advisory confidence is low.";
  return "";
}

function cleanReason(row = {}, strategyLabel = "") {
  const strategy = clean(strategyLabel);
  const named = strategy && !["unknown", "n/a"].includes(strategy.toLowerCase());
  const sma = Number(row.sma_200);
  const price = Number(row.price ?? row.current_price);
  let stopBit = "stop is not on this row";
  if (Number.isFinite(sma) && sma > 0) stopBit = "stop is 3% under the 200-day";
  else if (Number.isFinite(price) && price > 0) stopBit = "stop is 7% under price, 200-day missing";
  if (named) return `Clean: ${strategy}, ${stopBit}.`;
  return `Clean: ${stopBit}.`;
}

export function morningNoteCopy({
  killSwitch = false,
  schwabDisconnected = false,
  scanBlocked = false,
  row = null,
  strategyLabel = "",
} = {}) {
  const allow = allowance({ killSwitch, schwabDisconnected, scanBlocked });
  const ticker = clean(row?.ticker || row?.symbol).toUpperCase();
  if (!row || !ticker) {
    return {
      allow,
      name: "No name to open yet.",
      risk: "Run a scan.",
    };
  }
  return {
    allow,
    name: `Open ${ticker} first.`,
    risk: skipRisk(row) || cleanReason(row, strategyLabel),
  };
}
