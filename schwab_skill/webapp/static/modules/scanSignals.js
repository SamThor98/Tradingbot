/**
 * Scan-signal shape normalization shared by the scan table, scan detail,
 * pending staging, and history hydration.
 *
 * Backend scan payloads vary by age and transport: nested `signal` objects,
 * JSON-string sub-objects, and DB rows with a `payload` column all appear in
 * the wild. These helpers flatten every variant into one predictable shape so
 * render code can read `row.advisory.confidence_bucket` etc. without guards.
 *
 * Extracted from app.js per the module decomposition policy in
 * docs/FRONTEND_DESIGN_SYSTEM.md.
 */

function asObject(value) {
  if (!value) return null;
  if (typeof value === "object" && !Array.isArray(value)) return value;
  if (typeof value !== "string") return null;
  const raw = value.trim();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function normalizeScanSignal(rawSignal) {
  const base = asObject(rawSignal) || {};
  const nested = asObject(base.signal) || {};
  const signal = { ...base, ...nested };
  signal.advisory = asObject(signal.advisory) || {};
  signal.mirofish_result = asObject(signal.mirofish_result) || {};
  signal.strategy_attribution = asObject(signal.strategy_attribution) || {};
  signal.prediction_market = asObject(signal.prediction_market) || {};
  return signal;
}

/** Hydrate a signal from a scan-results DB row (`{ticker, payload, ...}`). */
export function signalFromScanResultRow(row) {
  const rec = asObject(row) || {};
  const payload = asObject(rec.payload) || {};
  const signal = normalizeScanSignal(payload);
  if (!signal.ticker && rec.ticker) signal.ticker = rec.ticker;
  if (!signal.symbol && rec.ticker) signal.symbol = rec.ticker;
  if (signal.signal_score == null && rec.signal_score != null) signal.signal_score = rec.signal_score;
  if (!signal.job_id && rec.job_id) signal.job_id = rec.job_id;
  if (signal.flagged_days == null && rec.flagged_days != null) signal.flagged_days = rec.flagged_days;
  return signal;
}
