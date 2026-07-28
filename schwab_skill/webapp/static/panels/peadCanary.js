/**
 * PEAD canary sleeve — paper/diagnostics top-N beside Stage2 executables.
 *
 * Derives from last-scan diagnostics (`pead_primary_shadow_names` +
 * `capacity_top_n`), matching `core/pead_primary_shadow_compare.build_pead_canary_sleeve_block`.
 * Never implies PEAD-only orders — see docs/PEAD_CANARY_SLEEVE_DESIGN.md.
 */

import { safeText, safeNum, escapeHtml } from "../modules/format.js";

const DEFAULT_TOP_N = 5;

/**
 * @param {object} diag scan diagnostics from last_scan / /api/scan
 * @returns {{executable:boolean, rank_arm:string, rank_top_n:number, n_names:number, names:object[], tickers:string[], mode:string, evaluated:number, admitted:number, overlap:number, data_quality:string, truncated:number}}
 */
export function buildPeadCanarySleeve(diag = {}) {
  const d = diag && typeof diag === "object" ? diag : {};
  const mode = String(d.strategy_pead_primary_effective_mode || d.strategy_pead_primary_mode || "off")
    .trim()
    .toLowerCase();
  const topN = Math.max(1, safeNum(d.pead_primary_capacity_rank_top_n, DEFAULT_TOP_N) || DEFAULT_TOP_N);
  const raw = Array.isArray(d.pead_primary_shadow_names) ? d.pead_primary_shadow_names : [];
  const dicts = raw.filter((n) => n && typeof n === "object");
  let capacity = dicts.filter((n) => Boolean(n.capacity_top_n));
  if (!capacity.length && dicts.length) {
    capacity = dicts.slice(0, topN);
  } else {
    capacity = capacity.slice(0, topN);
  }
  const names = capacity.map((n) => ({
    ticker: String(n.ticker || "").toUpperCase(),
    edge_score: n.edge_score,
    stage_a_score: n.stage_a_score,
    pead_beat: n.pead_beat,
    pead_surprise_pct: n.pead_surprise_pct,
    executable: false,
  }));
  return {
    executable: false,
    rank_arm: safeText(d.pead_primary_capacity_rank_arm || "top5_by_edge_score"),
    rank_top_n: topN,
    n_names: names.length,
    names,
    tickers: names.map((n) => n.ticker).filter(Boolean),
    mode,
    evaluated: safeNum(d.pead_primary_evaluated, 0),
    admitted: safeNum(d.pead_primary_admitted, 0),
    overlap: safeNum(d.overlap_with_stage2, 0),
    data_quality: safeText(d.data_quality || "").toLowerCase(),
    truncated: safeNum(d.pead_primary_shadow_truncated, 0),
  };
}

function fmtEdge(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(1) : "—";
}

function fmtSurprise(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `${n >= 0 ? "+" : ""}${n.toFixed(1)}%` : "—";
}

/**
 * Paint the PEAD canary sleeve card from scan diagnostics.
 * @param {object} diag
 */
export function renderPeadCanarySleeve(diag = {}) {
  const root = document.getElementById("peadCanarySection");
  const body = document.getElementById("peadCanaryBody");
  const meta = document.getElementById("peadCanaryMeta");
  if (!root || !body) return;

  const sleeve = buildPeadCanarySleeve(diag);
  root.classList.remove("hidden");

  if (sleeve.mode === "off" && sleeve.evaluated <= 0 && !sleeve.n_names) {
    if (meta) meta.textContent = "PEAD canary off — set STRATEGY_PEAD_PRIMARY_MODE=shadow";
    body.innerHTML = `<p class="muted small">Paper sleeve inactive. Enable PEAD-primary shadow in <code>.env</code> (ALLOW_LIVE stays false) and run a scan to see the top-${DEFAULT_TOP_N}.</p>`;
    root.dataset.state = "off";
    return;
  }

  const dq = sleeve.data_quality || "—";
  const parts = [
    "paper only · not executable",
    sleeve.rank_arm,
    `top ${sleeve.rank_top_n}`,
    `eval ${sleeve.evaluated}`,
    `admit ${sleeve.admitted}`,
    `overlap ${sleeve.overlap}`,
    `dq ${dq || "—"}`,
  ];
  if (sleeve.truncated > 0) parts.push(`truncated ${sleeve.truncated}`);
  if (meta) meta.textContent = parts.join(" · ");

  if (!sleeve.n_names) {
    body.innerHTML = `<p class="muted small">No PEAD capacity top-${sleeve.rank_top_n} yet on the last scan. Run a dual-admit scan during RTH (earnings warm on) to populate the sleeve.</p>`;
    root.dataset.state = "empty";
    return;
  }

  const rows = sleeve.names
    .map((n, i) => {
      const beat =
        n.pead_beat === true ? '<span class="pill good">beat</span>' : n.pead_beat === false ? '<span class="pill warn">miss</span>' : '<span class="muted">—</span>';
      return `<tr>
        <td class="mono-nums muted">${i + 1}</td>
        <td class="mono-nums intel-ticker">${escapeHtml(n.ticker)}</td>
        <td class="num mono-nums">${escapeHtml(fmtEdge(n.edge_score))}</td>
        <td class="ctr">${beat}</td>
        <td class="num mono-nums">${escapeHtml(fmtSurprise(n.pead_surprise_pct))}</td>
        <td class="ctr"><span class="pill neutral">paper</span></td>
      </tr>`;
    })
    .join("");

  body.innerHTML = `<div class="table-wrap pead-canary-table-wrap">
    <table>
      <thead>
        <tr>
          <th class="ctr">#</th>
          <th>Ticker</th>
          <th class="num">Edge</th>
          <th class="ctr">Beat</th>
          <th class="num">Surprise</th>
          <th class="ctr">Status</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
  <p class="muted small pead-canary-footnote">Canary sleeve = PEAD-primary capacity top-${sleeve.rank_top_n} by edge score. Stage2 remains the only executable entry family.</p>`;
  root.dataset.state = "ready";
}
