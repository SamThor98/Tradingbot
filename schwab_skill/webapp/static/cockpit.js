/**
 * Trading Cockpit — four always-visible lanes driven by the /api/cockpit/*
 * DTO endpoints. Additive page (served at /cockpit); does not touch the main
 * dashboard. Every lane renders its provenance (source / as-of / confidence)
 * from the DTO envelope, per the cockpit architecture guardrail.
 */

import { api } from "./modules/api.js";
import { safeText, safeNum, formatDecimal } from "./modules/format.js";
import {
  setAsyncState,
  ASYNC_LOADING,
  ASYNC_EMPTY,
  ASYNC_ERROR,
  ASYNC_SUCCESS,
} from "./modules/asyncState.js";

const $ = (id) => document.getElementById(id);

function provBadge(prov) {
  if (!prov) return "";
  const conf = String(prov.confidence || "medium");
  const stale = prov.is_stale ? ' <span class="stale-dot" title="stale">●</span>' : "";
  const asOf = prov.as_of ? new Date(prov.as_of).toLocaleTimeString() : "live";
  return `<span class="prov-badge prov-${conf}" title="source=${safeText(prov.source)} as_of=${safeText(asOf)}">${safeText(prov.source)} · ${conf}${stale}</span>`;
}

function setProv(key, prov) {
  const el = document.querySelector(`.lane-prov[data-prov="${key}"]`);
  if (el) el.innerHTML = provBadge(prov);
}

async function loadLane(bodyId, fetchFn, renderFn, provKey) {
  const body = $(bodyId);
  if (!body) return;
  setAsyncState(body, ASYNC_LOADING, { message: "Loading…" });
  const out = await fetchFn();
  if (!out.ok) {
    setAsyncState(body, ASYNC_ERROR, {
      html: `<div class="async-state async-state--error" role="alert">
        <div>${safeText(out.user_message || out.error || "Unavailable")}</div>
        <button class="btn small secondary" type="button" data-async-retry>Retry</button>
      </div>`,
      onRetry: () => void loadLane(bodyId, fetchFn, renderFn, provKey),
    });
    setProv(provKey, null);
    return;
  }
  renderFn(body, out.data || {});
}

// --- Lane 1: Market Regime ------------------------------------------------ //
function renderMarket(body, data) {
  setProv("market", data.provenance);
  const cls =
    data.regime_state === "bullish" ? "regime-bull" : data.regime_state === "bearish" ? "regime-bear" : "regime-neutral";
  body.setAttribute("data-async-state", ASYNC_SUCCESS);
  body.innerHTML = `
    <div class="kv"><span>Regime</span><span class="${cls}"><strong>${safeText((data.regime_state || "—").toUpperCase())}</strong></span></div>
    <div class="kv"><span>Regime score</span><span>${formatDecimal(data.regime_score, 1, "—")} ${data.regime_bucket ? `(${safeText(data.regime_bucket)})` : ""}</span></div>
    <div class="kv"><span>SPY vs 200SMA</span><span>${formatDecimal(data.spy_price, 2, "—")} / ${formatDecimal(data.spy_sma_200, 2, "—")}</span></div>
    <div class="kv"><span>Volatility</span><span>${safeText(data.volatility_state || "—")}${data.vix_level != null ? ` (VIX ${formatDecimal(data.vix_level, 1, "—")})` : ""}</span></div>
    <div class="kv"><span>Scan blocked by regime</span><span>${data.scan_blocked_by_regime ? '<span class="pill bad">YES</span>' : '<span class="pill good">no</span>'}</span></div>
    <div class="kv"><span>Winning sectors</span><span>${(data.sector_breadth || []).map((s) => safeText(s.etf)).join(", ") || "—"}</span></div>
  `;
}

