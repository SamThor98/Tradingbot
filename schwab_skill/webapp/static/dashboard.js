/* =========================================================================
 * APERTURE — Trading terminal UI controller
 * Hash-routed, vanilla JS, no build step. Talks to the same /api/* endpoints
 * the legacy dashboard used; the legacy app.js / styles.css are no longer
 * loaded by index.html. /simple still works for fallback.
 * ========================================================================= */

(() => {
  "use strict";

  // -----------------------------------------------------------------
  // tiny helpers
  // -----------------------------------------------------------------
  const $  = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const make = (tag, props = {}, ...children) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k === "data") for (const [dk, dv] of Object.entries(v)) el.dataset[dk] = dv;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (v != null) el.setAttribute(k, v);
    }
    children.forEach((c) => {
      if (c == null) return;
      el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    });
    return el;
  };
  const fmtMoney = (n, digits = 2) =>
    n == null || !isFinite(n) ? "—" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const fmtPct = (n, d = 1) => (n == null || !isFinite(n) ? "—" : `${(Number(n) * 100).toFixed(d)}%`);
  const fmtNum = (n, d = 1) => (n == null || !isFinite(n) ? "—" : Number(n).toFixed(d));
  const safe = (s) => (s == null ? "" : String(s));
  const clamp = (n, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, Number(n) || 0));
  const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtRelTime = (iso) => {
    if (!iso) return "Never";
    const t = new Date(iso).getTime();
    if (!isFinite(t)) return "Never";
    const sec = Math.round((Date.now() - t) / 1000);
    if (sec < 30) return "just now";
    if (sec < 60) return `${sec}s ago`;
    if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
    return `${Math.round(sec / 86400)}d ago`;
  };

  // -----------------------------------------------------------------
  // state
  // -----------------------------------------------------------------
  const state = {
    publicConfig: { api_key_required: false, saas_mode: false },
    user: null,
    signals: [],
    lastScanAt: null,
    lastScanDiagnostics: null,
    lastScanFunnel: null,
    pending: [],
    portfolio: [],
    sectors: [],
    health: {},
    onboarding: null,
    sortKey: "score",
    activity: [],
    scanPolling: false,
    chartInstances: new Map(),
    sparkCache: new Map(),
  };

  const PRESET_TIERS = {
    conservative: {
      label: "Conservative",
      env: { POSITION_SIZE_USD: "300", MAX_TRADES_PER_DAY: "3", QUALITY_GATES_MODE: "hard", EVENT_RISK_MODE: "live", EVENT_ACTION: "block", EXEC_QUALITY_MODE: "live" },
      desc: "Tight gates, smaller size, full earnings block.",
    },
    balanced: {
      label: "Balanced",
      env: { POSITION_SIZE_USD: "500", MAX_TRADES_PER_DAY: "5", QUALITY_GATES_MODE: "soft", EVENT_RISK_MODE: "live", EVENT_ACTION: "downsize", EXEC_QUALITY_MODE: "live" },
      desc: "Calibrated funnel; downsize on risk events.",
    },
    aggressive: {
      label: "Aggressive",
      env: { POSITION_SIZE_USD: "900", MAX_TRADES_PER_DAY: "8", QUALITY_GATES_MODE: "soft", EVENT_RISK_MODE: "shadow", EVENT_ACTION: "downsize", EXEC_QUALITY_MODE: "shadow" },
      desc: "Wider gates, larger size, shadow-only quality checks.",
    },
  };

  // -----------------------------------------------------------------
  // toast / activity
  // -----------------------------------------------------------------
  function toast({ title = "", message = "", sev = "info", ttl = 4500 } = {}) {
    const host = $("#toastHost");
    if (!host) return;
    const t = make("div", { class: "toast", "data-sev": sev });
    if (title) t.appendChild(make("strong", {}, title));
    t.appendChild(make("span", {}, message));
    host.appendChild(t);
    setTimeout(() => {
      t.style.opacity = "0";
      t.style.transition = "opacity 200ms ease";
      setTimeout(() => t.remove(), 220);
    }, ttl);
  }
  function logActivity(kind, message, sev = "info") {
    state.activity.unshift({ kind, message, sev, at: new Date().toISOString() });
    state.activity = state.activity.slice(0, 60);
    if (location.hash === "#/diagnostics") renderActivityLog();
  }

  // -----------------------------------------------------------------
  // api client (lightweight wrapper around fetch)
  // -----------------------------------------------------------------
  const api = (() => {
    const tokenKey = "tradingbot.jwt";
    const apiKeyKey = "tradingbot.api_key";

    async function request(path, options = {}) {
      const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
      headers["X-Request-ID"] = `aperture-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
      const jwt = localStorage.getItem(tokenKey);
      if (jwt) headers.Authorization = `Bearer ${jwt}`;
      if (state.publicConfig?.api_key_required) {
        const k = localStorage.getItem(apiKeyKey);
        if (k) headers["X-API-Key"] = k;
      }
      try {
        const res = await fetch(path, { ...options, headers, credentials: options.credentials ?? "same-origin" });
        const text = await res.text();
        let body;
        try { body = text ? JSON.parse(text) : {}; }
        catch { body = { ok: false, error: `Invalid JSON (${res.status})` }; }
        if (!res.ok) {
          return { ok: false, status: res.status, error: body?.error || body?.detail || `HTTP ${res.status}`, data: body?.data ?? null };
        }
        return body;
      } catch (err) {
        return { ok: false, error: err?.message || "Network error" };
      }
    }
    return {
      request,
      get: (p) => request(p, { method: "GET" }),
      post: (p, body = {}) => request(p, { method: "POST", body: JSON.stringify(body) }),
      patch: (p, body = {}) => request(p, { method: "PATCH", body: JSON.stringify(body) }),
      del: (p) => request(p, { method: "DELETE" }),
      _tokenKey: tokenKey,
      _apiKeyKey: apiKeyKey,
    };
  })();

  // -----------------------------------------------------------------
  // bootstrap: public config, status, user
  // -----------------------------------------------------------------
  async function bootstrap() {
    const cfg = await api.get("/api/public-config");
    if (cfg?.ok && cfg?.data) state.publicConfig = { ...state.publicConfig, ...cfg.data };
    await refreshStatus({ silent: true });
    if (state.publicConfig.saas_mode) await refreshAccountMe();
    await refreshHealth({ silent: true });
    await refreshOnboarding({ silent: true });
    updateUserChip();
    updateSchwabPill();
  }

  function updateUserChip() {
    const monoEl = $("#userMonogram");
    const mailEl = $("#userEmailLabel");
    const u = state.user;
    if (u?.email) {
      const initial = u.email.trim().charAt(0).toUpperCase();
      monoEl.textContent = initial;
      mailEl.textContent = u.email;
    } else {
      monoEl.textContent = "·";
      mailEl.textContent = state.publicConfig.saas_mode ? "Sign in" : "Local";
    }
  }

  function updateSchwabPill() {
    const pill = $("#statusPillSchwab");
    if (!pill) return;
    const acct = (state.health?.account_token || "").toLowerCase();
    const mkt = (state.health?.market_token || "").toLowerCase();
    let s = "neutral";
    let txt = "Schwab — checking";
    const aOk = acct === "ok" || acct === "valid" || acct.includes("active");
    const mOk = mkt === "ok" || mkt === "valid" || mkt.includes("active");
    if (aOk && mOk) { s = "ok"; txt = "Schwab — connected"; }
    else if (aOk || mOk) { s = "warn"; txt = "Schwab — partial"; }
    else { s = "bad"; txt = "Schwab — not linked"; }
    pill.dataset.state = s;
    pill.querySelector(".status-text").textContent = txt;

    const live = $("#statusPillTrading");
    if (live) {
      const liveOn = state.user?.live_execution_enabled;
      live.dataset.state = liveOn ? "ok" : "warn";
      live.querySelector(".status-text").textContent = liveOn ? "Trading — live" : "Trading — paper";
    }
  }

  async function refreshStatus({ silent = false } = {}) {
    const out = await api.get("/api/status");
    if (out.ok && out.data) {
      const d = out.data;
      state.user = d?.user || state.user;
      state.lastScanAt = d?.last_scan?.at || state.lastScanAt;
      if (Array.isArray(d?.last_scan?.signals)) state.signals = d.last_scan.signals;
      if (d?.last_scan?.diagnostics_summary?.funnel) state.lastScanFunnel = d.last_scan.diagnostics_summary.funnel;
      if (d?.last_scan?.diagnostics_summary?.blockers) state.lastScanBlockers = d.last_scan.diagnostics_summary.blockers;
    } else if (!silent) {
      toast({ title: "Status fetch failed", message: out.error || "", sev: "warn" });
    }
  }

  async function refreshHealth({ silent = false } = {}) {
    const [shallow, deep] = await Promise.all([api.get("/api/health"), api.get("/api/health/deep")]);
    const merged = {};
    if (shallow?.ok && shallow.data) Object.assign(merged, shallow.data);
    if (deep?.ok && deep.data) Object.assign(merged, deep.data);
    state.health = merged;
    updateSchwabPill();
    if (!silent) renderHealthTiles();
  }

  async function refreshOnboarding({ silent = false } = {}) {
    const out = await api.get("/api/onboarding/status");
    if (out.ok && out.data) {
      state.onboarding = out.data;
    } else if (!silent) {
      toast({ message: out.error || "Could not load setup status", sev: "warn" });
    }
  }

  // -----------------------------------------------------------------
  // ROUTER
  // -----------------------------------------------------------------
  const ROUTES = {
    scans:       { title: "Scans",       sub: "Run a tier and surface today's candidates.",     render: renderScans },
    queue:       { title: "Trade queue", sub: "Approve, reject, or remove staged trades.",       render: renderQueue },
    portfolio:   { title: "Portfolio",   sub: "Live positions and sector posture.",              render: renderPortfolio },
    research:    { title: "Research",    sub: "Single-ticker checks, reports, backtests, more.", render: renderResearch },
    diagnostics: { title: "Diagnostics", sub: "System health, performance, learning, activity.", render: renderDiagnostics },
    settings:    { title: "Settings",    sub: "Connections, presets, security, and billing.",    render: renderSettings },
  };

  function currentRoute() {
    const h = (location.hash || "").replace(/^#\/?/, "");
    const key = h.split("?")[0] || "scans";
    return ROUTES[key] ? key : "scans";
  }

  function setRoute(key) {
    if (location.hash !== `#/${key}`) {
      location.hash = `#/${key}`;
      return;
    }
    renderRoute();
  }

  function renderRoute() {
    const key = currentRoute();
    const route = ROUTES[key];
    $$(".nav-link").forEach((a) => a.classList.toggle("is-active", a.dataset.route === key));
    const titleEl = $("#topbarTitle");
    const subEl = $("#topbarSubtitle");
    if (titleEl) titleEl.textContent = route.title;
    if (subEl) subEl.textContent = route.sub;
    state.chartInstances.forEach((c) => { try { c.remove?.(); } catch {} });
    state.chartInstances.clear();
    const root = $("#viewRoot");
    root.innerHTML = "";
    const tpl = $(`#tpl-${key}`);
    if (tpl) root.appendChild(tpl.content.cloneNode(true));
    route.render();
  }

  window.addEventListener("hashchange", renderRoute);

  // =================================================================
  // VIEW: SCANS
  // =================================================================
  function renderScans() {
    const dateEl = $("#heroDate");
    if (dateEl) dateEl.textContent = new Date().toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });

    $$(".tier-run").forEach((btn) => {
      btn.addEventListener("click", () => runScan(btn.dataset.tier));
    });

    const sortSel = $("#resultsSort");
    if (sortSel) {
      sortSel.value = state.sortKey;
      sortSel.addEventListener("change", () => { state.sortKey = sortSel.value; renderResults(); });
    }

    $("#scanCancelBtn")?.addEventListener("click", () => { state.scanPolling = false; setScanProgress(null); });

    refreshScanStatus({ initial: true });
    renderResults();
    refreshPending({ silent: true }).then(updateHeroStats);
    updateHeroStats();
  }

  function updateHeroStats() {
    $("#heroLastScan") && ($("#heroLastScan").textContent = state.lastScanAt ? fmtRelTime(state.lastScanAt) : "Never");
    $("#heroSignals") && ($("#heroSignals").textContent = state.signals.length || "0");
    $("#heroQueue") && ($("#heroQueue").textContent = state.pending.filter((r) => r.status === "pending").length);
    const badge = $("#queueBadge");
    if (badge) {
      const n = state.pending.filter((r) => r.status === "pending").length;
      badge.textContent = String(n);
      badge.hidden = n <= 0;
    }
  }

  function setScanProgress(label) {
    const wrap = $("#scanProgress");
    const lbl = $("#scanProgressLabel");
    if (!wrap) return;
    if (label) {
      wrap.hidden = false;
      if (lbl) lbl.textContent = label;
    } else {
      wrap.hidden = true;
    }
  }

  async function runScan(tier) {
    const preset = PRESET_TIERS[tier];
    if (!preset) return;
    setScanProgress(`Applying ${preset.label} preset…`);
    logActivity("scan", `Started ${preset.label} scan`);
    // 1) Apply tier server-side so position size, daily cap, and event-risk rules take effect.
    const applyOut = await api.post(`/api/settings/profile?profile=${encodeURIComponent(tier)}&mode=standard`, {});
    if (!applyOut.ok) {
      setScanProgress(null);
      toast({ title: "Could not apply tier", message: applyOut.error || "", sev: "error" });
      logActivity("scan", `Apply ${tier} failed: ${applyOut.error}`, "error");
      return;
    }
    // 2) Trigger scan. Only pass overrides the server's StrategyOverrides whitelist accepts.
    setScanProgress(`Running ${preset.label} scan…`);
    const body = { strategy_overrides: { quality_gates_mode: preset.env.QUALITY_GATES_MODE } };
    const out = await api.post("/api/scan?async_mode=true", body);
    if (!out.ok) {
      setScanProgress(null);
      toast({ title: "Scan failed", message: out.error || "Could not start scan", sev: "error" });
      logActivity("scan", `Scan failed: ${out.error}`, "error");
      return;
    }
    if (out.data?.status === "running" || out.data?.started) {
      pollScan();
    } else if (Array.isArray(out.data?.signals)) {
      state.signals = out.data.signals;
      state.lastScanAt = new Date().toISOString();
      state.lastScanFunnel = out.data?.diagnostics_summary?.funnel || null;
      state.lastScanBlockers = out.data?.diagnostics_summary?.blockers || [];
      setScanProgress(null);
      renderResults();
      updateHeroStats();
      logActivity("scan", `Scan complete · ${state.signals.length} signals`);
    }
  }

  async function pollScan() {
    state.scanPolling = true;
    let attempts = 0;
    while (state.scanPolling && attempts < 240) {
      attempts += 1;
      await new Promise((r) => setTimeout(r, 1500));
      if (!state.scanPolling) return;
      const out = await api.get("/api/scan/status");
      if (!out.ok) continue;
      const d = out.data || {};
      if (d.status === "running") {
        const stats = d.in_flight ? `${d.in_flight} symbols in flight` : "scanning";
        setScanProgress(`Scanning the universe — ${stats}…`);
        continue;
      }
      // idle / done
      const last = d.last_scan || {};
      if (Array.isArray(last.signals)) state.signals = last.signals;
      state.lastScanAt = last.at || state.lastScanAt;
      state.lastScanFunnel = last?.diagnostics_summary?.funnel || null;
      state.lastScanBlockers = last?.diagnostics_summary?.blockers || [];
      setScanProgress(null);
      renderResults();
      updateHeroStats();
      logActivity("scan", `Scan complete · ${state.signals.length} signals`);
      state.scanPolling = false;
      return;
    }
    setScanProgress(null);
    state.scanPolling = false;
  }

  async function refreshScanStatus({ initial = false } = {}) {
    const out = await api.get("/api/scan/status");
    if (!out.ok) return;
    const d = out.data || {};
    if (d.status === "running") {
      setScanProgress("Scan in progress…");
      pollScan();
    } else {
      const last = d.last_scan || {};
      if (Array.isArray(last.signals)) state.signals = last.signals;
      state.lastScanAt = last.at || state.lastScanAt;
      state.lastScanFunnel = last?.diagnostics_summary?.funnel || state.lastScanFunnel;
      state.lastScanBlockers = last?.diagnostics_summary?.blockers || state.lastScanBlockers;
    }
    if (initial) { renderResults(); updateHeroStats(); }
  }

  // ---------- result rendering ----------
  function normalizeSignal(s = {}) {
    const ticker = (s.ticker || s.symbol || "?").toUpperCase();
    const score = num(s.signal_score ?? s.score);
    const advisory = s.advisory || {};
    const pUp = num(advisory.p_up_10d ?? advisory.p_up_10d_raw ?? s.p_up_10d ?? s.advisory_p_up);
    const conf = (advisory.confidence_bucket ?? s.confidence_bucket ?? s.advisory_confidence ?? "—");
    const conviction = num(s.mirofish_conviction ?? s.conviction_score ?? s.mirofish_result?.conviction_score);
    const price = num(s.price ?? s.current_price);
    const sector = s.sector_etf || s.sector || "—";
    const strategy = (s.strategy_attribution?.top_live || s.strategy || s.strategy_label || "—");
    const flagged = num(s.flagged_days ?? s.days_flagged) || 0;
    return { raw: s, ticker, score, pUp, conf, conviction, price, sector, strategy, flagged };
  }
  function num(v) { const n = Number(v); return isFinite(n) ? n : null; }

  function getSortedSignals() {
    const list = state.signals.map(normalizeSignal);
    const k = state.sortKey;
    list.sort((a, b) => {
      if (k === "ticker") return a.ticker.localeCompare(b.ticker);
      if (k === "pup") return (b.pUp ?? -Infinity) - (a.pUp ?? -Infinity);
      if (k === "conviction") return (b.conviction ?? -Infinity) - (a.conviction ?? -Infinity);
      return (b.score ?? -Infinity) - (a.score ?? -Infinity);
    });
    return list;
  }

  function renderResults() {
    const grid = $("#resultsGrid");
    const meta = $("#resultsMeta");
    if (!grid) return;
    grid.innerHTML = "";
    const list = getSortedSignals();
    if (meta) {
      meta.textContent = list.length
        ? `${list.length} candidate${list.length === 1 ? "" : "s"} · last scan ${fmtRelTime(state.lastScanAt)}`
        : "No scan run yet — pick a tier above.";
    }
    if (!list.length) {
      grid.innerHTML = `
        <div class="results-empty">
          <svg viewBox="0 0 64 64" width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
            <circle cx="28" cy="28" r="16"/>
            <path d="m40 40 14 14"/>
            <path d="M22 28h12M28 22v12"/>
          </svg>
          <p>The grid fills in once a tier finishes scanning.</p>
        </div>`;
      return;
    }
    list.forEach((sig) => grid.appendChild(buildResultCard(sig)));
  }

  function buildResultCard(sig) {
    const card = make("article", { class: "result", "data-ticker": sig.ticker });

    // 1: symbol
    const sym = make("div", { class: "r-symbol" });
    sym.appendChild(make("span", { class: "r-ticker" }, sig.ticker));
    const meta = make("div", { class: "r-meta" });
    meta.appendChild(make("span", { class: "r-strategy" }, formatStrategyLabel(sig.strategy)));
    meta.appendChild(make("span", { class: "r-sector" }, sig.sector));
    sym.appendChild(meta);
    card.appendChild(sym);

    // 2: sparkline placeholder (loads on demand)
    const spark = make("div", { class: "r-spark" });
    spark.innerHTML = sparkSvgPlaceholder();
    card.appendChild(spark);
    loadSparkline(sig.ticker, spark);

    // 3: meters
    const meters = make("div", { class: "r-meters" });
    meters.appendChild(buildMeter("Score", sig.score, 100, "gold"));
    meters.appendChild(buildMeter("P(up 10d)", sig.pUp != null ? sig.pUp * 100 : null, 100, "green"));
    meters.appendChild(buildMeter("Conviction", sig.conviction != null ? (sig.conviction + 100) / 2 : null, 100, sig.conviction >= 0 ? "green" : "rust"));
    card.appendChild(meters);

    // 4: price block
    const px = make("div", { class: "r-price" });
    px.appendChild(make("span", { class: "r-price-now" }, sig.price != null ? fmtMoney(sig.price) : "—"));
    px.appendChild(make("span", { class: "r-price-meta" }, sig.flagged ? `Flagged ${sig.flagged}d` : "First scan"));
    if (sig.conf && sig.conf !== "—") {
      const conf = make("span", { class: "r-price-meta" }, `Confidence: ${sig.conf}`);
      px.appendChild(conf);
    }
    card.appendChild(px);

    // 5: actions
    const actions = make("div", { class: "r-actions" });
    const expandBtn = make("button", { class: "btn ghost small", type: "button" }, "Open chart");
    expandBtn.addEventListener("click", () => toggleResultExpand(card, sig, expandBtn));
    const stageBtn = make("button", { class: "btn primary small", type: "button" }, "Stage trade");
    stageBtn.addEventListener("click", () => openStageDialog(sig));
    actions.appendChild(expandBtn);
    actions.appendChild(stageBtn);
    card.appendChild(actions);

    // expanded panel (chart + description)
    const expand = make("div", { class: "r-expand" });
    const chartCol = make("div", { class: "r-chart-container", "data-chart-host": sig.ticker });
    const detail = make("div", { class: "r-detail" });
    detail.appendChild(make("h4", {}, "Why this ticker?"));
    detail.appendChild(make("p", {}, buildSignalDescription(sig)));
    const grid = make("div", { class: "r-detail-grid" });
    grid.appendChild(detailItem("Strategy", formatStrategyLabel(sig.strategy)));
    grid.appendChild(detailItem("Sector ETF", sig.sector));
    grid.appendChild(detailItem("Score", sig.score != null ? sig.score.toFixed(1) : "—"));
    grid.appendChild(detailItem("P(up 10d)", sig.pUp != null ? fmtPct(sig.pUp, 1) : "—"));
    grid.appendChild(detailItem("Conviction", sig.conviction != null ? fmtNum(sig.conviction, 1) : "—"));
    grid.appendChild(detailItem("Last price", sig.price != null ? fmtMoney(sig.price) : "—"));
    detail.appendChild(grid);
    expand.appendChild(chartCol);
    expand.appendChild(detail);
    card.appendChild(expand);

    return card;
  }

  function detailItem(label, value) {
    const it = make("div", { class: "r-detail-item" });
    it.appendChild(make("span", {}, label));
    it.appendChild(make("strong", {}, safe(value)));
    return it;
  }

  function buildMeter(label, val, max, palette = "gold") {
    const row = make("div", { class: "meter" });
    row.appendChild(make("span", { class: "meter-label" }, label));
    const bar = make("div", { class: "meter-bar" });
    const fill = make("span", { class: `meter-bar-fill ${palette === "gold" ? "" : palette}` });
    fill.style.width = (val == null ? 0 : clamp(val, 0, max)) + "%";
    bar.appendChild(fill);
    row.appendChild(bar);
    row.appendChild(make("span", { class: "meter-val" }, val == null ? "—" : (val > 1 ? val.toFixed(0) : val.toFixed(2))));
    return row;
  }

  function formatStrategyLabel(s) {
    return String(s || "—").replace(/[_\-]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function buildSignalDescription(sig) {
    const parts = [];
    if (sig.strategy && sig.strategy !== "—") parts.push(`The ${formatStrategyLabel(sig.strategy)} strategy fired on this name.`);
    if (sig.score != null) {
      const pos = sig.score >= 75 ? "high-conviction" : sig.score >= 55 ? "moderate" : "marginal";
      parts.push(`Composite score ${sig.score.toFixed(1)} (${pos}).`);
    }
    if (sig.pUp != null) parts.push(`Calibrated probability of being up 10 days from now is ${fmtPct(sig.pUp, 1)}.`);
    if (sig.conviction != null) {
      const dir = sig.conviction >= 30 ? "bullish" : sig.conviction <= -30 ? "bearish" : "mixed";
      parts.push(`Sentiment synthesis is ${dir} (conviction ${fmtNum(sig.conviction, 1)}).`);
    }
    if (sig.flagged > 0) parts.push(`Has been flagged for ${sig.flagged} consecutive scans.`);
    if (!parts.length) parts.push("Passed Stage A & B filters with no enrichment available.");
    return parts.join(" ");
  }

  function sparkSvgPlaceholder() {
    return `<svg viewBox="0 0 200 60" preserveAspectRatio="none">
      <line x1="0" x2="200" y1="40" y2="40" stroke="#262E3D" stroke-width="1" stroke-dasharray="2 4"/>
    </svg>`;
  }

  async function loadSparkline(ticker, host) {
    let candles = state.sparkCache.get(ticker);
    if (!candles) {
      const out = await api.get(`/api/chart/${encodeURIComponent(ticker)}?days=60`);
      if (!out.ok || !Array.isArray(out.data?.candles) || !out.data.candles.length) return;
      candles = out.data.candles;
      state.sparkCache.set(ticker, candles);
    }
    const closes = candles.map((c) => c.close);
    const min = Math.min(...closes);
    const max = Math.max(...closes);
    const range = max - min || 1;
    const W = 200, H = 60;
    const dx = closes.length > 1 ? W / (closes.length - 1) : W;
    const points = closes.map((c, i) => `${(i * dx).toFixed(2)},${(H - ((c - min) / range) * (H - 6) - 3).toFixed(2)}`);
    const last = closes[closes.length - 1];
    const first = closes[0];
    const isUp = last >= first;
    const color = isUp ? "#5FA579" : "#C8553D";
    const fillColor = isUp ? "rgba(95,165,121,0.18)" : "rgba(200,85,61,0.16)";
    const path = `M${points.join(" L ")}`;
    const area = `${path} L ${W},${H} L 0,${H} Z`;
    host.innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <path d="${area}" fill="${fillColor}"/>
        <path d="${path}" fill="none" stroke="${color}" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/>
        <circle cx="${(closes.length - 1) * dx}" cy="${(H - ((last - min) / range) * (H - 6) - 3).toFixed(2)}" r="2.4" fill="${color}"/>
      </svg>`;
  }

  async function toggleResultExpand(card, sig, btn) {
    const isOpen = card.classList.toggle("is-expanded");
    btn.textContent = isOpen ? "Hide chart" : "Open chart";
    if (isOpen) {
      const host = card.querySelector("[data-chart-host]");
      await mountFullChart(sig.ticker, host);
    } else {
      const inst = state.chartInstances.get(sig.ticker);
      if (inst) { try { inst.remove(); } catch {} state.chartInstances.delete(sig.ticker); }
    }
  }

  async function mountFullChart(ticker, host) {
    if (!host || !window.LightweightCharts) return;
    host.innerHTML = "";
    const chart = LightweightCharts.createChart(host, {
      width: host.clientWidth,
      height: host.clientHeight,
      layout: { background: { color: "transparent" }, textColor: "#8A8579", fontFamily: "JetBrains Mono, ui-monospace, monospace" },
      grid: { vertLines: { color: "#1B2230" }, horzLines: { color: "#1B2230" } },
      crosshair: { mode: 0 },
      timeScale: { borderColor: "#262E3D", timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: "#262E3D" },
    });
    const series = chart.addCandlestickSeries({
      upColor: "#5FA579", downColor: "#C8553D",
      borderUpColor: "#5FA579", borderDownColor: "#C8553D",
      wickUpColor: "#5FA579", wickDownColor: "#C8553D",
    });
    state.chartInstances.set(ticker, chart);
    const out = await api.get(`/api/chart/${encodeURIComponent(ticker)}?days=180`);
    if (out.ok && Array.isArray(out.data?.candles)) {
      series.setData(out.data.candles);
      chart.timeScale().fitContent();
    } else {
      host.innerHTML = `<p class="muted small" style="padding:1rem">Could not load chart for ${escapeHtml(ticker)}.</p>`;
    }
    const ro = new ResizeObserver(() => { try { chart.resize(host.clientWidth, host.clientHeight); } catch {} });
    ro.observe(host);
  }

  // ---------- stage trade dialog ----------
  function openStageDialog(sig) {
    const d = $("#stageDialog");
    if (!d) return;
    $("#stageSummary").textContent = `${sig.ticker} · last ${sig.price != null ? fmtMoney(sig.price) : "—"} · score ${sig.score != null ? sig.score.toFixed(1) : "—"}`;
    $("#stageQty").value = "";
    $("#stageNote").value = "Queued from scan";
    d._draft = sig;
    d.showModal();
    const confirmBtn = $("#stageConfirmBtn");
    const handler = async (e) => {
      if (e.submitter && e.submitter.value !== "confirm") { d._draft = null; return; }
      e.preventDefault();
      await submitStage(sig);
    };
    d.onsubmit = handler;
  }

  async function submitStage(sig) {
    const d = $("#stageDialog");
    const qtyRaw = $("#stageQty").value.trim();
    const note = $("#stageNote").value.trim() || "Queued from scan";
    let qty = null;
    if (qtyRaw) {
      const n = parseInt(qtyRaw, 10);
      if (!Number.isFinite(n) || n < 1) { toast({ title: "Bad quantity", message: "Enter a positive integer.", sev: "warn" }); return; }
      qty = n;
    }
    const payload = { ticker: sig.ticker, price: sig.price, signal: sig.raw, note };
    if (qty != null) payload.qty = qty;
    const out = await api.post("/api/pending-trades", payload);
    if (!out.ok) {
      toast({ title: "Stage failed", message: out.error, sev: "error" });
      logActivity("trade", `Stage ${sig.ticker} failed: ${out.error}`, "error");
      return;
    }
    toast({ title: "Staged", message: `${sig.ticker} added to queue.`, sev: "ok" });
    logActivity("trade", `Staged ${sig.ticker}`);
    d.close();
    await refreshPending();
    updateHeroStats();
  }

  // =================================================================
  // VIEW: QUEUE
  // =================================================================
  function renderQueue() {
    $("#queueRefreshBtn")?.addEventListener("click", () => refreshPending().then(renderQueueList));
    $("#queueClearBtn")?.addEventListener("click", async () => {
      if (!confirm("Reject all pending trades? Already-executed orders are unaffected.")) return;
      const out = await api.post("/api/pending-trades/clear-pending", {});
      if (!out.ok) toast({ title: "Clear failed", message: out.error, sev: "error" });
      else { toast({ title: "Pending queue cleared", sev: "ok" }); await refreshPending(); renderQueueList(); }
    });
    $("#manualStageBtn")?.addEventListener("click", submitManualStage);
    $("#manualTicker")?.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submitManualStage(); } });
    refreshPending().then(renderQueueList);
  }

  async function submitManualStage() {
    const ticker = $("#manualTicker")?.value.trim().toUpperCase();
    const qtyRaw = $("#manualQty")?.value.trim();
    const note = $("#manualNote")?.value.trim() || "Manual staging";
    if (!ticker) { toast({ title: "Ticker required", message: "Enter a symbol to stage.", sev: "warn" }); return; }
    let qty = null;
    if (qtyRaw) {
      const n = parseInt(qtyRaw, 10);
      if (!Number.isFinite(n) || n < 1) { toast({ title: "Bad quantity", message: "Positive integer or blank.", sev: "warn" }); return; }
      qty = n;
    }
    const btn = $("#manualStageBtn");
    if (btn) btn.disabled = true;
    const payload = { ticker, note };
    if (qty != null) payload.qty = qty;
    const out = await api.post("/api/pending-trades", payload);
    if (btn) btn.disabled = false;
    if (!out.ok) {
      toast({ title: "Stage failed", message: out.error, sev: "error" });
      logActivity("trade", `Manual stage ${ticker} failed: ${out.error}`, "error");
      return;
    }
    toast({ title: "Staged", message: `${ticker} added to queue.`, sev: "ok" });
    logActivity("trade", `Manually staged ${ticker}`);
    if ($("#manualTicker")) $("#manualTicker").value = "";
    if ($("#manualQty")) $("#manualQty").value = "";
    if ($("#manualNote")) $("#manualNote").value = "";
    await refreshPending();
    renderQueueList();
    updateHeroStats();
  }

  async function refreshPending({ silent = false } = {}) {
    const out = await api.get("/api/pending-trades");
    if (out.ok) {
      const rows = out.data?.trades || out.data?.rows || (Array.isArray(out.data) ? out.data : []);
      state.pending = Array.isArray(rows) ? rows : [];
    } else if (!silent) {
      toast({ title: "Could not load queue", message: out.error, sev: "warn" });
    }
    updateHeroStats();
  }

  function renderQueueList() {
    const list = $("#queueList");
    const summary = $("#queueSummary");
    if (!list) return;
    list.innerHTML = "";
    const pending = state.pending.filter((r) => r.status === "pending");
    const executed = state.pending.filter((r) => r.status === "executed");
    const rejected = state.pending.filter((r) => ["rejected", "failed"].includes(r.status));
    if (summary) {
      summary.innerHTML = `
        <span>Pending<strong>${pending.length}</strong></span>
        <span>Executed<strong>${executed.length}</strong></span>
        <span>Rejected/Failed<strong>${rejected.length}</strong></span>
      `;
    }
    const ordered = [...pending, ...executed, ...rejected];
    if (!ordered.length) {
      list.innerHTML = `<div class="queue-empty"><p>No staged trades yet. Stage candidates from <a href="#/scans">Scans</a>.</p></div>`;
      return;
    }
    ordered.forEach((row) => list.appendChild(buildQueueRow(row)));
  }

  function buildQueueRow(row) {
    const sig = row.signal || {};
    const score = num(sig.signal_score ?? sig.score);
    const r = make("article", { class: "queue-row", "data-id": row.id });
    const sym = make("div", { class: "queue-symbol" });
    sym.appendChild(make("span", { class: "queue-ticker" }, row.ticker));
    sym.appendChild(make("span", { class: "queue-id" }, `#${safe(row.id)}`));
    r.appendChild(sym);

    r.appendChild(make("span", { class: "queue-num" }, row.qty != null ? `${row.qty} sh` : "auto"));
    r.appendChild(make("span", { class: "queue-num" }, sig.price != null ? fmtMoney(sig.price) : (row.price != null ? fmtMoney(row.price) : "—")));
    r.appendChild(make("span", { class: "queue-num" }, score != null ? `Score ${score.toFixed(1)}` : (sig.sector_etf || "—")));
    r.appendChild(make("span", { class: "queue-status", "data-s": row.status }, row.status));

    const actions = make("div", { class: "queue-actions" });
    if (row.status === "pending") {
      const approve = make("button", { class: "btn primary small", type: "button" }, "Approve");
      approve.addEventListener("click", () => openApproveDialog(row));
      const reject = make("button", { class: "btn ghost small", type: "button" }, "Reject");
      reject.addEventListener("click", () => rejectTrade(row.id));
      actions.appendChild(approve);
      actions.appendChild(reject);
    }
    const del = make("button", { class: "btn danger outline small", type: "button" }, "Delete");
    del.addEventListener("click", () => deleteTrade(row.id));
    actions.appendChild(del);
    r.appendChild(actions);
    return r;
  }

  function openApproveDialog(row) {
    const d = $("#approveDialog");
    if (!d) return;
    $("#approveSummary").textContent = `${row.ticker} · ${row.qty || "auto"} sh · live order`;
    $("#approveTickerInput").value = "";
    $("#approveOtpInput").value = "";
    d.showModal();
    d.onsubmit = async (e) => {
      if (e.submitter?.value === "cancel") return;
      e.preventDefault();
      const typed = $("#approveTickerInput").value.trim();
      const otp = $("#approveOtpInput").value.trim();
      if (typed.toUpperCase() !== row.ticker.toUpperCase()) {
        toast({ title: "Type the ticker", message: `Type ${row.ticker} to confirm.`, sev: "warn" });
        return;
      }
      const out = await api.post(`/api/trades/${row.id}/approve?confirm_live=true`, { typed_ticker: typed, otp_code: otp });
      if (!out.ok) {
        toast({ title: "Approval failed", message: out.error, sev: "error" });
        logActivity("trade", `Approve ${row.id} failed: ${out.error}`, "error");
      } else {
        toast({ title: "Order submitted", message: `${row.ticker} sent to Schwab.`, sev: "ok" });
        logActivity("trade", `Approved ${row.id} (${row.ticker})`);
      }
      d.close();
      await refreshPending();
      renderQueueList();
    };
  }

  async function rejectTrade(id) {
    const out = await api.post(`/api/trades/${id}/reject`, {});
    if (!out.ok) toast({ title: "Reject failed", message: out.error, sev: "error" });
    else toast({ message: `Trade ${id} rejected.`, sev: "warn" });
    await refreshPending();
    renderQueueList();
  }

  async function deleteTrade(id) {
    if (!confirm(`Permanently delete trade ${id}?`)) return;
    const out = await api.post(`/api/trades/${id}/delete`, {});
    if (!out.ok) toast({ title: "Delete failed", message: out.error, sev: "error" });
    else toast({ message: `Trade ${id} deleted.`, sev: "ok" });
    await refreshPending();
    renderQueueList();
  }

  // =================================================================
  // VIEW: PORTFOLIO
  // =================================================================
  function renderPortfolio() {
    $("#portfolioRefreshBtn")?.addEventListener("click", () => loadPortfolio());
    loadPortfolio();
  }

  async function loadPortfolio() {
    const [posOut, sectOut, riskOut] = await Promise.all([
      api.get("/api/portfolio"),
      api.get("/api/sectors"),
      api.get("/api/portfolio/risk"),
    ]);

    // ----- Positions table -----
    const tbody = $("#portfolioBody");
    const positions = posOut.ok ? (posOut.data?.positions || posOut.data?.rows || []) : [];
    if ($("#positionsTotal")) {
      const total = posOut.ok ? (posOut.data?.total_market_value ?? null) : null;
      $("#positionsTotal").textContent = total != null ? `· ${fmtMoney(total, 0)} market value` : "";
    }
    if (tbody) {
      tbody.innerHTML = "";
      if (!positions.length) {
        const msg = posOut.ok
          ? "No open positions in this account yet. Connect Schwab on the Settings page."
          : `Could not load positions: ${posOut.error}`;
        tbody.innerHTML = `<tr><td colspan="6" class="muted center">${escapeHtml(msg)}</td></tr>`;
      } else {
        positions.forEach((p) => {
          const tr = make("tr");
          tr.appendChild(make("td", {}, make("strong", {}, safe(p.symbol || p.ticker))));
          tr.appendChild(make("td", { class: "num" }, fmtNum(p.qty ?? p.quantity, 0)));
          tr.appendChild(make("td", { class: "num" }, p.last != null ? fmtMoney(p.last) : (p.price != null ? fmtMoney(p.price) : "—")));
          tr.appendChild(make("td", { class: "num" }, p.market_value != null ? fmtMoney(p.market_value, 0) : "—"));
          const dpl = p.day_pl;
          const tdDpl = make("td", { class: "num" });
          if (dpl != null && isFinite(dpl)) {
            tdDpl.textContent = `${dpl >= 0 ? "+" : ""}${fmtMoney(dpl, 2)}`;
            tdDpl.style.color = dpl >= 0 ? "var(--green)" : "var(--rust)";
          } else tdDpl.textContent = "—";
          tr.appendChild(tdDpl);
          const plPct = p.pl_pct ?? p.pnl_pct ?? p.unrealized_pl_pct;
          const tdPl = make("td", { class: "num" });
          if (plPct != null) {
            const norm = Math.abs(plPct) > 1 ? plPct : plPct * 100;
            tdPl.textContent = `${norm >= 0 ? "+" : ""}${norm.toFixed(2)}%`;
            tdPl.style.color = norm >= 0 ? "var(--green)" : "var(--rust)";
          } else tdPl.textContent = "—";
          tr.appendChild(tdPl);
          tbody.appendChild(tr);
        });
      }
    }

    // ----- Sector strength -----
    const sectGrid = $("#sectorGrid");
    if (sectGrid) {
      sectGrid.innerHTML = "";
      const sects = sectOut.ok ? (sectOut.data?.sectors || sectOut.data || []) : [];
      const list = Array.isArray(sects) ? sects : Object.entries(sects || {}).map(([name, val]) => ({ name, value: val }));
      if (!list.length) {
        sectGrid.innerHTML = `<div class="muted">${sectOut.ok ? "No sector data yet." : `Sector load failed: ${escapeHtml(sectOut.error || "")}`}</div>`;
      } else {
        const max = Math.max(...list.map((s) => Math.abs(s.relative_strength ?? s.rel ?? s.value ?? 0))) || 1;
        list.slice(0, 11).forEach((s) => {
          const v = s.relative_strength ?? s.rel ?? s.value ?? 0;
          const w = Math.min(100, (Math.abs(v) / max) * 100);
          const positive = v >= 0;
          const row = make("div", { class: "sector-row" });
          row.appendChild(make("span", { class: "sector-name" }, s.name || s.ticker || "—"));
          const bar = make("div", { class: "sector-bar" });
          const fill = make("div", { class: "sector-bar-fill" });
          fill.style.background = positive ? "var(--green)" : "var(--rust)";
          fill.style[positive ? "left" : "right"] = "50%";
          fill.style.width = (w / 2) + "%";
          bar.appendChild(fill);
          row.appendChild(bar);
          row.appendChild(make("span", { class: "sector-val" }, `${positive ? "+" : ""}${(v * (Math.abs(v) > 1 ? 1 : 100)).toFixed(1)}%`));
          sectGrid.appendChild(row);
        });
      }
    }

    // ----- Risk card + allocation + movers (upstream feature) -----
    renderRiskCard(riskOut);
  }

  function renderRiskCard(riskOut) {
    const card = $("#riskCard");
    const headline = $("#riskHeadline");
    const reason = $("#riskReason");
    const action = $("#riskAction");
    const priority = $("#riskPriority");
    const metrics = $("#riskMetrics");
    const allocHost = $("#allocationGrid");
    const moverHost = $("#dayPlMovers");
    if (!card) return;

    // Auth-error / recovery shape
    if (!riskOut.ok) {
      const rec = riskOut.data?.recovery;
      card.dataset.priority = "info";
      priority.textContent = "Action needed";
      headline.textContent = rec?.title || "Could not load risk analytics";
      reason.textContent = rec?.summary || riskOut.error || "Connect Schwab in Settings, then refresh.";
      action.textContent = rec?.fix_path || "";
      metrics.innerHTML = "";
      if (allocHost) allocHost.innerHTML = `<div class="muted">Allocation will load once positions are available.</div>`;
      if (moverHost) moverHost.innerHTML = `<li class="muted">Day P/L movers will load once positions are available.</li>`;
      return;
    }

    const d = riskOut.data || {};
    const rec = d.recommendation || {};
    const conc = d.concentration || {};

    // Recommendation block
    const prio = (rec.priority || "low").toLowerCase();
    card.dataset.priority = ["high", "medium", "low"].includes(prio) ? prio : "info";
    priority.textContent = prio === "high" ? "High priority" : prio === "medium" ? "Medium priority" : prio === "low" ? "Low priority" : "Info";
    headline.textContent = rec.headline || (d.position_count ? "Portfolio looks balanced" : "No positions to analyze");
    reason.textContent = rec.reason || (d.position_count ? "" : "Connect Schwab and add positions to see recommendations.");
    action.textContent = rec.suggested_action || "";

    // Metrics
    metrics.innerHTML = "";
    const addMetric = (label, value, hint) => {
      if (value == null) return;
      const m = make("div", { class: "risk-metric" });
      m.appendChild(make("span", {}, label));
      m.appendChild(make("strong", {}, value));
      if (hint) m.appendChild(make("small", {}, hint));
      metrics.appendChild(m);
    };
    if (d.position_count != null) addMetric("Positions", String(d.position_count));
    if (conc.top_position_pct != null) addMetric("Top position", `${conc.top_position_pct.toFixed(1)}%`, conc.largest_position_symbol || "");
    if (conc.top_5_pct != null) addMetric("Top 5", `${conc.top_5_pct.toFixed(1)}%`);
    if (conc.sector_count != null) addMetric("Sectors held", String(conc.sector_count));
    if (conc.hhi != null) addMetric("HHI", String(conc.hhi), conc.hhi_label || "");
    if (d.day_pl_total != null) {
      const sign = d.day_pl_total >= 0 ? "+" : "";
      addMetric("Day P/L", `${sign}${fmtMoney(d.day_pl_total, 2)}`, "today");
    }

    // Allocation bars (sector_allocation by weight_pct)
    if (allocHost) {
      allocHost.innerHTML = "";
      const sectors = Array.isArray(d.sector_allocation) ? d.sector_allocation : [];
      if (!sectors.length) {
        allocHost.innerHTML = `<div class="muted">No allocation data yet.</div>`;
      } else {
        const maxW = Math.max(1, ...sectors.map((s) => Number(s.weight_pct) || 0));
        sectors.slice(0, 12).forEach((s) => {
          const w = Number(s.weight_pct) || 0;
          const row = make("div", { class: "alloc-row" });
          row.appendChild(make("span", { class: "alloc-name" }, s.sector || s.name || "—"));
          const bar = make("div", { class: "alloc-bar" });
          const fill = make("div", { class: "alloc-fill" });
          fill.style.width = `${(w / maxW) * 100}%`;
          bar.appendChild(fill);
          row.appendChild(bar);
          row.appendChild(make("span", { class: "alloc-val" }, `${w.toFixed(1)}%`));
          allocHost.appendChild(row);
        });
      }
    }

    // Day P/L movers
    if (moverHost) {
      moverHost.innerHTML = "";
      const movers = Array.isArray(d.day_pl_breakdown) ? d.day_pl_breakdown : [];
      if (!movers.length) {
        moverHost.innerHTML = `<li class="muted">No day P/L movement to report.</li>`;
      } else {
        movers.slice(0, 8).forEach((m) => {
          const dpl = Number(m.day_pl) || 0;
          const li = make("li", { class: "mover" });
          li.appendChild(make("span", { class: "ticker" }, m.symbol || "—"));
          li.appendChild(make("span", { class: "muted small" }, m.contribution_pct != null ? `${m.contribution_pct.toFixed(2)}% of book` : ""));
          const pl = make("span", { class: `pl ${dpl >= 0 ? "up" : "down"}` });
          pl.textContent = `${dpl >= 0 ? "+" : ""}${fmtMoney(dpl, 2)}`;
          li.appendChild(pl);
          moverHost.appendChild(li);
        });
      }
    }
  }

  // =================================================================
  // VIEW: DIAGNOSTICS
  // =================================================================
  function renderDiagnostics() {
    $("#diagRefreshBtn")?.addEventListener("click", async () => {
      await refreshHealth();
      await refreshScanStatus();
      renderHealthTiles();
      renderFunnel();
      renderActivityLog();
    });
    renderHealthTiles();
    renderFunnel();
    renderActivityLog();
  }

  function renderHealthTiles() {
    const tiles = $$(".health-tile");
    if (!tiles.length) return;
    const map = {
      auth: () => {
        const a = (state.health?.account_token || "").toLowerCase();
        const m = (state.health?.market_token || "").toLowerCase();
        if ((a.includes("ok") || a === "valid") && (m.includes("ok") || m === "valid")) return { label: "Connected", state: "ok" };
        if (a.includes("ok") || m.includes("ok") || a === "valid" || m === "valid") return { label: "Partial", state: "warn" };
        return { label: "Disconnected", state: "bad" };
      },
      quotes: () => {
        const q = String(state.health?.quote_health || state.health?.quotes || "").toLowerCase();
        if (q.includes("ok") || q.includes("healthy") || q.includes("good")) return { label: "Healthy", state: "ok" };
        if (q.includes("degraded") || q.includes("slow")) return { label: "Degraded", state: "warn" };
        if (!q) return { label: "Unknown", state: "warn" };
        return { label: q.charAt(0).toUpperCase() + q.slice(1), state: "bad" };
      },
      api: () => {
        const r = state.health?.api_error_rate;
        const v = typeof r === "number" ? r : Number(r);
        if (!isFinite(v)) return { label: "—", state: "warn" };
        const pct = v > 1 ? v : v * 100;
        return { label: `${pct.toFixed(1)}%`, state: pct < 1 ? "ok" : pct < 5 ? "warn" : "bad" };
      },
      validation: () => {
        const v = String(state.health?.validation_health || "").toLowerCase();
        if (v.includes("ok") || v.includes("healthy")) return { label: "Healthy", state: "ok" };
        if (v.includes("warn") || v.includes("stale")) return { label: "Stale", state: "warn" };
        if (!v) return { label: "Unknown", state: "warn" };
        return { label: v.charAt(0).toUpperCase() + v.slice(1), state: "bad" };
      },
    };
    tiles.forEach((tile) => {
      const k = tile.dataset.key;
      const info = map[k]?.() || { label: "—", state: "warn" };
      tile.dataset.state = info.state;
      tile.querySelector(".ht-value").textContent = info.label;
    });
  }

  function renderFunnel() {
    const host = $("#diagFunnel");
    const blockHost = $("#diagBlockers");
    const f = state.lastScanFunnel;
    if (host) {
      host.innerHTML = "";
      if (!f || typeof f !== "object") {
        host.innerHTML = `<p class="muted">Run a scan to populate the funnel.</p>`;
      } else {
        Object.entries(f).forEach(([k, v]) => {
          const row = make("div", { class: "funnel-row" });
          row.appendChild(make("span", {}, k.replace(/_/g, " ")));
          row.appendChild(make("strong", {}, String(v)));
          host.appendChild(row);
        });
      }
    }
    if (blockHost) {
      blockHost.innerHTML = "";
      const blockers = Array.isArray(state.lastScanBlockers) ? state.lastScanBlockers : [];
      if (!blockers.length) {
        blockHost.innerHTML = `<li class="muted">No blockers recorded yet.</li>`;
      } else {
        blockers.slice(0, 12).forEach((b) => {
          const text = typeof b === "string" ? b : (b.label || b.message || JSON.stringify(b));
          blockHost.appendChild(make("li", {}, text));
        });
      }
    }
  }

  function renderActivityLog() {
    const log = $("#activityLog");
    if (!log) return;
    log.innerHTML = "";
    if (!state.activity.length) {
      log.innerHTML = `<li class="muted">Activity will appear here.</li>`;
      return;
    }
    state.activity.forEach((ev) => {
      const li = make("li", { "data-sev": ev.sev || "info" });
      li.appendChild(make("span", { class: "activity-time" }, fmtRelTime(ev.at)));
      li.appendChild(make("span", { class: "activity-kind" }, ev.kind));
      li.appendChild(make("span", { class: "activity-msg" }, ev.message));
      log.appendChild(li);
    });
  }

  // =================================================================
  // VIEW: SETTINGS
  // =================================================================
  function renderSettings() {
    renderOnboarding();
    renderPresetSummary();
    $("#presetApplyBtn")?.addEventListener("click", applyPreset);
    $("#presetSelect")?.addEventListener("change", renderPresetSummary);
    $("#saveJwtBtn")?.addEventListener("click", () => {
      const v = $("#jwtInput").value.trim();
      if (v) localStorage.setItem(api._tokenKey, v);
      else localStorage.removeItem(api._tokenKey);
      toast({ title: "Token saved", message: "Reload to apply.", sev: "ok" });
    });
    $("#saveApiKeyBtn")?.addEventListener("click", () => {
      const v = $("#apiKeyInput").value.trim();
      if (v) localStorage.setItem(api._apiKeyKey, v);
      else localStorage.removeItem(api._apiKeyKey);
      toast({ title: "API key saved", message: "Subsequent calls will include it.", sev: "ok" });
    });
    $("#dangerClearPendingBtn")?.addEventListener("click", async () => {
      if (!confirm("Reject every pending trade?")) return;
      const out = await api.post("/api/pending-trades/clear-pending", {});
      toast({ title: out.ok ? "Cleared" : "Failed", message: out.ok ? "Pending queue cleared." : out.error, sev: out.ok ? "ok" : "error" });
      await refreshPending();
    });
    $("#dangerDeleteAllTradesBtn")?.addEventListener("click", async () => {
      if (!confirm("Permanently delete ALL trade history? This cannot be undone.")) return;
      const out = await api.post("/api/pending-trades/delete-all", {});
      toast({ title: out.ok ? "Deleted" : "Failed", message: out.ok ? "Trade history cleared." : out.error, sev: out.ok ? "ok" : "error" });
      await refreshPending();
    });

    const jwt = localStorage.getItem(api._tokenKey);
    if (jwt) $("#jwtInput").placeholder = "(token saved — paste new to replace)";
    const ak = localStorage.getItem(api._apiKeyKey);
    if (ak) $("#apiKeyInput").placeholder = "(key saved — paste new to replace)";

    // populate OAuth links
    Promise.all([api.get("/api/oauth/schwab/authorize-url"), api.get("/api/oauth/schwab/market/authorize-url")])
      .then(([acct, mkt]) => {
        if (acct.ok && acct.data?.url) $("#oauthAccountLink").href = acct.data.url;
        if (mkt.ok && mkt.data?.url) $("#oauthMarketLink").href = mkt.data.url;
      });

    $("#onboardingNextBtn")?.addEventListener("click", runNextOnboardingStep);
  }

  // Backend tracks a single "connect" step but the UI splits it into account/market
  // so users see both OAuth flows. Account/market UI steps mirror connect's status.
  const ONBOARDING_VIRTUAL = { account: "connect", market: "connect" };

  function readStepStatus(stepStatus, k) {
    const real = ONBOARDING_VIRTUAL[k] || k;
    const raw = stepStatus[real];
    if (raw == null) return "pending";
    if (typeof raw === "string") return raw.toLowerCase();
    if (typeof raw === "object") {
      if (raw.ok === true) return "done";
      if (raw.ok === false && raw.at) return "failed";
      if (raw.status) return String(raw.status).toLowerCase();
    }
    return "pending";
  }

  function renderOnboarding() {
    const status = state.onboarding;
    const hint = $("#onboardingHint");
    const steps = $$(".setup-steps li");
    if (!status) {
      if (hint) hint.textContent = "Loading status…";
      return;
    }
    const stepStatus = status.steps || {};
    let nextKey = null;
    steps.forEach((li) => {
      const k = li.dataset.step;
      const s = readStepStatus(stepStatus, k);
      let label = s;
      let mark = "pending";
      if (s === "ok" || s === "done" || s === "complete" || s === "completed") { mark = "done"; label = "done"; }
      else if (s === "running" || s === "in_progress") { mark = "current"; label = "running"; }
      else if (s === "failed" || s === "error") { mark = "failed"; label = "failed"; }
      else if (!nextKey) { mark = "current"; nextKey = k; }
      li.dataset.state = mark;
      li.querySelector(".step-state").textContent = label;
    });
    if (hint) {
      if (status.complete || status.done) hint.textContent = "Setup complete — Schwab is connected and verified.";
      else if (nextKey) {
        const label = ({
          account: "Authorize Schwab account access",
          market: "Authorize Schwab market data",
          verify_token_health: "Verify token health",
          test_scan: "Run a test scan",
          test_paper_order: "Place a paper order",
        })[nextKey] || nextKey.replace(/_/g, " ");
        hint.textContent = `Next: ${label}.`;
      }
      else hint.textContent = "Run remaining steps to finish setup.";
    }
    const btn = $("#onboardingNextBtn");
    if (btn) {
      btn.dataset.step = nextKey || "";
      btn.disabled = !nextKey;
      // For OAuth steps, label the button as Authorize and let the click handler open the URL.
      if (nextKey === "account" || nextKey === "market") btn.textContent = "Open Schwab authorization";
      else btn.textContent = "Run next step";
    }
  }

  async function runNextOnboardingStep() {
    const btn = $("#onboardingNextBtn");
    const step = btn?.dataset.step;
    if (!step) return;
    if (step === "account" || step === "market") {
      const link = step === "account" ? $("#oauthAccountLink") : $("#oauthMarketLink");
      const url = link?.getAttribute("href");
      if (url && url !== "#") window.open(url, "_blank", "noopener");
      else toast({ title: "OAuth URL not ready", message: "Refresh and try again in a moment.", sev: "warn" });
      return;
    }
    btn.disabled = true;
    const out = await api.post(`/api/onboarding/step/${encodeURIComponent(step)}`, {});
    if (!out.ok) toast({ title: "Step failed", message: out.error, sev: "error" });
    else toast({ message: `Step "${step}" finished.`, sev: "ok" });
    await refreshOnboarding();
    await refreshHealth();
    renderOnboarding();
    renderHealthTiles();
    updateSchwabPill();
  }

  function renderPresetSummary() {
    const sel = $("#presetSelect");
    const host = $("#presetSummary");
    if (!sel || !host) return;
    const t = PRESET_TIERS[sel.value];
    host.innerHTML = "";
    if (!t) return;
    Object.entries(t.env).forEach(([k, v]) => {
      const li = make("li");
      li.appendChild(make("span", {}, prettyEnvKey(k)));
      li.appendChild(make("strong", {}, v));
      host.appendChild(li);
    });
  }
  function prettyEnvKey(k) {
    return ({
      POSITION_SIZE_USD: "Position size",
      MAX_TRADES_PER_DAY: "Daily cap",
      QUALITY_GATES_MODE: "Quality gates",
      EVENT_RISK_MODE: "Event-risk mode",
      EVENT_ACTION: "Event action",
      EXEC_QUALITY_MODE: "Execution gates",
    })[k] || k;
  }

  async function applyPreset() {
    const sel = $("#presetSelect");
    if (!sel) return;
    const profile = sel.value;
    const automation = $("#automationOptIn")?.checked ? "true" : "false";
    const qs = `profile=${encodeURIComponent(profile)}&mode=standard&automation_opt_in=${automation}`;
    const out = await api.post(`/api/settings/profile?${qs}`, {});
    if (!out.ok) toast({ title: "Apply failed", message: out.error, sev: "error" });
    else { toast({ title: "Preset applied", message: `${PRESET_TIERS[profile].label} active.`, sev: "ok" }); logActivity("config", `Preset → ${profile}`); }
  }

  // =================================================================
  // VIEW: RESEARCH (ticker check, decision, report, SEC, backtest, recovery)
  // =================================================================
  function renderResearch() {
    // Tab switching
    $$(".tabbar .tab").forEach((tab) => {
      tab.addEventListener("click", () => activateResearchTab(tab.dataset.tab));
    });
    activateResearchTab("check");

    // Ticker check
    $("#checkRunBtn")?.addEventListener("click", () => researchCheck());
    bindEnter("#checkTicker", researchCheck);

    // Decision card
    $("#decisionRunBtn")?.addEventListener("click", () => researchDecision());
    bindEnter("#decisionTicker", researchDecision);

    // Full report
    $("#reportRunBtn")?.addEventListener("click", () => researchReport());
    bindEnter("#reportTicker", researchReport);

    // SEC compare
    $("#secRunBtn")?.addEventListener("click", () => researchSec());
    $$(".sec-preset").forEach((b) => {
      b.addEventListener("click", () => {
        $("#secTickerA").value = b.dataset.a || "";
        $("#secTickerB").value = b.dataset.b || "";
        researchSec();
      });
    });

    // Backtest
    $("#btQueueBtn")?.addEventListener("click", () => researchBacktestQueue());
    $("#btRefreshBtn")?.addEventListener("click", () => researchBacktestList());
    $$(".bt-preset").forEach((b) => {
      b.addEventListener("click", () => {
        const years = Number(b.dataset.years) || 5;
        const end = new Date();
        const start = new Date(); start.setFullYear(start.getFullYear() - years);
        $("#btEnd").value = isoDate(end);
        $("#btStart").value = isoDate(start);
      });
    });

    // Recovery
    $("#recRunBtn")?.addEventListener("click", () => researchRecovery());
    bindEnter("#recMessage", researchRecovery);

    researchBacktestList({ silent: true });
  }

  function bindEnter(sel, fn) {
    $(sel)?.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); fn(); } });
  }
  function isoDate(d) { return d.toISOString().slice(0, 10); }

  function activateResearchTab(name) {
    $$(".tabbar .tab").forEach((t) => t.setAttribute("aria-selected", t.dataset.tab === name ? "true" : "false"));
    $$(".tabpanel").forEach((p) => { p.hidden = p.dataset.tab !== name; });
  }

  function setBusy(host, label = "Loading…") {
    if (!host) return;
    host.innerHTML = `<p class="muted"><span class="scan-progress-spin" style="display:inline-block;vertical-align:middle;margin-right:8px"></span>${escapeHtml(label)}</p>`;
  }
  function setError(host, err) {
    if (!host) return;
    host.innerHTML = `<p class="muted" style="color:var(--rust)">${escapeHtml(err || "Request failed.")}</p>`;
  }
  function rowItem(label, value) {
    const row = make("div", { class: "research-row" });
    row.appendChild(make("span", {}, label));
    row.appendChild(make("strong", {}, value == null || value === "" ? "—" : String(value)));
    return row;
  }
  function rawJsonBlock(obj) {
    const det = make("details");
    det.appendChild(make("summary", {}, "raw response"));
    det.appendChild(make("pre", {}, JSON.stringify(obj, null, 2)));
    return det;
  }

  // ---- Ticker check ----
  // Endpoint returns Discord-embed shape: {title, description, color, fields[]}.
  function mdInline(s) {
    return escapeHtml(String(s ?? ""))
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br/>");
  }
  async function researchCheck() {
    const t = $("#checkTicker")?.value.trim().toUpperCase();
    const host = $("#checkOutput");
    if (!t) { setError(host, "Enter a ticker."); return; }
    setBusy(host, `Checking ${t}…`);
    const out = await api.get(`/api/check/${encodeURIComponent(t)}`);
    if (!out.ok) { setError(host, out.error); return; }
    const d = out.data || {};
    host.innerHTML = "";
    if (d.title) {
      const head = make("div", { class: "research-section" });
      head.appendChild(make("h4", {}, d.title.trim()));
      if (d.description) head.appendChild(make("p", { html: mdInline(d.description) }));
      host.appendChild(head);
    }
    if (Array.isArray(d.fields) && d.fields.length) {
      const grid = make("div", { class: "research-summary" });
      d.fields.forEach((f) => {
        const row = make("div", { class: "research-row" });
        row.appendChild(make("span", {}, f.name || ""));
        const strong = make("strong", {});
        strong.innerHTML = mdInline(f.value || "");
        row.appendChild(strong);
        grid.appendChild(row);
      });
      host.appendChild(grid);
    }
    if (d.footer?.text) {
      host.appendChild(make("p", { class: "muted small" }, d.footer.text));
    }
    host.appendChild(rawJsonBlock(d));
  }

  // ---- Decision card ----
  async function researchDecision() {
    const t = $("#decisionTicker")?.value.trim().toUpperCase();
    const host = $("#decisionOutput");
    if (!t) { setError(host, "Enter a ticker."); return; }
    setBusy(host, `Loading decision for ${t}…`);
    const out = await api.get(`/api/decision-card/${encodeURIComponent(t)}`);
    if (!out.ok) { setError(host, out.error); return; }
    const d = out.data || {};
    host.innerHTML = "";
    const verdict = d.verdict || d.recommendation || d.action || "—";
    const reasons = d.reasons || d.notes || d.checklist || [];
    const summary = make("div", { class: "research-summary" });
    summary.appendChild(rowItem("Verdict", String(verdict).toUpperCase()));
    if (d.score != null) summary.appendChild(rowItem("Score", fmtNum(d.score, 1)));
    if (d.confidence) summary.appendChild(rowItem("Confidence", d.confidence));
    if (d.position_size_usd != null) summary.appendChild(rowItem("Suggested size", fmtMoney(d.position_size_usd)));
    host.appendChild(summary);
    if (Array.isArray(reasons) && reasons.length) {
      const sec = make("div", { class: "research-section" });
      sec.appendChild(make("h4", {}, "Reasoning"));
      const ul = make("ul", { class: "bullets" });
      reasons.forEach((r) => ul.appendChild(make("li", {}, typeof r === "string" ? r : (r.label || r.text || JSON.stringify(r)))));
      sec.appendChild(ul);
      host.appendChild(sec);
    }
    host.appendChild(rawJsonBlock(d));
  }

  // ---- Full report ----
  async function researchReport() {
    const t = $("#reportTicker")?.value.trim().toUpperCase();
    const sec = $("#reportSection")?.value || "";
    const skipMiro = $("#reportSkipMiro")?.checked ? "true" : "false";
    const skipEdgar = $("#reportSkipEdgar")?.checked ? "true" : "false";
    const host = $("#reportOutput");
    if (!t) { setError(host, "Enter a ticker."); return; }
    const qs = new URLSearchParams();
    if (sec) qs.set("section", sec);
    qs.set("skip_mirofish", skipMiro);
    qs.set("skip_edgar", skipEdgar);
    setBusy(host, `Building report for ${t}… this can take a few seconds.`);
    const out = await api.get(`/api/report/${encodeURIComponent(t)}?${qs}`);
    if (!out.ok) { setError(host, out.error); return; }
    const d = out.data || {};
    host.innerHTML = "";
    const v2 = d.report_v2 || {};
    const headline = v2.headline || d.headline || d.summary;
    if (headline) {
      const h = make("div", { class: "research-section" });
      h.appendChild(make("h4", {}, "Headline verdict"));
      h.appendChild(make("p", {}, typeof headline === "string" ? headline : (headline.text || headline.summary || JSON.stringify(headline))));
      host.appendChild(h);
    }
    const verdicts = d.section_verdicts || {};
    if (verdicts && Object.keys(verdicts).length) {
      const sv = make("div", { class: "research-section" });
      sv.appendChild(make("h4", {}, "Section verdicts"));
      const wrap = make("div", { class: "research-summary" });
      Object.entries(verdicts).forEach(([k, v]) => {
        const text = typeof v === "string" ? v : (v?.label || v?.verdict || JSON.stringify(v));
        wrap.appendChild(rowItem(k, text));
      });
      sv.appendChild(wrap);
      host.appendChild(sv);
    }
    ["tech", "dcf", "comps", "health", "edgar", "mirofish"].forEach((k) => {
      const sub = d[k];
      if (!sub || (typeof sub === "object" && Object.keys(sub).length === 0)) return;
      const card = make("div", { class: "research-section" });
      card.appendChild(make("h4", {}, k.toUpperCase()));
      const summary = sub.summary || sub.takeaway || sub.headline;
      if (summary) card.appendChild(make("p", {}, String(summary)));
      const det = make("details");
      det.appendChild(make("summary", {}, "details"));
      det.appendChild(make("pre", {}, JSON.stringify(sub, null, 2)));
      card.appendChild(det);
      host.appendChild(card);
    });
    host.appendChild(rawJsonBlock(d));
  }

  // ---- SEC compare ----
  async function researchSec() {
    const mode = $("#secMode")?.value || "ticker_vs_ticker";
    const a = $("#secTickerA")?.value.trim().toUpperCase();
    const b = $("#secTickerB")?.value.trim().toUpperCase();
    const form = $("#secForm")?.value || "10-K";
    const changes = $("#secChangesOnly")?.checked ? "true" : "false";
    const host = $("#secOutput");
    if (!a) { setError(host, "Ticker A required."); return; }
    if (mode === "ticker_vs_ticker" && !b) { setError(host, "Ticker B required for ticker_vs_ticker mode."); return; }
    setBusy(host, `Comparing ${a}${b ? " · " + b : ""} (${form})…`);
    const qs = new URLSearchParams({ mode, ticker_a: a, ticker_b: b || "", form_type: form, changes_only: changes });
    const out = await api.get(`/api/sec/compare?${qs}`);
    if (!out.ok) { setError(host, out.error); return; }
    const d = out.data || {};
    host.innerHTML = "";
    const headline = d.headline || d.summary;
    if (headline) {
      const h = make("div", { class: "research-section" });
      h.appendChild(make("h4", {}, "Headline"));
      h.appendChild(make("p", {}, typeof headline === "string" ? headline : (headline.text || headline.summary || JSON.stringify(headline))));
      host.appendChild(h);
    }
    if (d.narrative) {
      const n = make("div", { class: "research-section" });
      n.appendChild(make("h4", {}, "Narrative"));
      n.appendChild(make("p", {}, String(d.narrative)));
      host.appendChild(n);
    }
    if (Array.isArray(d.material_changes) && d.material_changes.length) {
      const mc = make("div", { class: "research-section" });
      mc.appendChild(make("h4", {}, "Material changes"));
      const ul = make("ul", { class: "bullets" });
      d.material_changes.forEach((c) => ul.appendChild(make("li", {}, typeof c === "string" ? c : (c.label || c.summary || JSON.stringify(c)))));
      mc.appendChild(ul);
      host.appendChild(mc);
    }
    host.appendChild(rawJsonBlock(d));
  }

  // ---- Backtest ----
  async function researchBacktestQueue() {
    const universe = $("#btUniverse")?.value || "watchlist";
    const tickersRaw = $("#btTickers")?.value.trim() || "";
    const start = $("#btStart")?.value;
    const end = $("#btEnd")?.value;
    const theory = $("#btTheory")?.value.trim() || null;
    const slippage = Number($("#btSlippage")?.value) || 15;
    const feeShare = Number($("#btFeeShare")?.value) || 0.005;
    const minFee = Number($("#btMinFee")?.value) || 1;
    const maxAdv = Number($("#btMaxAdv")?.value) || 0.02;
    const qg = $("#btQualityGates")?.value || "";
    const skipMiro = $("#btSkipMiro")?.checked || false;
    const host = $("#btOutput");
    if (!start || !end) { setError(host, "Pick a start and end date."); return; }

    const overrides = {};
    if (qg) overrides.quality_gates_mode = qg;
    if (skipMiro) overrides.skip_mirofish = true;

    const body = {
      schema_version: 1,
      theory_name: theory,
      universe_mode: universe,
      tickers: universe === "tickers"
        ? tickersRaw.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean)
        : [],
      start_date: start,
      end_date: end,
      slippage_bps_per_side: slippage,
      fee_per_share: feeShare,
      min_fee_per_order: minFee,
      max_adv_participation: maxAdv,
      overrides: Object.keys(overrides).length ? overrides : null,
    };
    setBusy(host, "Queueing backtest…");
    const out = await api.post("/api/backtest-runs", body);
    if (!out.ok) {
      const hint = state.publicConfig.saas_mode ? "" : " (Backtest queue is available in SaaS mode.)";
      setError(host, (out.error || "Could not queue backtest.") + hint);
      return;
    }
    host.innerHTML = "";
    const sec = make("div", { class: "research-section" });
    sec.appendChild(make("h4", {}, "Queued"));
    sec.appendChild(make("p", {}, `Backtest task ${out.data?.task_id || "—"} accepted.`));
    host.appendChild(sec);
    host.appendChild(rawJsonBlock(out.data || {}));
    logActivity("backtest", `Queued backtest (${start} → ${end})`);
    researchBacktestList({ silent: true });
  }

  async function researchBacktestList({ silent = false } = {}) {
    const list = $("#btRunList");
    if (!list) return;
    list.innerHTML = "";
    const out = await api.get("/api/backtest-runs?limit=10");
    if (!out.ok) {
      if (!silent) list.appendChild(make("li", { class: "muted" }, `Could not list runs: ${out.error}`));
      return;
    }
    const runs = out.data?.runs || out.data || [];
    if (!Array.isArray(runs) || !runs.length) {
      list.appendChild(make("li", { class: "muted" }, "No backtest runs yet."));
      return;
    }
    runs.slice(0, 10).forEach((r) => {
      const label = `${r.theory_name || r.task_id || "run"} — ${r.start_date || "?"} → ${r.end_date || "?"} · ${r.status || "queued"}`;
      list.appendChild(make("li", {}, label));
    });
  }

  // ---- Recovery ----
  // Backend signature: GET /api/recovery/map?error=<msg>&source=<src>
  // Response: {source, code, title, summary, fix_path, action, raw_error}
  async function researchRecovery() {
    const source = $("#recSource")?.value || "schwab_auth";
    const message = $("#recMessage")?.value.trim() || "";
    const host = $("#recOutput");
    if (!message) { setError(host, "Paste an error message."); return; }
    setBusy(host, "Mapping fix path…");
    const qs = new URLSearchParams({ source, error: message });
    const out = await api.get(`/api/recovery/map?${qs}`);
    if (!out.ok) { setError(host, out.error); return; }
    const d = out.data || {};
    host.innerHTML = "";
    if (d.title || d.summary) {
      const sec = make("div", { class: "research-section" });
      sec.appendChild(make("h4", {}, d.title || "Diagnosis"));
      if (d.summary) sec.appendChild(make("p", {}, d.summary));
      host.appendChild(sec);
    }
    if (d.fix_path) {
      const sec = make("div", { class: "research-section" });
      sec.appendChild(make("h4", {}, "Fix path"));
      const fix = Array.isArray(d.fix_path) ? d.fix_path : [d.fix_path];
      const ol = make("ol", { class: "bullets" });
      fix.forEach((s) => ol.appendChild(make("li", {}, typeof s === "string" ? s : JSON.stringify(s))));
      sec.appendChild(ol);
      host.appendChild(sec);
    }
    const summaryRow = make("div", { class: "research-summary" });
    if (d.code)   summaryRow.appendChild(rowItem("Code",   d.code));
    if (d.action) summaryRow.appendChild(rowItem("Action", d.action));
    if (d.source) summaryRow.appendChild(rowItem("Source", d.source));
    if (summaryRow.children.length) host.appendChild(summaryRow);
    host.appendChild(rawJsonBlock(d));
  }

  // =================================================================
  // DIAGNOSTICS — performance / learning / calibration / validation
  // =================================================================
  function wireDiagnosticsExtras() {
    $("#performanceRefreshBtn")?.addEventListener("click", loadPerformance);
    $("#evolveBtn")?.addEventListener("click", runPostMortem);
    $("#challengerBtn")?.addEventListener("click", runChallenger);
    $("#calibrationRefreshBtn")?.addEventListener("click", loadCalibration);
    loadValidation();
  }

  async function loadValidation() {
    const host = $("#validationPanel");
    if (!host) return;
    setBusy(host, "Loading validation status…");
    const out = await api.get("/api/validation/status");
    if (!out.ok) { setError(host, out.error); return; }
    const d = out.data || {};
    host.innerHTML = "";
    const wrap = make("div", { class: "research-summary" });
    wrap.appendChild(rowItem("Health", d.health || d.status || "Unknown"));
    if (d.last_run_at) wrap.appendChild(rowItem("Last run", fmtRelTime(d.last_run_at)));
    if (d.profile) wrap.appendChild(rowItem("Profile", d.profile));
    if (d.progress) wrap.appendChild(rowItem("Progress", typeof d.progress === "string" ? d.progress : JSON.stringify(d.progress)));
    host.appendChild(wrap);
    host.appendChild(rawJsonBlock(d));
  }

  async function loadPerformance() {
    const host = $("#performancePanel");
    if (!host) return;
    setBusy(host, "Loading performance…");
    const out = await api.get("/api/performance");
    if (!out.ok) { setError(host, out.error); return; }
    const d = out.data || {};
    host.innerHTML = "";
    const buckets = ["live", "shadow", "paper", "backtest"];
    const wrap = make("div", { class: "research-summary" });
    buckets.forEach((b) => {
      const v = d[b];
      if (!v) return;
      const text = typeof v === "object"
        ? `n=${v.n_trades ?? v.count ?? "—"} · win ${v.win_rate != null ? fmtPct(v.win_rate, 1) : "—"} · pnl ${v.total_pnl != null ? fmtMoney(v.total_pnl, 0) : "—"}`
        : String(v);
      wrap.appendChild(rowItem(b.charAt(0).toUpperCase() + b.slice(1), text));
    });
    if (!wrap.children.length) wrap.appendChild(rowItem("Snapshot", "No performance data yet."));
    host.appendChild(wrap);
    host.appendChild(rawJsonBlock(d));
  }

  async function runPostMortem() {
    const host = $("#learningPanel");
    setBusy(host, "Running post-mortem analysis…");
    const out = await api.post("/api/evolve/run", {});
    if (!out.ok) { setError(host, out.error); return; }
    renderLearningOutput(out.data, "Post-mortem");
  }

  async function runChallenger() {
    const host = $("#learningPanel");
    setBusy(host, "Running challenger scan…");
    const out = await api.post("/api/challenger/run", {});
    if (!out.ok) { setError(host, out.error); return; }
    renderLearningOutput(out.data, "Challenger");
  }

  function renderLearningOutput(d, title) {
    const host = $("#learningPanel");
    if (!host) return;
    host.innerHTML = "";
    const sec = make("div", { class: "research-section" });
    sec.appendChild(make("h4", {}, title));
    sec.appendChild(make("p", {}, d?.summary || d?.message || `Run complete.`));
    host.appendChild(sec);
    host.appendChild(rawJsonBlock(d || {}));
  }

  async function loadCalibration() {
    const host = $("#calibrationPanel");
    if (!host) return;
    setBusy(host, "Loading calibration ledger…");
    const out = await api.get("/api/calibration/summary");
    if (!out.ok) { setError(host, out.error); return; }
    const d = out.data || {};
    host.innerHTML = "";
    const wrap = make("div", { class: "research-summary" });
    if (d.brier != null) wrap.appendChild(rowItem("Brier", fmtNum(d.brier, 4)));
    if (d.log_loss != null) wrap.appendChild(rowItem("Log loss", fmtNum(d.log_loss, 4)));
    if (d.n_samples != null) wrap.appendChild(rowItem("Samples", String(d.n_samples)));
    if (d.last_updated_at) wrap.appendChild(rowItem("Updated", fmtRelTime(d.last_updated_at)));
    if (Array.isArray(d.hypotheses) && d.hypotheses.length) {
      wrap.appendChild(rowItem("Active hypotheses", String(d.hypotheses.length)));
    }
    if (!wrap.children.length) wrap.appendChild(rowItem("Status", d.message || "No calibration snapshot yet."));
    host.appendChild(wrap);
    host.appendChild(rawJsonBlock(d));
  }

  // -- inject diagnostics extras into renderDiagnostics
  const _origRenderDiagnostics = renderDiagnostics;
  renderDiagnostics = function() {
    _origRenderDiagnostics();
    wireDiagnosticsExtras();
  };
  ROUTES.diagnostics.render = renderDiagnostics;

  // =================================================================
  // SETTINGS — live trading, 2FA, billing, halt
  // =================================================================
  function wireSettingsExtras() {
    const saas = !!state.publicConfig.saas_mode;
    if (saas) {
      $("#liveTradingCard")?.removeAttribute("hidden");
      $("#twoFaCard")?.removeAttribute("hidden");
      $("#billingCard")?.removeAttribute("hidden");
    }
    refreshAccountMe().then(renderLiveTradingState);
    if (saas) {
      refreshTwoFaStatus().then(renderTwoFaState);
      refreshBillingStatus().then(renderBillingState);
    }

    $("#enableLiveBtn")?.addEventListener("click", submitEnableLiveTrading);
    $("#saveHaltBtn")?.addEventListener("click", submitHaltUpdate);
    $("#twoFaSetupBtn")?.addEventListener("click", twoFaSetup);
    $("#twoFaEnableBtn")?.addEventListener("click", twoFaEnable);
    $("#twoFaDisableBtn")?.addEventListener("click", twoFaDisable);
    $("#billingCheckoutBtn")?.addEventListener("click", billingCheckout);
    $("#billingPortalBtn")?.addEventListener("click", billingPortal);
  }

  async function refreshAccountMe() {
    if (!state.publicConfig.saas_mode) return;
    const out = await api.get("/api/me");
    if (out.ok && out.data) state.user = out.data;
    updateUserChip();
    updateSchwabPill();
  }

  function renderLiveTradingState() {
    const me = state.user || {};
    const line = $("#liveTradingStatusLine");
    if (line) {
      if (me.live_execution_enabled) line.textContent = "Live trading is ENABLED. Approvals will route real orders to Schwab.";
      else line.textContent = "Live trading is OFF. Approvals are blocked until you enable below.";
    }
    if ($("#haltCheckbox")) $("#haltCheckbox").checked = !!me.trading_halted;
  }

  async function submitEnableLiveTrading() {
    const ack = $("#enableLiveAck")?.checked || false;
    const phrase = $("#enableLivePhrase")?.value.trim() || "";
    if (!ack) { toast({ title: "Acknowledge first", message: "Tick the risk acknowledgement.", sev: "warn" }); return; }
    if (phrase !== "ENABLE") { toast({ title: "Type ENABLE", message: "Type the literal word ENABLE to confirm.", sev: "warn" }); return; }
    const btn = $("#enableLiveBtn"); if (btn) btn.disabled = true;
    const out = await api.post("/api/settings/enable-live-trading", { risk_acknowledged: ack, typed_phrase: phrase });
    if (btn) btn.disabled = false;
    if (!out.ok) { toast({ title: "Enable failed", message: out.error, sev: "error" }); return; }
    toast({ title: "Live trading enabled", message: "You can now approve pending trades.", sev: "ok" });
    logActivity("system", "Live trading enabled");
    if ($("#enableLivePhrase")) $("#enableLivePhrase").value = "";
    await refreshAccountMe();
    renderLiveTradingState();
    await refreshPending();
    renderQueueList?.();
  }

  async function submitHaltUpdate() {
    const halted = $("#haltCheckbox")?.checked || false;
    const out = await api.patch("/api/settings/trading-halt", { halted });
    if (!out.ok) { toast({ title: "Halt update failed", message: out.error || "", sev: "error" }); return; }
    toast({ title: halted ? "Trading paused" : "Trading resumed", sev: "ok" });
    logActivity("system", `Trading halt ${halted ? "set" : "cleared"}`);
    await refreshAccountMe();
    renderLiveTradingState();
  }

  // 2FA
  async function refreshTwoFaStatus() {
    if (!state.publicConfig.saas_mode) return;
    const out = await api.get("/api/security/2fa/status");
    state.twoFa = out.ok ? out.data : null;
  }
  function renderTwoFaState() {
    const line = $("#twoFaStatusLine");
    if (!line) return;
    const t = state.twoFa || {};
    if (t.enabled) line.textContent = `2FA enabled${t.high_value_threshold_usd ? ` · threshold ${fmtMoney(t.high_value_threshold_usd, 0)}` : ""}.`;
    else line.textContent = "2FA disabled. High-value approvals will be blocked until enabled.";
  }
  async function twoFaSetup() {
    const out = await api.post("/api/security/2fa/setup", {});
    if (!out.ok) { toast({ title: "2FA setup failed", message: out.error, sev: "error" }); return; }
    const pre = $("#twoFaSecret");
    if (pre) {
      pre.hidden = false;
      pre.textContent = `Secret: ${out.data?.secret || "—"}\nAdd to your authenticator, then enter a code to enable.\nURI:\n${out.data?.otpauth_uri || "—"}`;
    }
    toast({ title: "Secret generated", message: "Add to authenticator and enter a code.", sev: "ok" });
  }
  async function twoFaEnable() {
    const code = $("#twoFaCode")?.value.trim();
    if (!code) { toast({ title: "Code required", sev: "warn" }); return; }
    const out = await api.post("/api/security/2fa/enable", { otp_code: code });
    if (!out.ok) { toast({ title: "Enable failed", message: out.error, sev: "error" }); return; }
    toast({ title: "2FA enabled", sev: "ok" });
    await refreshTwoFaStatus();
    renderTwoFaState();
    if ($("#twoFaCode")) $("#twoFaCode").value = "";
  }
  async function twoFaDisable() {
    const code = $("#twoFaCode")?.value.trim();
    if (!code) { toast({ title: "Enter current code to disable", sev: "warn" }); return; }
    if (!confirm("Disable 2FA on this account?")) return;
    const out = await api.post("/api/security/2fa/disable", { otp_code: code });
    if (!out.ok) { toast({ title: "Disable failed", message: out.error, sev: "error" }); return; }
    toast({ title: "2FA disabled", sev: "warn" });
    await refreshTwoFaStatus();
    renderTwoFaState();
    if ($("#twoFaCode")) $("#twoFaCode").value = "";
  }

  // Billing
  async function refreshBillingStatus() {
    // No dedicated GET — derive from /api/me
    if (!state.publicConfig.saas_mode) return;
    if (!state.user) await refreshAccountMe();
  }
  function renderBillingState() {
    const line = $("#billingStatusLine");
    if (!line) return;
    const u = state.user || {};
    const status = u.subscription_status || u.billing_status || "unknown";
    line.textContent = `Subscription: ${status}.`;
  }
  function billingCallbackUrls() {
    const origin = location.origin;
    return { success_url: `${origin}/#/settings`, cancel_url: `${origin}/#/settings`, return_url: `${origin}/#/settings` };
  }
  async function billingCheckout() {
    const out = await api.post("/api/billing/checkout-session", billingCallbackUrls());
    if (!out.ok) { toast({ title: "Checkout failed", message: out.error, sev: "error" }); return; }
    const url = out.data?.url || out.data?.checkout_url;
    if (url) window.location.href = url;
  }
  async function billingPortal() {
    const out = await api.post("/api/billing/portal-session", billingCallbackUrls());
    if (!out.ok) { toast({ title: "Portal failed", message: out.error, sev: "error" }); return; }
    const url = out.data?.url || out.data?.portal_url;
    if (url) window.location.href = url;
  }

  // Hook settings extras into renderSettings
  const _origRenderSettings = renderSettings;
  renderSettings = function() {
    _origRenderSettings();
    wireSettingsExtras();
  };
  ROUTES.settings.render = renderSettings;

  // =================================================================
  // NOTIFICATIONS — bell + panel
  // =================================================================
  state.notifications = [];
  state.notifSeen = 0;
  // Wrap toast to also push into notification list
  const _origToast = toast;
  toast = function(opts = {}) {
    _origToast(opts);
    if (opts.title || opts.message) {
      state.notifications.unshift({ at: new Date().toISOString(), title: opts.title || "", message: opts.message || "", sev: opts.sev || "info" });
      state.notifications = state.notifications.slice(0, 50);
      renderNotifBadge();
      renderNotifList();
    }
  };

  function renderNotifBadge() {
    const badge = $("#notifBadge");
    if (!badge) return;
    const unseen = state.notifications.length - state.notifSeen;
    if (unseen > 0) {
      badge.hidden = false;
      badge.textContent = unseen > 9 ? "9+" : String(unseen);
    } else {
      badge.hidden = true;
    }
  }
  function renderNotifList() {
    const list = $("#notifList");
    if (!list) return;
    if (!state.notifications.length) {
      list.innerHTML = `<li class="muted">No notifications yet.</li>`;
      return;
    }
    list.innerHTML = "";
    state.notifications.forEach((n) => {
      const li = make("li", { "data-sev": n.sev });
      if (n.title) li.appendChild(make("strong", {}, n.title));
      if (n.message) li.appendChild(make("span", {}, n.message));
      li.appendChild(make("small", {}, fmtRelTime(n.at)));
      list.appendChild(li);
    });
  }
  function toggleNotifPanel() {
    const panel = $("#notifPanel");
    if (!panel) return;
    panel.hidden = !panel.hidden;
    if (!panel.hidden) {
      state.notifSeen = state.notifications.length;
      renderNotifBadge();
    }
  }
  $("#notifBellBtn")?.addEventListener("click", toggleNotifPanel);
  $("#notifClearBtn")?.addEventListener("click", () => {
    state.notifications = [];
    state.notifSeen = 0;
    renderNotifBadge();
    renderNotifList();
  });
  document.addEventListener("click", (e) => {
    const panel = $("#notifPanel");
    if (!panel || panel.hidden) return;
    if (panel.contains(e.target) || $("#notifBellBtn")?.contains(e.target)) return;
    panel.hidden = true;
  });

  // =================================================================
  // KILL SWITCH banner (driven by public-config)
  // =================================================================
  function renderKillSwitch() {
    const banner = $("#killSwitchBanner");
    if (!banner) return;
    banner.hidden = !state.publicConfig.platform_live_trading_kill_switch;
  }

  // =================================================================
  // COMMAND PALETTE (Ctrl/Cmd+K)
  // =================================================================
  const CMD_ITEMS = [
    { label: "Go to Scans",       hint: "1",        run: () => setRoute("scans") },
    { label: "Go to Trade queue", hint: "2",        run: () => setRoute("queue") },
    { label: "Go to Portfolio",   hint: "3",        run: () => setRoute("portfolio") },
    { label: "Go to Research",    hint: "4",        run: () => setRoute("research") },
    { label: "Go to Diagnostics", hint: "5",        run: () => setRoute("diagnostics") },
    { label: "Go to Settings",    hint: "6",        run: () => setRoute("settings") },
    { label: "Run Conservative scan", hint: "scan", run: () => { setRoute("scans"); setTimeout(() => runScan("conservative"), 80); } },
    { label: "Run Balanced scan",     hint: "scan", run: () => { setRoute("scans"); setTimeout(() => runScan("balanced"), 80); } },
    { label: "Run Aggressive scan",   hint: "scan", run: () => { setRoute("scans"); setTimeout(() => runScan("aggressive"), 80); } },
    { label: "Refresh everything",    hint: "data", run: () => $("#globalRefreshBtn")?.click() },
    { label: "Open Research → Ticker check",  hint: "research", run: () => { setRoute("research"); setTimeout(() => activateResearchTab("check"), 80); } },
    { label: "Open Research → Decision card", hint: "research", run: () => { setRoute("research"); setTimeout(() => activateResearchTab("decision"), 80); } },
    { label: "Open Research → Full report",   hint: "research", run: () => { setRoute("research"); setTimeout(() => activateResearchTab("report"), 80); } },
    { label: "Open Research → SEC compare",   hint: "research", run: () => { setRoute("research"); setTimeout(() => activateResearchTab("sec"), 80); } },
    { label: "Open Research → Backtest",      hint: "research", run: () => { setRoute("research"); setTimeout(() => activateResearchTab("backtest"), 80); } },
    { label: "Open Research → Recovery",      hint: "research", run: () => { setRoute("research"); setTimeout(() => activateResearchTab("recovery"), 80); } },
  ];
  let cmdCursor = 0;

  function openCmdPalette() {
    const overlay = $("#cmdPalette");
    if (!overlay) return;
    overlay.hidden = false;
    cmdCursor = 0;
    const input = $("#cmdInput");
    if (input) { input.value = ""; input.focus(); }
    renderCmdList("");
  }
  function closeCmdPalette() {
    const overlay = $("#cmdPalette");
    if (overlay) overlay.hidden = true;
  }
  function renderCmdList(query) {
    const list = $("#cmdList");
    if (!list) return;
    const q = (query || "").toLowerCase().trim();
    const items = q
      ? CMD_ITEMS.filter((i) => i.label.toLowerCase().includes(q) || (i.hint || "").includes(q))
      : CMD_ITEMS.slice();
    list.innerHTML = "";
    if (!items.length) {
      list.appendChild(make("li", { class: "cmd-empty muted" }, "No matches."));
      return;
    }
    items.forEach((item, i) => {
      const li = make("li", { class: i === cmdCursor ? "is-cursor" : "" });
      li.appendChild(make("span", {}, item.label));
      li.appendChild(make("small", {}, item.hint || ""));
      li.addEventListener("click", () => { item.run(); closeCmdPalette(); });
      list.appendChild(li);
    });
    list._items = items;
  }
  $("#cmdPaletteBtn")?.addEventListener("click", openCmdPalette);
  $("#cmdInput")?.addEventListener("input", (e) => { cmdCursor = 0; renderCmdList(e.target.value); });
  $("#cmdInput")?.addEventListener("keydown", (e) => {
    const list = $("#cmdList");
    const items = list?._items || [];
    if (e.key === "Escape") { e.preventDefault(); closeCmdPalette(); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); cmdCursor = Math.min(items.length - 1, cmdCursor + 1); renderCmdList($("#cmdInput").value); return; }
    if (e.key === "ArrowUp")   { e.preventDefault(); cmdCursor = Math.max(0, cmdCursor - 1); renderCmdList($("#cmdInput").value); return; }
    if (e.key === "Enter") {
      e.preventDefault();
      const item = items[cmdCursor];
      if (item) { item.run(); closeCmdPalette(); }
    }
  });
  $("#cmdPalette")?.addEventListener("click", (e) => {
    if (e.target.id === "cmdPalette") closeCmdPalette();
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      const overlay = $("#cmdPalette");
      if (overlay && !overlay.hidden) closeCmdPalette();
      else openCmdPalette();
    }
  });

  // =================================================================
  // GLOBAL HOOKS
  // =================================================================
  $("#globalRefreshBtn")?.addEventListener("click", async () => {
    await Promise.all([refreshStatus(), refreshHealth(), refreshPending(), refreshOnboarding(), refreshScanStatus()]);
    renderRoute();
    renderKillSwitch();
    toast({ message: "Refreshed.", sev: "ok", ttl: 1800 });
  });
  $("#userMenuBtn")?.addEventListener("click", () => setRoute("settings"));

  // initial
  bootstrap()
    .then(() => { renderKillSwitch(); renderRoute(); })
    .catch((err) => { console.error(err); renderRoute(); });
})();
