/**
 * Portfolio Book sub-tab: Calendar | Tax | Journal.
 *
 * Realized P/L from Schwab TRADE history; MTM from local EOD snapshots;
 * journal notes in SQLite. See Book v1 design brief.
 */

import { api } from "../modules/api.js";
import { safeText, formatMoney, formatCount, formatDecimal } from "../modules/format.js";

const BOOK_SEGMENTS = ["calendar", "tax", "journal"];
let activeSegment = "calendar";
let calendarCache = null;
let selectedDay = null;
let journalSymbol = null;

function $(id) {
  return document.getElementById(id);
}

function moneyTone(n) {
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "book-pl-pos" : "book-pl-neg";
}

function fmtPl(n) {
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${formatMoney(n)}`;
}

export function openPortfolioBookTab(segment) {
  $("portfolioTabBook")?.click();
  if (segment && BOOK_SEGMENTS.includes(segment)) {
    setBookSegment(segment);
  }
  $("portfolioSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function openBookJournalForSymbol(symbol) {
  journalSymbol = String(symbol || "")
    .toUpperCase()
    .trim();
  openPortfolioBookTab("journal");
  void loadJournalDetail(journalSymbol);
}

function setBookSegment(seg) {
  activeSegment = BOOK_SEGMENTS.includes(seg) ? seg : "calendar";
  BOOK_SEGMENTS.forEach((s) => {
    const btn = $(`bookSeg${s[0].toUpperCase()}${s.slice(1)}`);
    const panel = $(`bookPanel${s[0].toUpperCase()}${s.slice(1)}`);
    const on = s === activeSegment;
    btn?.classList.toggle("tab-btn-active", on);
    btn?.setAttribute("aria-selected", on ? "true" : "false");
    panel?.classList.toggle("hidden", !on);
  });
  if (activeSegment === "calendar") void loadCalendar();
  if (activeSegment === "tax") void loadTax();
  if (activeSegment === "journal") void loadJournalList();
}

export async function loadBook() {
  const mount = $("portfolioBookMount");
  if (!mount) return;
  if (!mount.dataset.built) {
    mount.innerHTML = renderShell();
    mount.dataset.built = "1";
    wireShell(mount);
  }
  setBookSegment(activeSegment);
}

function renderShell() {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth() + 1;
  return `
    <div class="book-shell">
      <div class="tab-bar book-segments" role="tablist" aria-label="Book views">
        <div class="book-segments-tabs">
          <button type="button" id="bookSegCalendar" class="tab-btn tab-btn-active" role="tab" aria-selected="true">Calendar</button>
          <button type="button" id="bookSegTax" class="tab-btn" role="tab" aria-selected="false">Tax</button>
          <button type="button" id="bookSegJournal" class="tab-btn" role="tab" aria-selected="false">Journal</button>
        </div>
        <button type="button" id="bookCaptureSnapshotBtn" class="btn small secondary">Capture today</button>
      </div>
      <div id="bookPanelCalendar" class="book-panel" role="tabpanel">
        <div class="book-toolbar">
          <label class="book-field">
            <span class="book-field-label">Year</span>
            <input type="number" id="bookCalYear" class="book-input book-input--year" min="2000" max="2100" value="${y}">
          </label>
          <label class="book-field">
            <span class="book-field-label">Month</span>
            <input type="number" id="bookCalMonth" class="book-input book-input--month" min="1" max="12" value="${m}">
          </label>
          <button type="button" id="bookCalRefresh" class="btn small secondary">Refresh</button>
          <span id="bookCalMeta" class="book-meta"></span>
        </div>
        <div id="bookCalGrid" class="book-cal-grid" aria-live="polite"></div>
        <div id="bookCalDayDetail" class="book-day-detail">
          <p class="book-empty">Select a day for fills.</p>
        </div>
      </div>
      <div id="bookPanelTax" class="book-panel hidden" role="tabpanel">
        <div id="bookTaxBody"></div>
      </div>
      <div id="bookPanelJournal" class="book-panel hidden" role="tabpanel">
        <div class="book-journal-layout">
          <aside id="bookJournalList" class="book-journal-list" aria-label="Tickers"></aside>
          <div id="bookJournalDetail" class="book-journal-detail">
            <p class="book-empty">Select a ticker.</p>
          </div>
        </div>
      </div>
      <div id="bookStatus" class="book-status" aria-live="polite"></div>
    </div>`;
}

function wireShell(mount) {
  $("bookSegCalendar")?.addEventListener("click", () => setBookSegment("calendar"));
  $("bookSegTax")?.addEventListener("click", () => setBookSegment("tax"));
  $("bookSegJournal")?.addEventListener("click", () => setBookSegment("journal"));
  $("bookCalRefresh")?.addEventListener("click", () => void loadCalendar({ force: true }));
  $("bookCaptureSnapshotBtn")?.addEventListener("click", () => void captureSnapshot());
  mount.addEventListener("click", (e) => {
    const dayEl = e.target.closest?.("[data-book-day]");
    if (dayEl) {
      selectedDay = dayEl.getAttribute("data-book-day");
      renderDayDetail();
    }
    const jEl = e.target.closest?.("[data-book-journal-sym]");
    if (jEl) {
      journalSymbol = jEl.getAttribute("data-book-journal-sym");
      void loadJournalDetail(journalSymbol);
    }
  });
}

function setStatus(msg) {
  const el = $("bookStatus");
  if (el) el.textContent = msg || "";
}

async function captureSnapshot() {
  setStatus("Capturing EOD snapshot…");
  const out = await api.post("/api/book/snapshot", {});
  if (!out.ok) {
    setStatus(out.user_message || out.error || "Snapshot failed");
    return;
  }
  setStatus(`Snapshot saved for ${safeText(out.data?.snapshot_date)}`);
  if (activeSegment === "calendar") void loadCalendar({ force: true });
}

async function loadCalendar() {
  const grid = $("bookCalGrid");
  if (!grid) return;
  const year = Number($("bookCalYear")?.value) || new Date().getFullYear();
  const month = Number($("bookCalMonth")?.value) || new Date().getMonth() + 1;
  grid.innerHTML = `<div class="muted">Loading calendar…</div>`;
  const out = await api.get(`/api/book/calendar?year=${year}&month=${month}`);
  if (!out.ok) {
    grid.innerHTML = `<div class="muted">${safeText(out.user_message || out.error || "Failed")}</div>`;
    return;
  }
  calendarCache = out.data;
  const meta = out.data?.meta || {};
  const metaEl = $("bookCalMeta");
  if (metaEl) {
    metaEl.textContent = meta.error
      ? `Schwab: ${meta.error}`
      : `${formatCount(meta.count, "0")} TRADE row(s) · unmatched closes ${formatCount(out.data?.closes_unmatched, "0")}`;
  }
  renderCalendarGrid(year, month, out.data?.days || []);
  renderDayDetail();
}

function renderCalendarGrid(year, month, days) {
  const grid = $("bookCalGrid");
  if (!grid) return;
  const byDate = Object.fromEntries((days || []).map((d) => [d.date, d]));
  const first = new Date(year, month - 1, 1);
  const startPad = first.getDay(); // 0=Sun
  const dim = new Date(year, month, 0).getDate();
  const cells = [];
  ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].forEach((h) => {
    cells.push(`<div class="book-cal-head">${h}</div>`);
  });
  for (let i = 0; i < startPad; i++) cells.push(`<div class="book-cal-cell book-cal-empty"></div>`);
  for (let d = 1; d <= dim; d++) {
    const key = `${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const cell = byDate[key];
    const realized = cell ? Number(cell.realized_pl) : 0;
    const mtm = cell && cell.mtm_pl != null ? Number(cell.mtm_pl) : null;
    const trades = cell ? Number(cell.trade_count) : 0;
    const selected = selectedDay === key ? " book-cal-selected" : "";
    cells.push(`
      <button type="button" class="book-cal-cell${selected}" data-book-day="${key}">
        <span class="book-cal-dom">${d}</span>
        <span class="book-cal-realized ${moneyTone(realized)}">${trades ? fmtPl(realized) : "·"}</span>
        <span class="book-cal-mtm muted">${mtm == null ? "" : `MTM ${fmtPl(mtm)}`}</span>
      </button>`);
  }
  grid.innerHTML = cells.join("");
}

