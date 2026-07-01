/**
 * Calibration panel visuals — KPI tiles, reliability bars, ledger pills, summary strip.
 * Matches Figma frame "Calibration — Success" (node 5:2) in Old Logan DS.
 */

import { escapeHtml, safeNum, safeText, timeAgo } from "../modules/format.js";

const DEFAULT_HIT_GATE = 0.5;

function gateClass(passed) {
  if (passed === true) return "good";
  if (passed === false) return "bad";
  return "neutral";
}

function fmtPctFraction(value, digits = 1) {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "—";
}

function fmtWinRate(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const pct = n <= 1 ? n * 100 : n;
  return `${pct.toFixed(1)}%`;
}

function fmtSignedPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function topReliabilitySource(calibration = {}) {
  const bySource = calibration?.by_source;
  if (!bySource || typeof bySource !== "object") return null;
  let best = null;
  for (const [source, stats] of Object.entries(bySource)) {
    const samples = safeNum(stats?.scored_samples, 0);
    const hitRate = safeNum(stats?.hit_rate, NaN);
    if (samples <= 0 || !Number.isFinite(hitRate)) continue;
    if (!best || samples > best.samples) {
      best = { source, hitRate, samples };
    }
  }
  return best;
}

/**
 * @param {HTMLElement|null} container
 * @param {object} data
 */
export function renderCalibrationSummaryStrip(container, data = {}) {
  if (!container) return;
  if (data.empty) {
    container.textContent = "No calibration snapshot";
    return;
  }
  const ss = data.self_study || {};
  const hc = data.hypothesis_calibration || ss.hypothesis_calibration || null;
  const top = topReliabilitySource(hc);
  const trips = safeNum(ss.round_trips_count ?? ss.round_trips, 0);
  const parts = [];
  if (top) {
    parts.push(`${safeText(top.source).replace(/_/g, " ")} ${fmtPctFraction(top.hitRate)}`);
    parts.push(`${Math.round(top.samples)} samples`);
  }
  if (trips > 0) {
    parts.push(`${Math.round(trips)} round trips`);
  }
  container.textContent = parts.length ? parts.join(" · ") : "Calibration loaded";
}

/**
 * @param {HTMLElement|null} container
 * @param {object} selfStudy
 */
export function renderCalibrationKpiTiles(container, selfStudy = {}) {
  if (!container) return;
  const conviction = selfStudy.suggested_min_conviction;
  const trips = selfStudy.round_trips_count ?? selfStudy.round_trips;
  const winRate = selfStudy.win_rate;
  const avgReturn = selfStudy.avg_return_pct;

  const tiles = [
    {
      label: "Min conviction",
      value: conviction != null ? String(safeNum(conviction, 0)) : "—",
      sub: "suggested threshold",
      state: conviction != null ? true : null,
    },
    {
      label: "Round trips",
      value: trips != null ? String(Math.round(safeNum(trips, 0))) : "—",
      sub: "closed positions",
      state: trips != null && safeNum(trips, 0) > 0 ? true : null,
    },
    {
      label: "Win rate",
      value: winRate != null ? fmtWinRate(winRate) : "—",
      sub: "self-study cohort",
      state:
        winRate != null
          ? (Number(winRate) <= 1 ? Number(winRate) : Number(winRate) / 100) >= 0.5
          : null,
    },
    {
      label: "Avg return",
      value: avgReturn != null ? fmtSignedPct(avgReturn) : "—",
      sub: "per round trip",
      state: avgReturn != null ? Number(avgReturn) >= 0 : null,
    },
  ];

  container.innerHTML = tiles
    .map(
      (tile) => `
    <div class="decision-gate-tile calibration-kpi-tile decision-gate-tile--${gateClass(tile.state)}">
      <span class="decision-gate-label">${escapeHtml(tile.label)}</span>
      <strong class="decision-gate-value mono-nums">${escapeHtml(tile.value)}</strong>
      <small class="decision-gate-sub muted">${escapeHtml(tile.sub)}</small>
    </div>
  `,
    )
    .join("");
}

/**
 * @param {HTMLElement|null} container
 * @param {object} ledger
 */