// --- Lane 2: Portfolio ---------------------------------------------------- //
function renderPortfolio(body, data) {
  setProv("portfolio", data.provenance);
  const ex = data.exposure || {};
  const conc = data.concentration || {};
  const positions = data.positions || [];
  body.setAttribute("data-async-state", ASYNC_SUCCESS);
  const posRows = positions
    .slice(0, 8)
    .map(
      (p) =>
        `<div class="kv"><span>${safeText(p.ticker)} ${p.sector_etf ? `<span class="pill muted">${safeText(p.sector_etf)}</span>` : ""}</span><span>${formatDecimal(p.weight_pct, 1, "—")}%</span></div>`,
    )
    .join("");
  body.innerHTML = `
    <div class="kv"><span>Equity</span><span>$${formatDecimal(data.equity, 0, "—")}</span></div>
    <div class="kv"><span>Cash / Buying power</span><span>$${formatDecimal(data.cash, 0, "—")} / $${formatDecimal(data.buying_power, 0, "—")}</span></div>
    <div class="kv"><span>Gross / Net exposure</span><span>${formatDecimal(ex.gross_pct, 1, "—")}% / ${formatDecimal(ex.net_pct, 1, "—")}%</span></div>
    <div class="kv"><span>Top1 / Top5 concentration</span><span>${formatDecimal(conc.top1_pct, 1, "—")}% / ${formatDecimal(conc.top5_pct, 1, "—")}%</span></div>
    ${(data.risk_flags || []).length ? `<div class="kv"><span>Risk flags</span><span>${(data.risk_flags || []).map((f) => `<span class="pill warn">${safeText(f)}</span>`).join(" ")}</span></div>` : ""}
    <div style="margin-top:8px; font-size:0.8rem; opacity:0.8;">Positions</div>
    ${posRows || '<div class="muted small">No open positions.</div>'}
  `;
}

// --- Lane 3: Opportunities ----------------------------------------------- //
function renderOpportunities(body, data) {
  const cards = data.opportunities || [];
  if (!cards.length) {
    setAsyncState(body, ASYNC_EMPTY, { message: "No scan results yet. Run a scan from the dashboard." });
    setProv("opportunities", null);
    return;
  }
  setProv("opportunities", cards[0]?.provenance);
  body.setAttribute("data-async-state", ASYNC_SUCCESS);
  body.innerHTML = cards
    .slice(0, 25)
    .map((c) => {
      const pt = c.pre_trade || {};
      const gate = c.gate_status || {};
      const tradeable = pt.tradeable;
      const badge =
        gate.disposition && gate.disposition !== "kept"
          ? `<span class="pill warn" title="${safeText((gate.reasons || []).join(', '))}">${safeText(gate.disposition.replace('filtered_', ''))}</span>`
          : tradeable === false
            ? `<span class="pill bad" title="${safeText((pt.blockers || []).join(', '))}">gated</span>`
            : `<span class="pill good">ready</span>`;
      return `<div class="opp-row" data-ticker="${safeText(c.ticker)}">
        <span class="tk">${safeText(c.ticker)}</span>
        <span class="muted small">${safeText(c.setup?.strategy_top_live || "")} ${c.setup?.breakout_confirmed ? "· breakout" : ""}</span>
        <span class="rank">${formatDecimal(c.rank?.rank_score, 1, "—")}</span>
        ${badge}
      </div>`;
    })
    .join("");
  body.querySelectorAll(".opp-row").forEach((row) => {
    row.addEventListener("click", () => openDrawer(row.getAttribute("data-ticker")));
  });
}

// --- Lane 4: Blotter ------------------------------------------------------ //
function renderBlotter(body, data) {
  const rows = data.blotter || [];
  if (!rows.length) {
    setAsyncState(body, ASYNC_EMPTY, { message: "No staged or executed orders." });
    setProv("blotter", null);
    return;
  }
  setProv("blotter", rows[0]?.provenance);
  body.setAttribute("data-async-state", ASYNC_SUCCESS);
  const stateCls = (s) =>
    ["filled"].includes(s) ? "good" : ["rejected", "failed", "cancelled", "expired"].includes(s) ? "bad" : "warn";
  body.innerHTML = rows
    .slice(0, 20)
    .map(
      (r) => `<div class="kv">
        <span>${safeText(r.ticker)} <span class="muted small">${safeText(r.side)} ${formatDecimal(r.qty, 0, "")}</span></span>
        <span><span class="pill ${stateCls(r.state)}">${safeText(r.state)}</span>${r.quality?.realized_slippage_bps != null ? ` <span class="muted small">${formatDecimal(r.quality.realized_slippage_bps, 1)}bps</span>` : ""}</span>
      </div>`,
    )
    .join("");
}