function renderDayDetail() {
  const el = $("bookCalDayDetail");
  if (!el || !calendarCache) return;
  if (!selectedDay) {
    el.innerHTML = `<p class="book-empty">Select a day for fills.</p>`;
    return;
  }
  const day = (calendarCache.days || []).find((d) => d.date === selectedDay);
  const fills = (calendarCache.fills || []).filter((f) => f.trade_date === selectedDay);
  const fees = day?.fees || 0;
  const rows = fills
    .map(
      (f) => `
      <tr>
        <td>${safeText(f.symbol)}</td>
        <td>${formatDecimal(f.qty, 2)}</td>
        <td class="${moneyTone(Number(f.realized_pl))}">${fmtPl(Number(f.realized_pl))}</td>
        <td>${safeText(f.holding?.toUpperCase() || "—")}</td>
        <td><button type="button" class="btn small secondary" data-book-add-note="${safeText(f.symbol)}" data-fill-id="${safeText(f.activity_id || "")}">Add note</button></td>
      </tr>`,
    )
    .join("");
  el.innerHTML = `
    <div class="book-day-card">
      <div class="book-day-summary">
        <div class="book-day-summary-title">${safeText(selectedDay)}</div>
        <div class="book-day-summary-metrics">
          <span>Realized <strong class="${moneyTone(Number(day?.realized_pl || 0))}">${fmtPl(Number(day?.realized_pl || 0))}</strong></span>
          <span>MTM <strong>${day?.mtm_pl == null ? "—" : fmtPl(Number(day.mtm_pl))}</strong></span>
          <span>Fees <strong>${formatMoney(fees)}</strong></span>
        </div>
        <p class="book-meta">Fees/cash flows excluded from MTM cell</p>
      </div>
      <div class="table-wrap">
        <table class="book-fills-table">
          <thead><tr><th>Symbol</th><th>Qty</th><th>Realized</th><th>Hold</th><th></th></tr></thead>
          <tbody>${rows || `<tr><td colspan="5" class="muted">No realized closes this day.</td></tr>`}</tbody>
        </table>
      </div>
    </div>`;
  el.querySelectorAll("[data-book-add-note]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sym = btn.getAttribute("data-book-add-note");
      const fillId = btn.getAttribute("data-fill-id") || "";
      openBookJournalForSymbol(sym);
      // Prefill quick note after detail loads
      setTimeout(() => {
        const body = $("bookNoteBody");
        const fill = $("bookNoteFillId");
        if (fill) fill.value = fillId;
        if (body && !body.value) body.value = `Review for ${selectedDay}`;
        const dateEl = $("bookNoteDate");
        if (dateEl) dateEl.value = selectedDay;
      }, 200);
    });
  });
}

