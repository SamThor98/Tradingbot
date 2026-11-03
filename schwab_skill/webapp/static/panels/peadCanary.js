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
    pead_surprise_large: n.pead_surprise_large === true,
    close_vs_sma200_pct: n.close_vs_sma200_pct,
    extended: n.pead_extension_drop_would_drop === true ? true : n.pead_extension_drop_would_drop === false ? false : null,
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
  if (!Number.isFinite(n)) return "—";
  // Scanner stores a fraction clamped to ±3. Values outside that are already percent points.
  const pct = Math.abs(n) <= 3 ? n * 100 : n;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function fmtPct(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `${n.toFixed(2)}%` : "—";
}

function fmtHit(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
}

const SPLIT_LABELS = {
  all: "All five",
  not_extended: "Not extended",
  high_surprise: "Surprise > 15%",
};

let forwardSnapshot = null;

function extensionCell(extended) {
  if (extended === true) return '<span class="pill warn">extended</span>';
  if (extended === false) return '<span class="pill good">inside</span>';
  return '<span class="muted">—</span>';
}

/**
 * Pre-registered T+5 slices. They do not change the top-5 membership.
 * @param {object|null|undefined} pead settled-lessons `pead_canary` payload
 */
export function renderPeadCanaryForward(pead) {
  forwardSnapshot = pead && typeof pead === "object" ? pead : null;
  paintPeadCanaryForward();
}

function paintPeadCanaryForward() {
  const host = document.getElementById("peadCanaryForward");
  if (!host) return;
  const pead = forwardSnapshot;
  const splits = pead && pead.splits && typeof pead.splits === "object" ? pead.splits : null;
  const slices = splits && splits.slices && typeof splits.slices === "object" ? splits.slices : null;
  if (!slices) {
    host.innerHTML = "";
    return;
  }
  const order = ["all", "not_extended", "high_surprise"];
  const rows = order
    .map((key) => {
      const row = slices[key] && typeof slices[key] === "object" ? slices[key] : {};
      return `<tr>
        <td>${escapeHtml(SPLIT_LABELS[key] || key)}</td>
        <td class="num mono-nums">${escapeHtml(String(row.n ?? 0))}</td>
        <td class="num mono-nums">${escapeHtml(fmtHit(row.hit_rate))}</td>
        <td class="num mono-nums">${escapeHtml(fmtPct(row.mean_return_pct))}</td>
      </tr>`;
    })
    .join("");
  const n = Number(pead.n ?? slices.all?.n ?? 0);
  const minN = Number(pead.min_n ?? 40);
  const extMissing = Number(splits.extension_unmeasured || 0);
  const surMissing = Number(splits.surprise_unmeasured || 0);
  const hold =
    n < minN
      ? `Forward sample is ${n} of ${minN} at T+${escapeHtml(String(pead.horizon || splits.horizon || "5"))}.`
      : `Forward sample has reached ${n} at T+${escapeHtml(String(pead.horizon || "5"))}.`;
  let missing = "";
  if (extMissing > 0 && extMissing === surMissing) {
    missing = ` ${extMissing} scored rows were saved before these flags and stay in All five only.`;
  } else if (extMissing > 0 || surMissing > 0) {
    const bits = [];
    if (extMissing > 0) bits.push(`${extMissing} without an extension flag`);
    if (surMissing > 0) bits.push(`${surMissing} without a surprise flag`);
    missing = ` ${bits.join(", ")}. Those rows stay in All five only.`;
  }
  host.innerHTML = `<div class="table-wrap pead-canary-table-wrap pead-canary-forward">
    <table>
      <thead>
        <tr>
          <th>T+${escapeHtml(String(splits.horizon || "5"))} slice</th>
          <th class="num">n</th>
          <th class="num">Hit rate</th>
          <th class="num">Mean return</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
  <p class="muted small pead-canary-footnote">${hold}${missing} Slices are recorded only. They do not change the top 5 or Stage 2 orders.</p>`;
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
    if (meta) meta.textContent = "Paper sleeve is off.";
    body.innerHTML = `<p class="muted small">Paper sleeve is off. Turn on PEAD shadow in System → Connect, then run a scan to see the top-${DEFAULT_TOP_N}.</p><div id="peadCanaryForward"></div>`;
    root.dataset.state = "off";
    paintPeadCanaryForward();
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
    body.innerHTML = `<p class="muted small">No PEAD capacity top-${sleeve.rank_top_n} yet on the last scan. Run a dual-admit scan during RTH (earnings warm on) to populate the sleeve.</p><div id="peadCanaryForward"></div>`;
    root.dataset.state = "empty";
    paintPeadCanaryForward();
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
        <td class="ctr">${extensionCell(n.extended)}</td>
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
          <th class="ctr">Extension</th>
          <th class="ctr">Status</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
  <div id="peadCanaryForward"></div>
  <p class="muted small pead-canary-footnote">Canary sleeve = PEAD-primary capacity top-${sleeve.rank_top_n} by edge score. Extension and surprise are recorded beside the name. Stage2 remains the only executable entry family.</p>`;
  root.dataset.state = "ready";
  paintPeadCanaryForward();
}