// --- Drilldown drawer: decision card + order-intent preview --------------- //
async function openDrawer(ticker) {
  const drawer = $("cockpitDrawer");
  const bodyEl = $("drawerBody");
  if (!drawer || !bodyEl || !ticker) return;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  bodyEl.innerHTML = `<h3>${safeText(ticker)}</h3><div class="muted">Loading decision card…</div>`;

  const [cardOut, previewOut, optionsOut] = await Promise.all([
    api.get(`/api/decision-card/${encodeURIComponent(ticker)}`),
    api.post("/api/cockpit/order-intent/preview", { ticker }),
    api.get(`/api/cockpit/symbol/${encodeURIComponent(ticker)}/options`),
  ]);

  const card = cardOut.ok ? cardOut.data || {} : {};
  const preview = previewOut.ok ? previewOut.data || {} : {};
  const pt = preview.pre_trade || {};
  const conf = card.confidence || {};
  const opt = optionsOut.ok ? (optionsOut.data || {}).options_intel : null;
  const optHtml = opt
    ? `<div class="preview-box">
        <strong>Options intelligence</strong>
        <div class="kv"><span>ATM IV</span><span>${opt.atm_iv != null ? (opt.atm_iv * 100).toFixed(1) + "%" : "—"}</span></div>
        <div class="kv"><span>Put/Call skew</span><span>${opt.put_call_skew != null ? (opt.put_call_skew * 100).toFixed(1) + " pts" : "—"}</span></div>
        <div class="kv"><span>Expected move</span><span>${formatDecimal(opt.expected_move_pct, 2, "—")}%</span></div>
        <div class="kv"><span>Nearest expiry</span><span>${safeText(opt.nearest_expiry || "—")}</span></div>
      </div>`
    : "";

  bodyEl.innerHTML = `
    <h3>${safeText(ticker)} <span class="muted small">decision card</span></h3>
    ${cardOut.ok ? "" : `<div class="pill bad">${safeText(cardOut.user_message || cardOut.error)}</div>`}
    <div class="kv"><span>Composite / Rank</span><span>${formatDecimal(conf.signal_score, 1, "—")} / ${formatDecimal(conf.rank_score, 1, "—")}</span></div>
    <div class="kv"><span>Confidence</span><span>${safeText(conf.bucket || "—")}</span></div>
    <div class="kv"><span>Entry zone</span><span>${safeText(card.entry_zone?.low ?? "—")} – ${safeText(card.entry_zone?.high ?? "—")}</span></div>
    <div class="kv"><span>Stop / invalidation</span><span>${formatDecimal(card.stop_invalidation, 2, "—")}</span></div>
    <div class="kv"><span>Size</span><span>${safeText(card.size?.qty ?? "—")} sh (~$${formatDecimal(card.size?.usd, 0, "—")})</span></div>
    <div style="margin-top:10px; font-size:0.85rem; opacity:0.85;">Key reasons</div>
    <ul style="margin:4px 0; padding-left:18px;">${(card.key_reasons || []).map((r) => `<li>${safeText(r)}</li>`).join("") || "<li class='muted'>—</li>"}</ul>

    <div class="preview-box">
      <div style="display:flex; align-items:center; gap:8px;">
        <strong>Order-intent preview</strong>
        ${pt.tradeable === false ? '<span class="pill bad">gated</span>' : '<span class="pill good">clear</span>'}
        <span class="muted small" style="margin-left:auto;">mode: ${safeText(preview.gates_mode || "—")}</span>
      </div>
      <div class="kv"><span>Intent</span><span>${safeText(preview.intent?.side || "BUY")} ${safeText(preview.intent?.order_type || "MARKET")}</span></div>
      <div class="kv"><span>Expected price</span><span>${formatDecimal(preview.quality?.expected_price, 2, "—")}</span></div>
      <div class="kv"><span>Spread</span><span>${formatDecimal(pt.spread_bps, 1, "—")} bps</span></div>
      <div class="kv"><span>Quote fresh / Liquidity</span><span>${pt.quote_fresh == null ? "—" : pt.quote_fresh ? "yes" : "no"} / ${pt.liquidity_ok == null ? "—" : pt.liquidity_ok ? "ok" : "low"}</span></div>
      <div class="kv"><span>Event risk</span><span>${safeText(pt.event_risk || "none")}</span></div>
      ${(pt.blockers || []).length ? `<div class="kv"><span>Blockers</span><span>${(pt.blockers || []).map((b) => `<span class="pill bad">${safeText(b)}</span>`).join(" ")}</span></div>` : ""}
      <div class="muted small" style="margin-top:6px;">Preview is read-only. Stage &amp; approve from the full dashboard to place a live order.</div>
    </div>
    ${optHtml}
  `;
}

