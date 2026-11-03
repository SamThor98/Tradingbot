/**
 * Liquid positioning panel — funding / open interest for Liquid's xyz
 * perp markets (the same markets the Liquid plugin trades), collected
 * server-side from the public Hyperliquid info API.
 *
 * Research only, shadow layer: this does not change scan scores,
 * ranking, sizing, or execution. Schwab remains the broker and the
 * data source of record; this is perp positioning, not Schwab flow.
 * Markets under the OI floor are shown as no-signal, never as a skew.
 */

import { api } from "../modules/api.js";
import { escapeHtml } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import { setAsyncState, ASYNC_ERROR, ASYNC_LOADING, ASYNC_EMPTY, ASYNC_SUCCESS } from "../modules/asyncState.js";

const SECTION_ID = "liquidPositioningSection";
const BODY_ID = "liquidPositioningBody";

let _gen = 0;
let _loaded = false;

function body() {
  return document.getElementById(BODY_ID);
}

function fmtUsd(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return `$${n.toFixed(0)}`;
}

function fmtFunding(value) {
  // hourly funding rate (decimal) -> annualized % is the readable form
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const ann = n * 24 * 365 * 100;
  return `${ann >= 0 ? "+" : ""}${ann.toFixed(1)}%/yr`;
}

function fmtPx(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function kpiTile(label, value, sub = "") {
  return `<div class="event-risk-kpi">
    <span class="event-risk-kpi-label">${escapeHtml(label)}</span>
    <strong class="event-risk-kpi-value">${escapeHtml(value)}</strong>
    ${sub ? `<span class="event-risk-kpi-sub muted">${escapeHtml(sub)}</span>` : ""}
  </div>`;
}

function snapshotRow(s) {
  const thin = s.is_thin
    ? ` <span class="pill small">thin — no signal</span>`
    : "";
  return `<tr>
    <td><strong>${escapeHtml(s.ticker || "—")}</strong>${thin}
      <div class="muted event-risk-meta">${escapeHtml(s.source_coin || "")}</div></td>
    <td class="event-risk-num">${escapeHtml(fmtPx(s.mark_px))}</td>
    <td class="event-risk-num">${escapeHtml(fmtFunding(s.funding_rate))}</td>
    <td class="event-risk-num">${escapeHtml(fmtUsd(s.open_interest_usd))}</td>
    <td class="event-risk-num">${escapeHtml(fmtUsd(s.day_volume_usd))}</td>
  </tr>`;
}

function snapshotsTable(title, rows) {
  if (!rows?.length) return "";
  return `<h4 class="liquid-subhead">${escapeHtml(title)}</h4>
  <div class="event-risk-table-wrap"><table class="event-risk-table">
    <thead><tr><th>Market</th><th class="event-risk-num">Mark</th>
    <th class="event-risk-num">Funding (ann.)</th><th class="event-risk-num">Open interest</th>
    <th class="event-risk-num">24h volume</th></tr></thead>
    <tbody>${rows.map(snapshotRow).join("")}</tbody></table></div>`;
}

/** Pure payload -> HTML so node tests can pin the thin/no-signal branches. */
export function liquidOverviewHtml(payload) {
  const cov = payload?.coverage || {};
  if (!cov.rows) {
    return `<div class="event-risk-notice"><strong>No Liquid snapshots collected yet.</strong>
      <span class="muted">Run scripts/collect_liquid_snapshots.py --force, or enable LIQUID_COLLECT_ENABLED for the hourly bot-loop job.</span></div>`;
  }
  const top = payload.top_open_interest || [];
  const extremes = payload.funding_extremes || [];
  return `
    <div class="event-risk-kpis">
      ${kpiTile("Markets tracked", String(cov.tickers ?? "—"), `${Number(cov.rows ?? 0).toLocaleString()} snapshots stored`)}
      ${kpiTile("Thin / no-signal", String(payload.thin_count ?? "—"), `under ${fmtUsd(payload.min_oi_usd)} open interest`)}
      ${kpiTile("Mode", String(payload.mode || "shadow"), "shadow = display only, no rank influence")}
      ${kpiTile("Latest snapshot", cov.latest_ts ? String(cov.latest_ts).replace("T", " ").slice(0, 16) + " UTC" : "—", "collector runs hourly at :05")}
    </div>
    ${snapshotsTable("Largest open interest", top.slice(0, 10))}
    ${snapshotsTable("Funding extremes (liquid markets only)", extremes)}
    <p class="muted event-risk-footnote">
      Funding is what leveraged longs pay shorts (annualized). Near-zero funding = no crowding either way.
      Perp positioning from Liquid's xyz markets — not Schwab order flow. Research only; does not change scan scores or ranking.
    </p>`;
}

export function liquidTickerHtml(payload) {
  const ticker = escapeHtml(payload?.ticker || "");
  if (payload?.status === "not_on_liquid") {
    return `<div class="event-risk-notice"><strong>${ticker} is not listed on Liquid.</strong>
      <span class="muted">Liquid covers ~137 xyz markets, mostly mega-caps. No positioning data exists for this name — that is a coverage fact, not a signal.</span></div>`;
  }
  const s = payload.snapshot || {};
  const f = payload.features || {};
  const thinNote = payload.status === "thin_no_signal"
    ? `<div class="event-risk-notice"><strong>Thin market — no signal.</strong><span class="muted">Open interest is under the floor; treat positioning as absent, not neutral.</span></div>`
    : "";
  const z = Number(f.liquid_funding_z90);
  const pctRank = Number(f.liquid_funding_pct_rank);
  return `${thinNote}
    <div class="event-risk-kpis">
      ${kpiTile(`${ticker} mark`, fmtPx(s.mark_px), s.source_coin || "")}
      ${kpiTile("Funding (ann.)", fmtFunding(s.funding_rate), "current hourly rate annualized")}
      ${kpiTile("Open interest", fmtUsd(s.open_interest_usd), `24h volume ${fmtUsd(s.day_volume_usd)}`)}
      ${kpiTile("Funding history", `${payload.history_days ?? 0} days`, Number.isFinite(z) ? `z90 ${z.toFixed(2)} · pct rank ${Number.isFinite(pctRank) ? Math.round(pctRank * 100) + "%" : "—"}` : "z90 needs 20+ days")}
    </div>
    ${fundingSparkline(payload.funding_daily || [])}
    <p class="muted event-risk-footnote">Daily mean funding, last ${Math.min(180, (payload.funding_daily || []).length)} days. Positive = longs paying shorts (crowded-long pressure).</p>`;
}

/** Inline SVG bars for daily mean funding. Pure string builder. */
export function fundingSparkline(daily) {
  const rows = (daily || []).filter((r) => Number.isFinite(Number(r.funding_mean)));
  if (!rows.length) return `<p class="muted">No funding history backfilled for this ticker yet (scripts/backfill_liquid_funding.py).</p>`;
  const w = 640, h = 90, mid = h / 2;
  const maxAbs = Math.max(...rows.map((r) => Math.abs(Number(r.funding_mean))), 1e-9);
  const bw = w / rows.length;
  const bars = rows.map((r, i) => {
    const v = Number(r.funding_mean);
    const bh = Math.max(1, (Math.abs(v) / maxAbs) * (mid - 4));
    const y = v >= 0 ? mid - bh : mid;
    return `<rect x="${(i * bw).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1, bw - 0.5).toFixed(1)}" height="${bh.toFixed(1)}" class="liquid-bar${v < 0 ? " liquid-bar--neg" : ""}"><title>${escapeHtml(r.date)}: ${(v * 100).toFixed(4)}%/hr</title></rect>`;
  }).join("");
  return `<svg class="liquid-spark" viewBox="0 0 ${w} ${h}" role="img" aria-label="Daily funding history">
    <line x1="0" y1="${mid}" x2="${w}" y2="${mid}" class="liquid-zero" />${bars}</svg>`;
}

function renderOverview(payload) {
  const host = body();
  if (!host) return;
  host.innerHTML = `
    <div class="inline-form compact liquid-lookup">
      <input id="liquidTickerInput" type="text" placeholder="Ticker (e.g. AAPL)" aria-label="Liquid ticker lookup" />
      <button id="liquidTickerBtn" class="btn small secondary" type="button">Look up</button>
      <small class="muted">Funding / OI history for one Liquid-listed name.</small>
    </div>
    <div id="liquidTickerResult"></div>
    <div id="liquidOverviewResult">${liquidOverviewHtml(payload)}</div>`;
  setAsyncState(host, ASYNC_SUCCESS);
  document.getElementById("liquidTickerBtn")?.addEventListener("click", () => {
    const sym = document.getElementById("liquidTickerInput")?.value || "";
    if (sym.trim()) void loadLiquidTicker(sym);
  });
}

export async function loadLiquidTicker(ticker) {
  const host = document.getElementById("liquidTickerResult");
  if (!host) return;
  const symbol = String(ticker || "").trim().toUpperCase();
  const gen = ++_gen;
  host.innerHTML = `<p class="muted">Loading ${escapeHtml(symbol)}…</p>`;
  let out;
  try {
    out = await api.get(`/api/liquid/${encodeURIComponent(symbol)}`);
  } catch (err) {
    if (gen !== _gen) return;
    host.innerHTML = `<div class="event-risk-notice"><strong>Liquid lookup failed.</strong><span class="muted">${escapeHtml(String(err))}</span></div>`;
    return;
  }
  if (gen !== _gen) return;
  if (!out?.ok) {
    host.innerHTML = `<div class="event-risk-notice"><strong>Liquid lookup failed.</strong><span class="muted">${escapeHtml(out?.error || "Request failed.")}</span></div>`;
    logEvent({ kind: "system", severity: "warn", message: `Liquid ${symbol} failed` });
    return;
  }
  host.innerHTML = liquidTickerHtml(out.data || {});
}

export async function loadLiquidOverview() {
  const host = body();
  if (!host) return;
  setAsyncState(host, ASYNC_LOADING, { message: "Loading Liquid positioning…" });
  let out;
  try {
    out = await api.get("/api/liquid/overview");
  } catch (err) {
    setAsyncState(host, ASYNC_ERROR, {
      headline: "Liquid positioning unavailable.",
      message: String(err),
      onRetry: () => void loadLiquidOverview(),
    });
    return;
  }
  if (!out?.ok) {
    setAsyncState(host, ASYNC_ERROR, {
      headline: "Liquid positioning unavailable.",
      message: String(out?.error || "Request failed."),
      onRetry: () => void loadLiquidOverview(),
    });
    return;
  }
  _loaded = true;
  renderOverview(out.data || {});
}

export function initLiquidPositioningPanel() {
  const section = document.getElementById(SECTION_ID);
  if (!section) return;
  section.addEventListener("toggle", () => {
    if (section.open && !_loaded) void loadLiquidOverview();
  });
}