async function loadTax() {
  const body = $("bookTaxBody");
  if (!body) return;
  body.innerHTML = `<div class="muted">Loading tax…</div>`;
  const out = await api.get("/api/book/tax");
  if (!out.ok) {
    body.innerHTML = `<div class="muted">${safeText(out.user_message || out.error)}</div>`;
    return;
  }
  const d = out.data || {};
  const prefs = d.prefs || {};
  const meta = d.meta || {};
  const st = d.short_term || {};
  const lt = d.long_term || {};
  const est = d.estimate;
  const pct = (r) => (r == null || r === "" ? "" : String(Math.round(Number(r) * 10000) / 100));
  const fetchBanner = meta.error
    ? `<div class="book-banner book-banner--warn" role="status">Trade history unavailable: ${safeText(meta.error)}. Totals may be incomplete.</div>`
    : meta.count != null
      ? `<p class="book-meta">FIFO from ${safeText(meta.count)} Schwab TRADE rows · ${safeText(meta.start || "?")} → ${safeText(meta.end || "?")}${d.closes_unmatched ? ` · ${safeText(d.closes_unmatched)} unmatched closes` : ""}</p>`
      : "";
  const estimateBlock = est
    ? `<div class="book-estimate-card">
        <div>
          <div class="book-kpi-label">Estimated tax</div>
          <div class="book-estimate-total">${formatMoney(est.total)}</div>
        </div>
        <div class="book-estimate-breakdown">
          <span>Federal ${formatMoney(est.federal)}</span>
          <span>State ${formatMoney(est.state)}</span>
        </div>
      </div>`
    : `<div class="book-banner book-banner--warn" role="status">Rates not set — enter federal ST/LT (and optional state) to unlock the dollar estimate.</div>`;
  body.innerHTML = `
    <div class="book-tax">
      ${fetchBanner}
      <div class="book-kpi-row">
        <div class="book-kpi">
          <div class="book-kpi-label">Short-term</div>
          <div class="book-kpi-value ${moneyTone(Number(st.net))}">${fmtPl(Number(st.net))}</div>
          <div class="book-kpi-meta">Gains ${formatMoney(st.gains)} · Losses ${formatMoney(st.losses)}</div>
        </div>
        <div class="book-kpi">
          <div class="book-kpi-label">Long-term</div>
          <div class="book-kpi-value ${moneyTone(Number(lt.net))}">${fmtPl(Number(lt.net))}</div>
          <div class="book-kpi-meta">Gains ${formatMoney(lt.gains)} · Losses ${formatMoney(lt.losses)}</div>
        </div>
        <div class="book-kpi">
          <div class="book-kpi-label">After netting</div>
          <div class="book-kpi-value ${moneyTone(Number(d.total_realized_net))}">${fmtPl(Number(d.total_realized_net))}</div>
          <div class="book-kpi-meta">ST ${fmtPl(Number(st.net_after_netting))} · LT ${fmtPl(Number(lt.net_after_netting))}</div>
        </div>
      </div>
      ${estimateBlock}
      <section class="book-rates-panel">
        <h3 class="book-section-title">Your rates</h3>
        <form id="bookTaxForm" class="book-tax-form">
          <label class="book-field">
            <span class="book-field-label">Tax year</span>
            <input type="number" id="bookTaxYear" class="book-input book-input--year" value="${safeText(prefs.tax_year || d.tax_year)}">
          </label>
          <label class="book-field">
            <span class="book-field-label">Fed ST %</span>
            <input type="number" id="bookTaxSt" class="book-input book-input--rate" step="0.1" min="0" max="100" value="${pct(prefs.federal_st_rate)}">
          </label>
          <label class="book-field">
            <span class="book-field-label">Fed LT %</span>
            <input type="number" id="bookTaxLt" class="book-input book-input--rate" step="0.1" min="0" max="100" value="${pct(prefs.federal_lt_rate)}">
          </label>
          <label class="book-field">
            <span class="book-field-label">State %</span>
            <input type="number" id="bookTaxState" class="book-input book-input--rate" step="0.1" min="0" max="100" value="${pct(prefs.state_rate ?? 0)}">
          </label>
          <div class="book-form-actions">
            <button type="submit" class="btn small">Save rates</button>
          </div>
        </form>
      </section>
      <p class="book-disclaimer">${safeText(d.disclaimer || "Estimate only — not tax advice.")}</p>
    </div>`;
  $("bookTaxForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const stR = Number($("bookTaxSt")?.value) / 100;
    const ltR = Number($("bookTaxLt")?.value) / 100;
    const stateR = Number($("bookTaxState")?.value) / 100;
    const year = Number($("bookTaxYear")?.value);
    if (![stR, ltR, stateR].every((n) => Number.isFinite(n))) {
      setStatus("Enter valid rate percentages.");
      return;
    }
    const saved = await api.post("/api/book/tax/prefs", {
      federal_st_rate: stR,
      federal_lt_rate: ltR,
      state_rate: stateR,
      tax_year: year,
    });
    if (!saved.ok) {
      setStatus(saved.user_message || saved.error || "Save failed");
      return;
    }
    setStatus("Tax rates saved.");
    void loadTax();
  });
}