function closeDrawer() {
  const drawer = $("cockpitDrawer");
  if (!drawer) return;
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
}

// --- "What changed since last cycle" strip -------------------------------- //
async function loadChangeStrip() {
  const strip = $("oppChangeStrip");
  if (!strip) return;
  const [deltaOut, wlOut] = await Promise.all([
    api.get("/api/cockpit/deltas"),
    api.get("/api/cockpit/watchlists"),
  ]);
  if (!deltaOut.ok && !wlOut.ok) {
    strip.innerHTML = "";
    return;
  }
  const d = deltaOut.data || {};
  const wl = wlOut.data || {};
  if (!d.has_prior) {
    strip.innerHTML = `<span class="change-chip muted">First cycle — no prior scan to diff.</span>`;
    return;
  }
  const c = d.counts || {};
  const chips = [
    `<span class="change-chip" title="${(d.new_tickers || []).join(', ')}">🆕 new <b>${safeNum(c.new, 0)}</b></span>`,
    `<span class="change-chip" title="${(d.dropped_tickers || []).join(', ')}">➖ dropped <b>${safeNum(c.dropped, 0)}</b></span>`,
    `<span class="change-chip">📈 breaking out <b>${(wl.breaking_out_now || []).length}</b></span>`,
    `<span class="change-chip">⤴ improving <b>${(wl.setup_improving || []).length}</b></span>`,
    `<span class="change-chip" title="${(wl.risk_rising || []).map((r) => r.ticker).join(', ')}">⚠ risk rising <b>${(wl.risk_rising || []).length}</b></span>`,
  ];
  strip.innerHTML = chips.join("");
}

// --- Execution-quality attribution strip ---------------------------------- //
async function loadExecQuality() {
  const strip = $("blotterQualityStrip");
  if (!strip) return;
  const out = await api.get("/api/cockpit/execution/quality");
  if (!out.ok) {
    strip.innerHTML = "";
    return;
  }
  const q = out.data || {};
  const sl = q.slippage || {};
  const pe = q.policy_events || {};
  strip.innerHTML = [
    `<span class="change-chip">avg slip <b>${sl.avg_realized_bps != null ? sl.avg_realized_bps + " bps" : "—"}</b></span>`,
    `<span class="change-chip">filled <b>${(q.lifecycle_counts || {}).filled || 0}</b></span>`,
    `<span class="change-chip" title="policy decisions evaluated in shadow/live">policy eval <b>${pe.evaluated || 0}</b></span>`,
    `<span class="change-chip">would-block <b>${pe.shadow_would_block || 0}</b></span>`,
  ].join("");
}

// --- Learning loop summary (header) --------------------------------------- //
async function loadReview() {
  const el = $("cockpitReview");
  if (!el) return;
  const out = await api.get("/api/cockpit/review");
  if (!out.ok) {
    el.textContent = "";
    return;
  }
  const r = out.data || {};
  const props = (r.tuning_proposals || {}).count || 0;
  el.textContent = `🧠 ${r.total_packets || 0} packets · ${safeNum(r.coverage_pct, 0)}% resolved · ${props} tuning proposal${props === 1 ? "" : "s"}`;
}

async function refreshAll() {
  $("cockpitUpdated").textContent = "refreshing…";
  await Promise.all([
    loadLane("marketBody", () => api.get("/api/cockpit/market"), renderMarket, "market"),
    loadLane("portfolioBody", () => api.get("/api/cockpit/portfolio"), renderPortfolio, "portfolio"),
    loadLane("opportunitiesBody", () => api.get("/api/cockpit/opportunities"), renderOpportunities, "opportunities"),
    loadLane("blotterBody", () => api.get("/api/cockpit/blotter"), renderBlotter, "blotter"),
    loadChangeStrip(),
    loadExecQuality(),
    loadReview(),
  ]);
  $("cockpitUpdated").textContent = `updated ${new Date().toLocaleTimeString()}`;
}

function init() {
  $("cockpitRefresh")?.addEventListener("click", () => void refreshAll());
  $("drawerClose")?.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });
  void refreshAll();
  // Lightweight auto-refresh; lanes each carry their own freshness badge.
  setInterval(() => void refreshAll(), 60000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