export function renderCalibrationLedgerSources(container, ledger = {}) {
  if (!container) return;
  const counts = ledger.recent_source_counts;
  const rowCount = safeNum(ledger.row_count, 0);
  if (!counts || typeof counts !== "object" || !Object.keys(counts).length) {
    container.innerHTML = `<p class="muted">No hypothesis ledger rows yet.</p>`;
    return;
  }
  const pills = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(
      ([source, n]) => `
      <span class="calibration-ledger-pill">
        <span class="calibration-ledger-pill-label">${escapeHtml(source.replace(/_/g, " "))}</span>
        <strong class="calibration-ledger-pill-count mono-nums">${Math.round(Number(n) || 0)}</strong>
      </span>
    `,
    )
    .join("");
  const truncated = ledger.truncated ? " · recent window only" : "";
  container.innerHTML = `
    <div class="calibration-ledger-sources">${pills}</div>
    <p class="muted calibration-ledger-meta">${rowCount} total hypotheses${truncated}</p>
  `;
}

/**
 * @param {HTMLElement|null} container
 * @param {object} calibration
 */
export function renderCalibrationReliabilityDiagram(container, calibration = {}) {
  if (!container) return;
  const bySource = calibration?.by_source;
  if (!bySource || typeof bySource !== "object" || !Object.keys(bySource).length) {
    container.innerHTML = `<p class="muted">Run hypothesis outcome scoring to populate reliability buckets.</p>`;
    return;
  }

  const rows = Object.entries(bySource)
    .map(([source, stats]) => ({
      source,
      hitRate: safeNum(stats?.hit_rate, NaN),
      samples: safeNum(stats?.scored_samples, 0),
      meanReturn: safeNum(stats?.mean_return_pct, NaN),
    }))
    .filter((row) => row.samples > 0)
    .sort((a, b) => b.samples - a.samples);

  if (!rows.length) {
    container.innerHTML = `<p class="muted">No scored hypothesis samples yet.</p>`;
    return;
  }

  const gateLeft = DEFAULT_HIT_GATE * 100;
  const body = rows
    .map((row) => {
      const pass = Number.isFinite(row.hitRate) ? row.hitRate >= DEFAULT_HIT_GATE : null;
      const width = Number.isFinite(row.hitRate) ? Math.min(100, row.hitRate * 100) : 0;
      return `
        <div class="calibration-reliability-row calibration-reliability-row--${pass === true ? "pass" : pass === false ? "fail" : "neutral"}">
          <div class="calibration-reliability-head">
            <strong>${escapeHtml(row.source.replace(/_/g, " "))}</strong>
            <span class="muted mono-nums">n=${Math.round(row.samples)}</span>
          </div>
          <div class="calibration-reliability-bar-row">
            <span class="calibration-reliability-label">Hit rate</span>
            <div class="calibration-reliability-track">
              <div class="calibration-reliability-fill" style="--calib-fill-pct:${width}%"></div>
              <div class="calibration-reliability-gate" style="--calib-gate-pct:${gateLeft}%"></div>
            </div>
            <span class="calibration-reliability-value mono-nums">${fmtPctFraction(row.hitRate)}</span>
          </div>
          ${
            Number.isFinite(row.meanReturn)
              ? `<div class="muted calibration-reliability-meta">Mean return ${row.meanReturn.toFixed(2)}%</div>`
              : ""
          }
        </div>
      `;
    })
    .join("");

  container.innerHTML = `
    <div class="calibration-reliability-chart">${body}</div>
    <p class="muted calibration-reliability-note">Reference line at 50% hit rate · ${rows.length} source bucket(s)</p>
  `;
}

/**
 * @param {HTMLElement|null} el
 * @param {object} data
 */
export function updateCalibrationFreshness(el, data = {}) {
  if (!el) return;
  const ss = data.self_study || {};
  const stamp = ss.last_run || ss.updated_at || "";
  if (stamp) {
    el.textContent = `Updated ${timeAgo(stamp)}`;
    el.setAttribute("data-freshness", "fresh");
  } else if (data.empty) {
    el.textContent = "No snapshot yet";
    el.setAttribute("data-freshness", "empty");
  } else {
    el.textContent = "Snapshot loaded";
    el.setAttribute("data-freshness", "unknown");
  }
}