async function loadJournalList() {
  const list = $("bookJournalList");
  if (!list) return;
  list.innerHTML = `<div class="muted">Loading…</div>`;
  const out = await api.get("/api/book/journal");
  if (!out.ok) {
    list.innerHTML = `<div class="muted">${safeText(out.user_message || out.error)}</div>`;
    return;
  }
  const tickers = out.data?.tickers || [];
  if (!tickers.length) {
    list.innerHTML = `<div class="muted">No open positions or notes yet.</div>`;
    return;
  }
  list.innerHTML = tickers
    .map(
      (t) => `
      <button type="button" class="book-journal-item${journalSymbol === t.symbol ? " active" : ""}" data-book-journal-sym="${safeText(t.symbol)}">
        <strong>${safeText(t.symbol)}</strong>
        <span class="muted small">${safeText(t.status)} · ${formatCount(t.note_count, "0")} note(s)</span>
      </button>`,
    )
    .join("");
  if (journalSymbol) void loadJournalDetail(journalSymbol);
}

async function loadJournalDetail(symbol) {
  const detail = $("bookJournalDetail");
  if (!detail || !symbol) return;
  detail.innerHTML = `<div class="muted">Loading ${safeText(symbol)}…</div>`;
  const out = await api.get(`/api/book/journal/${encodeURIComponent(symbol)}`);
  if (!out.ok) {
    detail.innerHTML = `<div class="muted">${safeText(out.user_message || out.error)}</div>`;
    return;
  }
  const d = out.data || {};
  const notes = (d.notes || [])
    .map(
      (n) => `
      <article class="book-note">
        <header><strong>${safeText(n.note_date)}</strong> · ${safeText(n.mode)} · ${safeText(n.note_type)}</header>
        <p>${safeText(n.body || "(no body)")}</p>
        ${n.mode === "full" && n.template ? `<pre class="book-note-template">${safeText(JSON.stringify(n.template, null, 0))}</pre>` : ""}
      </article>`,
    )
    .join("");
  detail.innerHTML = `
    <div class="book-journal-detail-inner">
      <header class="book-journal-header">
        <h3 class="book-journal-symbol">${safeText(d.symbol)}</h3>
      </header>
      <section class="book-thesis-panel">
        <h4 class="book-section-title">Thesis / plan</h4>
        <label class="book-field book-field--block">
          <textarea id="bookThesis" class="book-textarea" rows="3">${safeText(d.thesis || "")}</textarea>
        </label>
        <div class="book-form-actions">
          <button type="button" id="bookSaveThesis" class="btn small secondary">Save thesis</button>
        </div>
      </section>
      <section class="book-note-panel">
        <div class="tab-bar book-note-modes">
          <button type="button" id="bookModeQuick" class="tab-btn tab-btn-active">Quick note</button>
          <button type="button" id="bookModeFull" class="tab-btn">Full review</button>
        </div>
        <form id="bookNoteForm" class="book-note-form">
          <input type="hidden" id="bookNoteMode" value="quick">
          <input type="hidden" id="bookNoteFillId" value="">
          <label class="book-field">
            <span class="book-field-label">Date</span>
            <input type="date" id="bookNoteDate" class="book-input">
          </label>
          <label class="book-field">
            <span class="book-field-label">Type</span>
            <select id="bookNoteType" class="book-input">
              <option value="thesis">thesis</option>
              <option value="hold">hold</option>
              <option value="exit">exit</option>
              <option value="mistake">mistake</option>
              <option value="other" selected>other</option>
            </select>
          </label>
          <label class="book-field book-field--block">
            <span class="book-field-label">Notes</span>
            <textarea id="bookNoteBody" class="book-textarea" rows="3" required></textarea>
          </label>
          <div id="bookFullFields" class="book-full-fields hidden">
            <label class="book-field"><span class="book-field-label">Setup</span><input id="bookTplSetup" class="book-input" type="text"></label>
            <label class="book-field"><span class="book-field-label">Entry</span><input id="bookTplEntry" class="book-input" type="text"></label>
            <label class="book-field"><span class="book-field-label">Stop</span><input id="bookTplStop" class="book-input" type="text"></label>
            <label class="book-field"><span class="book-field-label">Target</span><input id="bookTplTarget" class="book-input" type="text"></label>
            <label class="book-field"><span class="book-field-label">Emotions</span><input id="bookTplEmotions" class="book-input" type="text"></label>
            <label class="book-field">
              <span class="book-field-label">Followed plan?</span>
              <select id="bookTplFollowed" class="book-input"><option value="">—</option><option value="yes">yes</option><option value="no">no</option></select>
            </label>
          </div>
          <div class="book-form-actions">
            <button type="submit" class="btn small">Save note</button>
          </div>
        </form>
      </section>
      <section class="book-notes-timeline">
        <h4 class="book-section-title">Notes</h4>
        ${notes || `<p class="book-empty">No notes yet.</p>`}
      </section>
    </div>`;

  $("bookSaveThesis")?.addEventListener("click", async () => {
    const thesis = $("bookThesis")?.value || "";
    const saved = await api.post(`/api/book/journal/${encodeURIComponent(symbol)}/thesis`, { thesis });
    setStatus(saved.ok ? "Thesis saved." : saved.user_message || saved.error);
    if (saved.ok) void loadJournalList();
  });
  $("bookModeQuick")?.addEventListener("click", () => {
    $("bookNoteMode").value = "quick";
    $("bookModeQuick").classList.add("tab-btn-active");
    $("bookModeFull").classList.remove("tab-btn-active");
    $("bookFullFields")?.classList.add("hidden");
  });
  $("bookModeFull")?.addEventListener("click", () => {
    $("bookNoteMode").value = "full";
    $("bookModeFull").classList.add("tab-btn-active");
    $("bookModeQuick").classList.remove("tab-btn-active");
    $("bookFullFields")?.classList.remove("hidden");
  });
  $("bookNoteForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const mode = $("bookNoteMode")?.value || "quick";
    const payload = {
      symbol,
      mode,
      body: $("bookNoteBody")?.value || "",
      note_type: $("bookNoteType")?.value || "other",
      note_date: $("bookNoteDate")?.value || null,
      fill_activity_id: $("bookNoteFillId")?.value || null,
      template:
        mode === "full"
          ? {
              setup: $("bookTplSetup")?.value || "",
              entry: $("bookTplEntry")?.value || "",
              stop: $("bookTplStop")?.value || "",
              target: $("bookTplTarget")?.value || "",
              emotions: $("bookTplEmotions")?.value || "",
              followed_plan: $("bookTplFollowed")?.value || "",
            }
          : {},
    };
    const saved = await api.post("/api/book/journal/notes", payload);
    if (!saved.ok) {
      setStatus(saved.user_message || saved.error || "Note save failed");
      return;
    }
    setStatus("Note saved.");
    void loadJournalDetail(symbol);
    void loadJournalList();
  });
}

/** Resolve hash aliases like #book-calendar → open Book segment. */
export function resolveBookHash(hashId) {
  const map = {
    portfolioPanelBook: "calendar",
    "book-calendar": "calendar",
    bookCalendar: "calendar",
    "book-tax": "tax",
    bookTax: "tax",
    "book-journal": "journal",
    bookJournal: "journal",
  };
  if (map[hashId]) {
    openPortfolioBookTab(map[hashId]);
    return true;
  }
  return false;
}
