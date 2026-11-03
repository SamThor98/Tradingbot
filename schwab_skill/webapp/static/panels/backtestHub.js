/**
 * Backtest landing hub — composer KPIs, queue library, selected-run briefing.
 * Uses GET /api/backtest-hub and GET /api/backtest-runs/{id}.
 */

import { api } from "../modules/api.js";
import { escapeHtml, formatPercentPoints, safeText } from "../modules/format.js";
import { relatedEvidenceHtml } from "../modules/typedView.js";
import { setAsyncState, ASYNC_ERROR } from "../modules/asyncState.js";
import { state } from "../modules/state.js";
import {
  renderBacktestResultSummary,
  renderBacktestResultRaw,
  backtestSpecSummaryLine,
  collectBacktestSpecFromForm,
  previewBacktestWindowYears,
  renderBacktestRunCompare,
} from "./backtest.js";
import {
  filterLeaderboardRows,
  exitReasonLabel,
  formatBacktestLabel,
  LARGE_N_TRADES,
  runDisplaySubtitle,
  runDisplayTitle,
  runStatusLabel,
  universeDisplayName,
} from "./backtestLabels.js";
import {
  MONITOR_RUN_CAP,
  defaultQueueFilter,
  filterQueueRows,
  finishedTradeTape,
  formatElapsed,
  inProgressTape,
  isCampaignRun,
  latestFinishedRun,
  pfNumber,
  pfTone,
  summarizeMonitor,
} from "./backtestMonitor.js";

let _hub = null;
let _studyView = null;
let _studyBusy = false;
let _studyAutoKey = "";
let _monitorRuns = [];
let _selectedRunId = null;
let _selectedRun = null;
let _pollTimer = null;
let _queueFilter = "";
let _queueShowAll = false;
let _boardWindow = "all";
let _boardUniverse = "all";
let _boardSample = "large";
let _selectionPinned = false;
const QUEUE_FILTER_KEY = "tb.bt.queueFilter";
const BOARD_WINDOW_KEY = "tb.bt.boardWindow";
const BOARD_UNIVERSE_KEY = "tb.bt.boardUniverse";
const BOARD_SAMPLE_KEY = "tb.bt.boardSample";
const QUEUE_VISIBLE_CAP = 25;
const BOARD_VISIBLE_CAP = 10;

function btLabelCatalog() {
  return state.scanCatalog;
}

function runTitle(run) {
  return runDisplayTitle(run, btLabelCatalog());
}

function runSubtitle(run) {
  return runDisplaySubtitle(run, btLabelCatalog());
}

function formatPf(value) {
  if (value === "inf" || value === Infinity) return "∞";
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
}

function formatSigned(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}`;
}

function toneClass(tone) {
  const t = String(tone || "neutral");
  if (t === "ok" || t === "healthy") return "ok";
  if (t === "bad" || t === "blocked") return "bad";
  if (t === "warn" || t === "attention") return "warn";
  return "neutral";
}

export function getSelectedBacktestRun() {
  return _selectedRun;
}

export function getBacktestHub() {
  return _hub;
}

function emitCta(cta) {
  if (!cta || typeof cta !== "object") return;
  window.dispatchEvent(new CustomEvent("backtest_cta", { detail: cta }));
}

function renderMoreActions(rows) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return "";
  const items = list
    .map((s) => {
      const cta = s.cta && s.cta.label ? s.cta : null;
      const btn = cta
        ? `<button type="button" class="btn small secondary bt-cta" data-step-id="${escapeHtml(s.id || "")}">${escapeHtml(cta.label)}</button>`
        : "";
      return `<li>
        <span>${escapeHtml(s.title || "Next")}</span>
        ${btn}
      </li>`;
    })
    .join("");
  return `<details class="bt-more-actions"><summary>More actions</summary><ul class="bt-more-action-list">${items}</ul></details>`;
}

function encodeHorizonActions(actions) {
  try {
    return encodeURIComponent(JSON.stringify(Array.isArray(actions) ? actions : []));
  } catch {
    return "";
  }
}

function readHorizonActions(sel) {
  const raw = sel?.getAttribute("data-horizon-actions") || "";
  if (!raw) return [];
  try {
    const parsed = JSON.parse(decodeURIComponent(raw));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function testFurtherMenu(row) {
  const actions = Array.isArray(row?.horizon_actions) ? row.horizon_actions : [];
  const label = row?.display_label || "this setup";
  const options = actions
    .map((action) => {
      const years = Number(action.years);
      const disabled = action.available === false ? " disabled" : "";
      return `<option value="${escapeHtml(String(years))}"${disabled}>${escapeHtml(action.label || `Test last ${years} years`)}</option>`;
    })
    .join("");
  return `<div class="bt-test-further-wrap">
    <select class="bt-test-further" data-run-id="${escapeHtml(row?.id || "")}" data-horizon-actions="${encodeHorizonActions(actions)}" aria-label="Test ${escapeHtml(label)} over another window" title="Same sleeve and index. New dates only. Per-window PF is not LIVE-ready.">
      <option value="">Test further</option>
      ${options}
    </select>
  </div>`;
}

function wireHorizonMenus(root) {
  if (!root) return;
  root.querySelectorAll(".bt-test-further").forEach((sel) => {
    const stop = (ev) => ev.stopPropagation();
    sel.addEventListener("click", stop);
    sel.addEventListener("mousedown", stop);
    sel.addEventListener("keydown", stop);
    sel.addEventListener("change", (ev) => {
      ev.stopPropagation();
      const years = Number(sel.value);
      const action = readHorizonActions(sel).find((row) => Number(row.years) === years);
      sel.value = "";
      if (!action || action.available === false) return;
      if (action.existing_run_id) {
        _selectionPinned = true;
        void selectBacktestRun(action.existing_run_id, { writeUrl: true });
        return;
      }
      if (action.cta) emitCta(action.cta);
    });
  });
}

function renderCountRow(title, rows) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return "";
  return `<div class="bt-count-row">
    <h3>${escapeHtml(title)}</h3>
    <ul>${list
      .map((r) => `<li><span>${escapeHtml(r.label || r.id || "")}</span><strong class="mono-nums">${escapeHtml(r.count)}</strong></li>`)
      .join("")}</ul>
  </div>`;
}

function renderSample(rows) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return "";
  return `<div class="bt-sample table-wrap">
    <h3>Sample trades</h3>
    <table>
      <thead><tr><th>Ticker</th><th>In</th><th>Out</th><th>Net</th><th>Exit</th></tr></thead>
      <tbody>${list
        .map(
          (t) => `<tr>
            <td>${escapeHtml(t.ticker || "—")}</td>
            <td class="mono-nums">${escapeHtml(String(t.entry_date || "").slice(0, 10))}</td>
            <td class="mono-nums">${escapeHtml(String(t.exit_date || "").slice(0, 10))}</td>
            <td class="mono-nums">${formatPercentPoints(t.net_return_pct, 2)}</td>
            <td>${escapeHtml(exitReasonLabel(t.exit_reason) || "—")}</td>
          </tr>`,
        )
        .join("")}</tbody>
    </table>
  </div>`;
}

function universeLabel(run) {
  const spec = run?.spec && typeof run.spec === "object" ? run.spec : {};
  const id = String(spec.universe_preset || spec.universe_mode || "").trim();
  return universeDisplayName(id, btLabelCatalog()) || formatBacktestLabel(spec, btLabelCatalog()).parts.universe;
}

function barRow(label, count, max, tone) {
  const width = max > 0 ? Math.max(4, Math.round((count / max) * 100)) : 0;
  return `<div class="bt-bar-row" data-tone="${escapeHtml(tone || "neutral")}">
    <span class="bt-bar-label">${escapeHtml(label)}</span>
    <span class="bt-bar-track" aria-hidden="true"><span class="bt-bar-fill" style="width:${width}%"></span></span>
    <strong class="mono-nums">${escapeHtml(count)}</strong>
  </div>`;
}

function tapeNetCell(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return `<td class="mono-nums">—</td>`;
  const tone = n > 0 ? "ok" : n < 0 ? "bad" : "neutral";
  const text = n > 0 ? `+${formatPercentPoints(n, 1)}` : formatPercentPoints(n, 1);
  return `<td class="mono-nums" data-tone="${tone}">${escapeHtml(text)}</td>`;
}

function renderTapeColumn(title, rows, { showRun = false } = {}) {
  if (!rows.length) return "";
  const head = showRun
    ? `<tr><th>Ticker</th><th>Net</th><th>Exit</th><th>Out</th><th>Run</th></tr>`
    : `<tr><th>Ticker</th><th>Net</th><th>Exit</th><th>Out</th></tr>`;
  const body = rows
    .map((trade) => {
      const ticker = escapeHtml(trade.ticker || "—");
      const name = trade.runId
        ? `<button type="button" class="bt-tape-run" data-tape-run="${escapeHtml(trade.runId)}">${ticker}</button>`
        : ticker;
      const runCell = showRun ? `<td class="muted">${escapeHtml(trade.runLabel || "")}</td>` : "";
      return `<tr>
        <td>${name}</td>
        ${tapeNetCell(trade.net_return_pct)}
        <td>${escapeHtml(exitReasonLabel(trade.exit_reason) || "—")}</td>
        <td class="mono-nums">${escapeHtml(String(trade.exit_date || "").slice(0, 10) || "—")}</td>
        ${runCell}
      </tr>`;
    })
    .join("");
  return `<div class="bt-tape-col">
    <p class="bt-live-kicker">${escapeHtml(title)}</p>
    <table class="bt-tape-table">
      <thead>${head}</thead>
      <tbody>${body}</tbody>
    </table>
  </div>`;
}

function renderTapeBlock(title, note, best, worst, { showRun = false } = {}) {
  const columns = [
    renderTapeColumn("Best", best, { showRun }),
    renderTapeColumn("Worst", worst, { showRun }),
  ].filter(Boolean);
  if (!columns.length) return "";
  return `<section class="bt-tape-block" aria-label="${escapeHtml(title)}">
    <p class="bt-live-kicker">${escapeHtml(title)}</p>
    <p class="muted small">${escapeHtml(note)}</p>
    <div class="bt-tape">${columns.join("")}</div>
  </section>`;
}

function renderLiveMonitor(runs) {
  const host = document.getElementById("backtestLiveMonitor");
  if (!host) return;
  const stats = summarizeMonitor(runs);
  const running = stats.running;
  const liveTape = inProgressTape(running);
  const finishedTape = finishedTradeTape(runs);
  const latest = latestFinishedRun(runs);
  const latestSum = latest?.result_summary || {};
  const latestLine = latest
    ? `${runTitle(latest) || "Finished run"} · PF ${formatPf(pfNumber(latest))} · win ${formatPercentPoints(latestSum.win_rate_net, 0)} · return ${formatPercentPoints(latestSum.total_return_net_pct, 1)} · DD ${formatPercentPoints(latestSum.max_drawdown_net_pct, 1)}`
    : "";
  const finishedNote = [
    latestLine,
    finishedTape.sources
      ? `${finishedTape.sources} finished run${finishedTape.sources === 1 ? "" : "s"} in this slice`
      : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const runName = running ? runTitle(running) || "Running job" : "Queue idle";
  const runMeta = running
    ? [universeLabel(running), isCampaignRun(running) ? "weekend campaign" : "manual"]
        .filter(Boolean)
        .join(" · ")
    : "One worker on this computer. Extra jobs wait.";
  const elapsed = running ? formatElapsed(stats.elapsedMs) : "—";
  const waitSlots = Math.min(8, Math.max(0, stats.queued.length));
  const slots = Array.from({ length: 8 }, (_, i) => {
    const filled = i < waitSlots;
    return `<span class="bt-slot${filled ? " is-filled" : ""}" title="${filled ? "Waiting" : "Open"}"></span>`;
  }).join("");
  const camp = stats.campaign;
  const meanPf = camp.meanPf == null ? "—" : camp.meanPf.toFixed(2);
  const thru = stats.throughputPerHour == null ? "—" : `${stats.throughputPerHour.toFixed(1)}/h`;
  const bucketMax = Math.max(1, ...stats.buckets.map((b) => b.count));
  const uniMax = Math.max(1, ...stats.universes.map((u) => u.count));
  const PF_SPARK_MAX = 2.4;
  const pfSpark = camp.pfs
    .slice(0, 24)
    .map((pf) => {
      const n = pf === Infinity ? PF_SPARK_MAX : Number(pf);
      const h = Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round((n / PF_SPARK_MAX) * 100))) : 0;
      return `<span class="bt-spark-bar" data-tone="${pfTone(pf)}" style="height:${h}%" title="PF ${escapeHtml(formatPf(pf))} on 0–${PF_SPARK_MAX} scale (gate 1.20)"></span>`;
    })
    .join("");
  const recent = stats.recentFinished
    .map((r) => {
      const pf = pfNumber(r);
      return `<li>
        <span class="bt-chip" data-tone="${pfTone(pf)}">PF ${escapeHtml(formatPf(pf))}</span>
        <span>${escapeHtml(runTitle(r) || "Finished run")}</span>
      </li>`;
    })
    .join("");
  host.innerHTML = `
    <div class="bt-live-now" data-busy="${running ? "true" : "false"}">
      <p class="bt-live-kicker">${running ? "Running now" : "Worker idle"}</p>
      <strong id="btLiveNowName">${escapeHtml(runName)}</strong>
      <p class="muted" id="btLiveNowMeta">${escapeHtml(runMeta)}</p>
      <dl class="bt-live-metrics">
        <div><dt>Elapsed</dt><dd class="mono-nums">${escapeHtml(elapsed)}</dd></div>
        <div><dt>Waiting</dt><dd class="mono-nums">${escapeHtml(stats.queued.length)}</dd></div>
        <div><dt>Closed</dt><dd class="mono-nums">${liveTape ? escapeHtml(liveTape.closed) : "—"}</dd></div>
        <div><dt>Recent campaign</dt><dd class="mono-nums">${escapeHtml(camp.status.success)} finished · ${escapeHtml(camp.status.failed)} fail</dd></div>
      </dl>
      <div class="bt-slot-row" aria-label="Waiting slots, max 8">${slots}</div>
    </div>
    ${renderTapeBlock(
      "This replay",
      liveTape
        ? `${liveTape.closed} closes so far. Partial — not a finished result.`
        : "",
      liveTape?.best || [],
      liveTape?.worst || [],
    )}
    ${renderTapeBlock("Best and worst closes", finishedNote, finishedTape.best, finishedTape.worst, { showRun: true })}
    ${
      !liveTape && !finishedTape.sources
        ? `<p class="muted small">Best and worst trades appear as closes land. Replays finished before this tape do not include a rank.</p>`
        : ""
    }
    <div class="bt-live-charts">
      <div>
        <p class="bt-live-kicker">Profit factor · last ${escapeHtml(stats.sampleSize)} runs</p>
        <p class="muted small">Campaign jobs in this slice: ${escapeHtml(camp.total)}. Mean PF ${escapeHtml(meanPf)} · ${escapeHtml(camp.aboveGate)} at or above the 1.20 gate. Single-window PF is not LIVE-ready.</p>
        <div class="bt-spark" role="img" aria-label="Recent campaign profit factors, scale 0 to 2.4, gate 1.20">${pfSpark || `<span class="muted">No finished campaign PF in this slice yet.</span>`}</div>
        <p class="muted small">PF axis 0–2.4 · gate line 1.20</p>
        <div class="bt-bar-list">${stats.buckets.map((b) => barRow(b.label, b.count, bucketMax, b.tone)).join("")}</div>
      </div>
      <div>
        <p class="bt-live-kicker">Indexes in this slice</p>
        <p class="muted small">Throughput about ${escapeHtml(thru)} from recent campaign finishes. API lists at most ${MONITOR_RUN_CAP} runs.</p>
        <div class="bt-bar-list">${
          stats.universes.length
            ? stats.universes.map((u) =>
                barRow(universeDisplayName(u.id, btLabelCatalog()) || u.id, u.count, uniMax, "neutral"),
              ).join("")
            : `<p class="muted">No index mix yet.</p>`
        }</div>
      </div>
    </div>
    <div class="bt-live-recent">
      <p class="bt-live-kicker">Just finished</p>
      ${recent ? `<ul class="bt-recent-list">${recent}</ul>` : `<p class="muted">No campaign finishes in the latest ${MONITOR_RUN_CAP} runs.</p>`}
    </div>
  `;
  const statusEl = document.getElementById("btLiveStatus");
  const statusText = running
    ? `${runName}. ${liveTape ? `${liveTape.closed} closes so far.` : "Waiting for the first close."}`
    : latest
      ? `Latest finish ${runTitle(latest) || "run"}.`
      : "Queue idle.";
  if (statusEl && statusEl.textContent !== statusText) statusEl.textContent = statusText;
  const wrap = document.getElementById("backtestLiveMonitorWrap");
  if (wrap && (running || liveTape)) wrap.open = true;
  wireLiveTape();
}

function wireLiveTape() {
  const wrap = document.getElementById("backtestLiveMonitorWrap");
  if (!wrap || wrap.dataset.tapeWired === "1") return;
  wrap.dataset.tapeWired = "1";
  wrap.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-tape-run]");
    if (!btn || !wrap.contains(btn)) return;
    const id = btn.getAttribute("data-tape-run") || "";
    if (!id) return;
    _selectionPinned = true;
    void selectBacktestRun(id, { writeUrl: true });
  });
}

function readQueueFilter(runs) {
  if (_queueFilter) return _queueFilter;
  try {
    const saved = sessionStorage.getItem(QUEUE_FILTER_KEY) || "";
    if (saved === "live" || saved === "campaign" || saved === "all") {
      _queueFilter = saved;
      return saved;
    }
  } catch {
    /* ignore */
  }
  _queueFilter = defaultQueueFilter(runs);
  return _queueFilter;
}

function setQueueFilter(next, runs) {
  _queueFilter = next;
  _queueShowAll = false;
  try {
    sessionStorage.setItem(QUEUE_FILTER_KEY, next);
  } catch {
    /* ignore */
  }
  renderQueue(_hub, runs);
}

function syncQueueFilterButtons(filter) {
  document.querySelectorAll("[data-bt-queue-filter]").forEach((btn) => {
    const on = btn.getAttribute("data-bt-queue-filter") === filter;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

function isFinishedRun(run) {
  if (!run) return false;
  return String(run.status || "") === "success" && Boolean(run.has_result || run.result_summary);
}

function boardFilterExtras() {
  return {
    universeId: _boardUniverse,
    minTrades: _boardSample === "large" ? LARGE_N_TRADES : 0,
  };
}

function visibleQualifiedRows(hub) {
  const board = hub?.leaderboard && typeof hub.leaderboard === "object" ? hub.leaderboard : {};
  const want = _boardWindow === "all" ? null : Number(_boardWindow);
  const minTrades = _boardSample === "large" ? Number(board.criteria?.large_n_trades || LARGE_N_TRADES) : 0;
  return filterLeaderboardRows(board.qualified, want, {
    universeId: _boardUniverse,
    minTrades,
  });
}

function renderPromotionChip(hub) {
  const el = document.getElementById("backtestPromotionChip");
  if (!el) return;
  const cards = Array.isArray(hub?.related) ? hub.related : [];
  const phase2 = cards.find((c) => String(c.kind || "") === "phase2");
  if (!phase2) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  const verdict = String(phase2.verdict || "Not generated yet");
  el.hidden = false;
  el.dataset.tone = phase2.exists && verdict !== "proceed" ? "warn" : "neutral";
  el.textContent = `Promotion: ${verdict.replace(/_/g, " ")}`;
}

function landingSubject(hub) {
  if (_selectedRun && (_selectedRun.outcome?.headline || _selectedRun.briefing?.short)) return _selectedRun;
  return hub?.latest_success || _hub?.latest_success || null;
}

function renderLanding(hub) {
  const verdictEl = document.getElementById("backtestZoneVerdict");
  const detailEl = document.getElementById("backtestZoneDetail");
  const nextEl = document.getElementById("backtestLandingNext");
  const queueEl = document.getElementById("backtestLandingQueue");
  const frame = document.getElementById("backtestSummaryLanding");
  const finished = landingSubject(hub);
  const queue = hub?.queue || {};
  const outcome = finished?.outcome || {};
  const stats = summarizeMonitor(_monitorRuns);
  const running = stats.running;
  if (frame) frame.dataset.tone = toneClass(outcome.tone) || (running ? "ok" : "neutral");
  if (verdictEl) {
    verdictEl.textContent = outcome.headline || (running ? "Worker busy" : stats.queued.length ? `${stats.queued.length} waiting` : "Queue idle");
  }
  if (detailEl) {
    if (running) {
      const who = runTitle(running) || "a replay";
      detailEl.textContent = `Running ${who}. ${stats.queued.length} waiting.`;
    } else if (outcome.summary) {
      detailEl.textContent = outcome.summary;
    } else {
      detailEl.textContent = "Ranked by trust, then robustness, then PF. Not LIVE-ready.";
    }
  }
  const selectedBusy =
    _selectedRun &&
    !isFinishedRun(_selectedRun) &&
    (_selectedRun.status === "queued" || _selectedRun.status === "running");
  const primary = selectedBusy ? null : finished?.briefing?.short?.primary;
  const nextLabel = selectedBusy
    ? "Waiting on this replay"
    : primary?.cta?.label || primary?.title || "Queue last 5 years";
  if (nextEl) {
    nextEl.textContent = nextLabel;
    nextEl.disabled = Boolean(selectedBusy);
  }
  if (queueEl) {
    const runningCount = running ? 1 : 0;
    const queuedCount = stats.queued.length || Number(queue.queued || 0);
    queueEl.textContent = runningCount
      ? `Running · ${queuedCount} waiting`
      : queuedCount
        ? `${queuedCount} waiting`
        : "Idle";
  }
  renderPromotionChip(hub);
  renderLiveMonitor(_monitorRuns);
  renderLeaderboard(hub);
  syncMonitorDisclosure();
}

function mergeRunLists(hub, extra) {
  const byId = new Map();
  for (const row of extra || []) {
    if (row?.id) byId.set(String(row.id), { ...row });
  }
  for (const row of hub?.runs || []) {
    if (!row?.id) continue;
    const id = String(row.id);
    byId.set(id, { ...(byId.get(id) || {}), ...row });
  }
  return [...byId.values()].sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
}

function formatBoardPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return formatPercentPoints(n, 1);
}

function returnTone(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "neutral";
  return n > 0 ? "ok" : "bad";
}

function drawdownTone(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "neutral";
  if (n <= -15) return "bad";
  if (n < 0) return "warn";
  return "neutral";
}

/** Bar plus number. Gate tick sits at PF 1.20 on a 0–2.4 scale. */
function pfGateMark(pf) {
  const text = formatPf(pf);
  const n = pf === Infinity ? 2.4 : Number(pf);
  if (!Number.isFinite(n)) return `<span class="mono-nums">${escapeHtml(text)}</span>`;
  const tone = pfTone(pf);
  const width = Math.max(0, Math.min(100, (Math.min(n, 2.4) / 2.4) * 100));
  const gate = (1.2 / 2.4) * 100;
  return `<span class="ol-pf-mark" title="Profit factor ${escapeHtml(text)} on a 0–2.4 scale. Gate at 1.20.">
    <span class="ol-pf-track" aria-hidden="true"><span class="ol-pf-fill" data-tone="${tone}" style="width:${width.toFixed(1)}%"></span><span class="ol-pf-gate" style="left:${gate.toFixed(1)}%"></span></span>
    <span class="mono-nums" data-tone="${tone}">${escapeHtml(text)}</span>
  </span>`;
}

function leaderboardRowHtml(row, { thin = false } = {}) {
  const pf = row?.profit_factor_net;
  const selected = String(row?.id || "") === String(_selectedRunId || "") ? " is-selected" : "";
  const rank = thin ? "—" : escapeHtml(row?.rank ?? "");
  const variants = Number(row?.variant_count || 1);
  const pfMin = row?.cluster_pf_min;
  const pfMax = row?.cluster_pf_max;
  const variantBit =
    variants > 1
      ? `${variants} variants` +
        (Number.isFinite(Number(pfMin)) && Number.isFinite(Number(pfMax))
          ? `, PF ${formatPf(pfMin)}–${formatPf(pfMax)}`
          : "")
      : "";
  const flags = [];
  if (row?.not_schwab_clean) flags.push("not Schwab-clean");
  if (row?.live_membership) flags.push("live membership");
  const honesty = flags[0] || "";
  const bits = [variantBit].filter(Boolean);
  const subtitle = bits.length ? `<span class="muted">${escapeHtml(bits.join(" · "))}</span>` : "";
  const trust = row?.trust_label
    ? `<span class="bt-board-trust" data-trust="${escapeHtml(row?.trust_tier ?? "")}">${escapeHtml(row.trust_label)}</span>`
    : "";
  const flagHtml = honesty
    ? `<span class="bt-board-flag" data-flag="not-schwab-clean">${escapeHtml(honesty)}</span>`
    : "";
  return `<tr class="bt-board-row${selected}${thin ? " is-thin" : ""}" data-run-id="${escapeHtml(row?.id || "")}" data-trust="${escapeHtml(row?.trust_tier ?? "")}" tabindex="0">
    <td class="mono-nums">${rank}</td>
    <td>
      <strong>${escapeHtml(row?.display_label || "Backtest")}</strong>
      ${trust}
      ${flagHtml}
      ${subtitle}
    </td>
    <td>${escapeHtml(row?.window_label || windowLabelFallback(row?.window_years))}</td>
    <td class="mono-nums">${escapeHtml(row?.total_trades ?? "—")}</td>
    <td class="mono-nums" data-tone="${pfTone(pf)}">${pfGateMark(pf)}</td>
    <td class="mono-nums" data-tone="${returnTone(row?.total_return_net_pct)}">${escapeHtml(formatBoardPct(row?.total_return_net_pct))}</td>
    <td class="mono-nums" data-tone="${drawdownTone(row?.max_drawdown_net_pct)}">${escapeHtml(formatBoardPct(row?.max_drawdown_net_pct))}</td>
    <td class="bt-board-actions">${testFurtherMenu(row)}</td>
  </tr>`;
}

function windowLabelFallback(years) {
  const n = Number(years);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return n === 1 ? "1 year" : `${n} years`;
}

function syncBoardFilterButtons() {
  document.querySelectorAll("[data-bt-board-window]").forEach((btn) => {
    const on = btn.getAttribute("data-bt-board-window") === _boardWindow;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  document.querySelectorAll("[data-bt-board-universe]").forEach((btn) => {
    const on = btn.getAttribute("data-bt-board-universe") === _boardUniverse;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  document.querySelectorAll("[data-bt-board-sample]").forEach((btn) => {
    const on = btn.getAttribute("data-bt-board-sample") === _boardSample;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

function renderLeaderboard(hub) {
  const host = document.getElementById("btLeaderboardBody");
  const note = document.getElementById("btLeaderboardNote");
  if (!host) return;
  const board = hub?.leaderboard && typeof hub.leaderboard === "object" ? hub.leaderboard : {};
  const want = _boardWindow === "all" ? null : Number(_boardWindow);
  const extras = boardFilterExtras();
  if (_boardSample === "large") extras.minTrades = Number(board.criteria?.large_n_trades || LARGE_N_TRADES);
  const qualified = filterLeaderboardRows(board.qualified, want, extras).slice(0, BOARD_VISIBLE_CAP);
  const thin = _boardSample === "large" ? [] : filterLeaderboardRows(board.thin, want, { universeId: _boardUniverse }).slice(0, 5);
  const minTrades = board.criteria?.min_trades ?? 35;
  const largeN = board.criteria?.large_n_trades ?? LARGE_N_TRADES;
  if (note) {
    note.textContent = "Ranked by trust, then robustness, then PF. Not LIVE-ready.";
  }
  syncBoardFilterButtons();
  if (!qualified.length && !thin.length) {
    const hint =
      _boardSample === "large"
        ? `No ${largeN}+ trade clusters in this filter. Switch to All qualified or another index.`
        : `No ranked runs in this filter. Finished jobs need at least ${minTrades} trades to make the board.`;
    host.innerHTML = `<p class="muted">${escapeHtml(hint)}</p>`;
    return;
  }
  const thinBlock = thin.length
    ? `<details class="bt-board-thin">
        <summary>Too few trades to rank (${thin.length})</summary>
        <div class="table-wrap">
          <table class="bt-board-table">
            <thead>
              <tr>
                <th>Rank</th>
                <th>Setup</th>
                <th>Window</th>
                <th>Trades</th>
                <th>PF</th>
                <th>Return</th>
                <th>Max DD</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>${thin.map((row) => leaderboardRowHtml(row, { thin: true })).join("")}</tbody>
          </table>
        </div>
      </details>`
    : "";
  host.innerHTML = `
    <div class="table-wrap">
      <table class="bt-board-table">
        <caption class="visually-hidden">Best qualified backtests</caption>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Setup</th>
            <th>Window</th>
            <th>Trades</th>
            <th>PF</th>
            <th>Return</th>
            <th>Max DD</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>${
          qualified.length
            ? qualified.map((row) => leaderboardRowHtml(row)).join("")
            : `<tr><td colspan="8" class="muted">No setups in this filter. Try All qualified or another index.</td></tr>`
        }</tbody>
      </table>
    </div>
    ${thinBlock}`;
  host.querySelectorAll("tr.bt-board-row").forEach((row) => {
    const open = () => {
      const id = row.getAttribute("data-run-id");
      if (id) {
        _selectionPinned = true;
        void selectBacktestRun(id, { writeUrl: true });
      }
    };
    row.addEventListener("click", open);
    row.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        open();
      }
    });
  });
  wireHorizonMenus(host);
}

function syncMonitorDisclosure() {
  const wrap = document.getElementById("backtestLiveMonitorWrap");
  if (!wrap) return;
  try {
    const section = String(new URLSearchParams(window.location.search).get("section") || "").toLowerCase();
    const hash = String(window.location.hash || "").replace(/^#/, "");
    if (["btmonitor", "backtest-monitor", "backtestlivemonitor"].includes(section) || hash === "backtestLiveMonitor") {
      wrap.open = true;
    }
  } catch {
    /* ignore */
  }
}

function renderQueue(hub, extraRuns = _monitorRuns) {
  const note = document.getElementById("backtestQueueNote");
  const list = document.getElementById("backtestQueueList");
  const rows = mergeRunLists(hub, extraRuns);
  const filter = readQueueFilter(rows);
  syncQueueFilterButtons(filter);
  const visible = filterQueueRows(rows, filter);
  const shown = !_queueShowAll && visible.length > QUEUE_VISIBLE_CAP ? visible.slice(0, QUEUE_VISIBLE_CAP) : visible;
  if (note) {
    const live = filterQueueRows(rows, "live").length;
    note.textContent = `One worker. Showing ${shown.length} of ${visible.length} in this filter · ${rows.length} recent · ${live} in line.`;
  }
  if (!list) return;
  if (!visible.length) {
    list.innerHTML = `<li class="bt-queue-empty muted">Nothing in this view. Switch filter or queue a window.</li>`;
    return;
  }
  const more =
    visible.length > shown.length
      ? `<li class="bt-queue-more"><button type="button" class="btn small secondary" id="btQueueShowMore">Show all ${visible.length}</button></li>`
      : "";
  list.innerHTML = shown
    .map((r) => {
      const selected = r.id === _selectedRunId ? " is-selected" : "";
      const label = runTitle(r) || backtestSpecSummaryLine(r.spec) || safeText(r.created_at);
      const extra = runSubtitle(r);
      const sum = r.result_summary || {};
      const pf = pfNumber(r);
      const pos = r.queue_position ? `#${safeText(r.queue_position)}` : "";
      let metrics = pos || runStatusLabel(r);
      if (r.has_result) {
        metrics = `PF ${formatPf(pf)} · ${safeText(sum.total_trades)} trades`;
      } else if (r.error_message && r.status === "failed") {
        metrics = safeText(r.error_message);
      }
      const uni = universeLabel(r);
      const cancel =
        r.status === "queued"
          ? `<button type="button" class="btn small secondary bt-cancel-run" data-cancel-id="${escapeHtml(r.id || "")}">Cancel</button>`
          : "";
      return `<li>
        <button type="button" class="bt-queue-item${selected}" data-run-id="${escapeHtml(r.id || "")}" data-status="${escapeHtml(r.status || "")}">
          <span class="bt-queue-status" data-status="${escapeHtml(r.status || "")}">${escapeHtml(runStatusLabel(r))}</span>
          <strong>${escapeHtml(label)}</strong>
          <span class="muted">${escapeHtml([extra, uni, isCampaignRun(r) ? "campaign" : "", metrics].filter(Boolean).join(" · "))}</span>
        </button>
        ${cancel}
      </li>`;
    })
    .join("") + more;
  document.getElementById("btQueueShowMore")?.addEventListener("click", () => {
    _queueShowAll = true;
    renderQueue(_hub, extraRuns);
  });
  list.querySelectorAll("[data-run-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-run-id");
      if (id) void selectBacktestRun(id, { writeUrl: true });
    });
  });
  list.querySelectorAll("[data-cancel-id]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const id = btn.getAttribute("data-cancel-id");
      if (id) void cancelBacktestRun(id);
    });
  });
}

function renderVsPrevious(briefing) {
  const host = document.getElementById("btVsPrevious");
  if (!host) return;
  const vs = briefing?.vs_previous;
  if (!vs) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = `<div class="bt-vs-card">
    <strong>This vs last</strong>
    <p>${escapeHtml(vs.headline || "")}</p>
    <p class="muted mono-nums">PF ${escapeHtml(formatSigned(vs.pf_delta))} · trades ${escapeHtml(formatSigned(vs.trades_delta, 0))} · return ${escapeHtml(formatSigned(vs.return_delta))} pp · DD ${escapeHtml(formatSigned(vs.dd_delta))} pp</p>
  </div>`;
}

function renderBriefingTiles(tiles) {
  const t = tiles && typeof tiles === "object" ? tiles : {};
  return `<div class="bt-briefing-tiles">
    <div class="bt-briefing-tile"><span>PF</span><strong class="mono-nums">${escapeHtml(formatPf(t.profit_factor_net))}</strong></div>
    <div class="bt-briefing-tile"><span>Trades</span><strong class="mono-nums">${escapeHtml(t.total_trades ?? "—")}</strong></div>
    <div class="bt-briefing-tile"><span>Return</span><strong class="mono-nums">${escapeHtml(formatBoardPct(t.total_return_net_pct))}</strong></div>
    <div class="bt-briefing-tile"><span>Max DD</span><strong class="mono-nums">${escapeHtml(formatBoardPct(t.max_drawdown_net_pct))}</strong></div>
  </div>`;
}

const CHIP_HELP = {
  not_schwab_clean: "Some prices came from a fallback feed. This is not a clean Schwab replay.",
  live_membership: "The index is today's membership, not the names in the index on each test date.",
  configured_hold: "Hold days is a setting cap. It is not how long the trades actually lasted.",
  thin_sample: "Fewer than 100 trades. Read the numbers as a hint, not a ranking result.",
};

function renderBriefingChips(chips) {
  const list = Array.isArray(chips) ? chips : [];
  if (!list.length) return "";
  return `<div class="bt-briefing-chips">${list
    .map((c) => {
      const help = CHIP_HELP[c.id] || "";
      const title = help ? ` title="${escapeHtml(help)}"` : "";
      return `<span class="bt-briefing-chip" data-tone="${toneClass(c.tone)}"${title}>${escapeHtml(c.label || "")}</span>`;
    })
    .join("")}</div>`;
}

function setupLine(run) {
  return [runTitle(run), runSubtitle(run)].filter(Boolean).join(" — ");
}

function recommendationCopy(run, primary) {
  const steps = Array.isArray(run?.next_steps) ? run.next_steps : [];
  const step = steps.find((row) => row && row.id === primary?.id) || null;
  if (!step) return "";
  return [step.why, step.action].filter(Boolean).join(" ");
}

function emitBriefingCta(run, stepId) {
  const short = run?.briefing?.short && typeof run.briefing.short === "object" ? run.briefing.short : {};
  if (short.primary?.id === stepId && short.primary.cta) {
    emitCta(short.primary.cta);
    return;
  }
  const more = Array.isArray(short.more) ? short.more.find((s) => s.id === stepId) : null;
  if (more?.cta) {
    emitCta(more.cta);
    return;
  }
  const step = (run?.next_steps || []).find((s) => s.id === stepId);
  if (step?.cta) emitCta(step.cta);
  else if (stepId === "requeue" && run?.requeue_cta) emitCta(run.requeue_cta);
}

function renderSelected(run) {
  const host = document.getElementById("backtestResultPanelBody");
  const extra = document.getElementById("btBriefingExtra");
  if (!host) return;
  if (!run) {
    host.innerHTML = `<p class="muted">Select a ranked run to see the verdict.</p>`;
    if (extra) extra.innerHTML = "";
    renderBacktestResultSummary(null);
    renderBacktestResultRaw(null, "No run yet.");
    renderVsPrevious(null);
    return;
  }
  const outcome = run.outcome || {};
  const briefing = run.briefing || {};
  const short =
    briefing.short && typeof briefing.short === "object"
      ? briefing.short
      : {
          verdict: outcome.headline || runTitle(run) || "Result",
          tone: outcome.tone,
          tiles: run.result_summary || run.result || {},
          chips: [],
          primary: null,
          more: [],
        };
  const errorLine =
    String(outcome.tone || "") === "bad" &&
    run.error_message &&
    run.error_message !== outcome.summary
      ? `<p class="bt-briefing-error">${escapeHtml(run.error_message)}</p>`
      : "";
  const primary = short.primary;
  const why = recommendationCopy(run, primary);
  const primaryBtn =
    primary?.cta?.label
      ? `<button type="button" class="btn primary bt-cta" data-step-id="${escapeHtml(primary.id || "")}" id="btBriefingPrimaryBtn">${escapeHtml(primary.cta.label)}</button>`
      : primary?.title
        ? `<p class="muted">${escapeHtml(primary.title)}</p>`
        : "";
  const tested = setupLine(run);
  host.innerHTML = `
    <div class="bt-briefing-short" data-tone="${toneClass(short.tone || outcome.tone)}">
      <p class="bt-briefing-kicker">${escapeHtml(runStatusLabel(run))}</p>
      <strong class="bt-briefing-verdict">${escapeHtml(short.verdict || outcome.headline || "Result")}</strong>
      ${tested ? `<p class="bt-briefing-setup">${escapeHtml(tested)}</p>` : ""}
      ${outcome.summary ? `<p class="bt-briefing-summary">${escapeHtml(outcome.summary)}</p>` : ""}
      ${errorLine}
      ${renderBriefingTiles(short.tiles)}
      ${renderBriefingChips(short.chips)}
      ${why ? `<p class="bt-briefing-why">${escapeHtml(why)}</p>` : ""}
      <div class="bt-briefing-primary">${primaryBtn}</div>
    </div>
  `;
  if (extra) {
    extra.innerHTML = `
      ${renderMoreActions(short.more)}
      ${renderCountRow("Funnel", briefing.funnel)}
      ${renderCountRow("Exits", briefing.exits)}
      ${renderSample(briefing.trades_sample)}
    `;
  }
  const bindCtas = (root) => {
    root?.querySelectorAll(".bt-cta").forEach((btn) => {
      btn.addEventListener("click", () => emitBriefingCta(run, btn.getAttribute("data-step-id")));
    });
  };
  bindCtas(host);
  bindCtas(extra);
  if (run.result) {
    renderBacktestResultSummary(run.result);
    renderBacktestResultRaw(run.result, "");
  } else {
    renderBacktestResultSummary(null);
    renderBacktestResultRaw(null, run.error_message || "No run yet.");
  }
  renderVsPrevious(briefing);
}

function renderRelated(hub) {
  const host = document.getElementById("backtestRelatedCards");
  if (!host) return;
  const cards = Array.isArray(hub?.related) ? hub.related : [];
  if (!cards.length) {
    host.innerHTML = `<p class="muted">No related evidence yet.</p>`;
    return;
  }
  host.innerHTML = cards
    .map((c, idx) => {
      const exists = Boolean(c.exists);
      const name = c.artifact_name ? escapeHtml(c.artifact_name) : "";
      return `<article class="bt-related-card" data-exists="${exists ? "true" : "false"}">
        <p class="bt-related-kind muted">${escapeHtml(c.title || c.kind || "Evidence")}</p>
        <strong>${escapeHtml(c.verdict || "Not generated yet")}</strong>
        <p class="muted">${escapeHtml(c.next_step || "")}</p>
        ${c.generated_at ? `<p class="muted small">${escapeHtml(c.generated_at)}</p>` : ""}
        ${
          exists && name
            ? `<details class="bt-related-details" data-artifact="${name}" data-idx="${idx}">
                <summary>View details</summary>
                <div class="bt-related-body">Loading…</div>
              </details>`
            : ""
        }
      </article>`;
    })
    .join("");
  host.querySelectorAll(".bt-related-details").forEach((details) => {
    details.addEventListener("toggle", () => {
      if (!details.open) return;
      const name = details.getAttribute("data-artifact");
      const host = details.querySelector(".bt-related-body");
      if (!name || !host || host.dataset.loaded) return;
      void loadArtifactDetail(name, host);
    });
  });
}

async function loadArtifactDetail(name, host) {
  const out = await api.get(`/api/backtest-artifacts/${encodeURIComponent(name)}`);
  if (!out.ok) {
    host.innerHTML = `<p class="muted">${safeText(out.user_message || out.error || "Could not load details.")}</p>`;
    return;
  }
  host.dataset.loaded = "1";
  const summary = out.data?.summary && typeof out.data.summary === "object" ? out.data.summary : out.data || {};
  host.innerHTML = relatedEvidenceHtml(summary);
}

function writeRunToUrl(runId) {
  try {
    const url = new URL(window.location.href);
    if (runId) url.searchParams.set("run", runId);
    else url.searchParams.delete("run");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    /* ignore */
  }
}

export function runIdFromUrl() {
  try {
    return new URL(window.location.href).searchParams.get("run") || "";
  } catch {
    return "";
  }
}

export async function cancelBacktestRun(runId) {
  const out = await api.post(`/api/backtest-runs/${encodeURIComponent(runId)}/cancel`, {});
  if (!out.ok) {
    window.dispatchEvent(
      new CustomEvent("backtest_toast", {
        detail: { message: safeText(out.user_message || out.error || "Could not cancel.") },
      }),
    );
    return;
  }
  await refreshBacktestHub();
}

export async function selectBacktestRun(runId, { writeUrl = false } = {}) {
  const sameRun = String(_selectedRun?.id || "") === String(runId || "");
  _selectedRunId = runId;
  if (writeUrl) _selectionPinned = true;
  const host = document.getElementById("backtestResultPanelBody");
  if (host && !sameRun) host.innerHTML = `<p class="muted">Loading run…</p>`;
  if (writeUrl) writeRunToUrl(runId);
  const out = await api.get(`/api/backtest-runs/${encodeURIComponent(runId)}`);
  if (!out.ok) {
    if (host) {
      setAsyncState(host, ASYNC_ERROR, {
        message: safeText(out.user_message || out.error || "Could not open that run."),
        onRetry: () => void selectBacktestRun(runId, { writeUrl }),
      });
    }
    return;
  }
  _selectedRun = out.data || null;
  renderSelected(_selectedRun);
  renderLanding(_hub);
  renderQueue(_hub, _monitorRuns);
}

function scheduleActivePoll(hub) {
  if (_pollTimer) {
    clearTimeout(_pollTimer);
    _pollTimer = null;
  }
  const rows = mergeRunLists(hub, _monitorRuns);
  const active = rows.some((r) => r.status === "queued" || r.status === "running");
  if (!active) return;
  _pollTimer = setTimeout(() => {
    void refreshBacktestHub();
  }, 4000);
}

function continueRecommendation() {
  if (
    _selectedRun &&
    !isFinishedRun(_selectedRun) &&
    (_selectedRun.status === "queued" || _selectedRun.status === "running")
  ) {
    return;
  }
  const finished = landingSubject(_hub);
  const primary = finished?.briefing?.short?.primary;
  if (primary?.cta && finished) {
    emitBriefingCta(finished, primary.id);
    return;
  }
  document.querySelector("[data-bt-queue-years='5']")?.click();
}

function wireLandingNext() {
  const button = document.getElementById("backtestLandingNext");
  if (!button || button.dataset.wired === "1") return;
  button.dataset.wired = "1";
  button.addEventListener("click", () => {
    const primary = landingSubject(_hub)?.briefing?.short?.primary;
    if (primary?.cta?.kind === "queue_spec") {
      button.disabled = true;
      button.textContent = "Queuing the recommendation…";
    }
    continueRecommendation();
  });
}

function wireQueueFilters() {
  const host = document.getElementById("btQueueFilters");
  if (!host || host.dataset.wired === "1") return;
  host.dataset.wired = "1";
  host.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-bt-queue-filter]");
    if (!btn) return;
    const next = btn.getAttribute("data-bt-queue-filter") || "all";
    setQueueFilter(next, mergeRunLists(_hub, _monitorRuns));
  });
}

function persistBoardFilters() {
  try {
    sessionStorage.setItem(BOARD_WINDOW_KEY, _boardWindow);
    sessionStorage.setItem(BOARD_UNIVERSE_KEY, _boardUniverse);
    sessionStorage.setItem(BOARD_SAMPLE_KEY, _boardSample);
  } catch {
    /* ignore */
  }
}

function restoreBoardFilters() {
  try {
    const windowSaved = sessionStorage.getItem(BOARD_WINDOW_KEY);
    const universeSaved = sessionStorage.getItem(BOARD_UNIVERSE_KEY);
    const sampleSaved = sessionStorage.getItem(BOARD_SAMPLE_KEY);
    if (windowSaved) _boardWindow = windowSaved;
    if (universeSaved) _boardUniverse = universeSaved;
    if (sampleSaved === "all" || sampleSaved === "large") _boardSample = sampleSaved;
  } catch {
    /* ignore */
  }
}

function applyDefaultSelection(hub, { force = false } = {}) {
  const fromUrl = runIdFromUrl();
  if (fromUrl) {
    _selectedRunId = fromUrl;
    _selectionPinned = true;
    return;
  }
  if (_selectedRunId && !force) return;
  const top = visibleQualifiedRows(hub)[0];
  _selectedRunId = top?.id ? String(top.id) : hub?.latest_success?.id ? String(hub.latest_success.id) : "";
}

function wireLeaderboardFilters() {
  restoreBoardFilters();
  const host = document.getElementById("btLeaderboardFilterStack") || document.getElementById("btLeaderboardFilters");
  if (!host || host.dataset.wired === "1") return;
  host.dataset.wired = "1";
  host.addEventListener("click", (ev) => {
    const windowBtn = ev.target.closest("[data-bt-board-window]");
    const universeBtn = ev.target.closest("[data-bt-board-universe]");
    const sampleBtn = ev.target.closest("[data-bt-board-sample]");
    if (!windowBtn && !universeBtn && !sampleBtn) return;
    if (windowBtn) _boardWindow = windowBtn.getAttribute("data-bt-board-window") || "all";
    if (universeBtn) _boardUniverse = universeBtn.getAttribute("data-bt-board-universe") || "all";
    if (sampleBtn) _boardSample = sampleBtn.getAttribute("data-bt-board-sample") || "large";
    persistBoardFilters();
    if (!_selectionPinned) applyDefaultSelection(_hub, { force: true });
    renderLanding(_hub);
    if (!_selectionPinned && _selectedRunId) void selectBacktestRun(_selectedRunId);
  });
}

function studyAnchorSpec() {
  const spec = collectBacktestSpecFromForm();
  if (!spec.start_date || !spec.end_date) {
    const window = previewBacktestWindowYears(5);
    spec.start_date = window.start;
    spec.end_date = window.end;
  }
  const years = Number(spec.end_date?.slice(0, 4)) - Number(spec.start_date?.slice(0, 4));
  if (Number.isFinite(years) && years >= 5) {
    spec.overrides = { ...(spec.overrides || {}), skip_mirofish: true };
  }
  return spec;
}

function renderStudy(view) {
  const hypothesis = document.getElementById("btStudyHypothesis");
  const rationale = document.getElementById("btStudyRationale");
  const status = document.getElementById("btStudyStatus");
  const effects = document.getElementById("btStudyEffects");
  const button = document.getElementById("btStudyNextBtn");
  if (hypothesis) hypothesis.textContent = view?.hypothesis || "No study yet. The first replay is the anchor.";
  if (rationale) rationale.textContent = view?.rationale || "After each replay, the next test changes one factor.";
  if (status) {
    const used = Number(view?.trials_used || 0);
    const budget = Number(view?.budget || 12);
    const family = view?.family ? ` · ${view.family}` : "";
    const model = view?.used_model ? " · model" : "";
    status.textContent = view?.study_id
      ? `Study ${view.study_id} · ${used}/${budget} trials${family}${model}`
      : "Not started. Promotion still requires the multi-era gates.";
  }
  if (effects) {
    const rows = Array.isArray(view?.factor_effects) ? view.factor_effects : [];
    effects.innerHTML = rows
      .map((row) => {
        const delta = row.best_utility_delta;
        const deltaText = Number.isFinite(Number(delta)) ? Number(delta).toFixed(3) : "—";
        return `<li><span>${escapeHtml(row.label || row.family || "Factor")}</span><span>${escapeHtml(row.note || deltaText)}</span></li>`;
      })
      .join("");
  }
  if (button) {
    const action = String(view?.action || "start");
    const canQueue = action === "start" || action === "queue";
    const restart = action === "stop" || action === "hold";
    button.disabled = !canQueue && !restart;
    if (action === "queue") button.textContent = "Queue next";
    else if (action === "wait") button.textContent = "Waiting on this replay";
    else if (restart) button.textContent = "Start a new study";
    else button.textContent = "Start study";
  }
}

const STUDY_AUTO_KEY = "btStudyAuto";

async function postStudyNext(restart) {
  if (_studyBusy) return "";
  _studyBusy = true;
  const button = document.getElementById("btStudyNextBtn");
  if (button) button.disabled = true;
  try {
    const body = { auto: Boolean(document.getElementById("btStudyAuto")?.checked), restart: Boolean(restart) };
    const action = String(_studyView?.action || "start");
    if (action === "start" || restart || !_studyView?.study_id) body.spec = studyAnchorSpec();
    const out = await api.post("/api/backtest-study/next", body);
    if (!out.ok) {
      const status = document.getElementById("btStudyStatus");
      if (status) status.textContent = safeText(out.user_message || out.error || "Could not queue the next test.");
      if (button) button.disabled = false;
      return "";
    }
    _studyView = out.data || _studyView;
    renderStudy(_studyView);
    return String(out.data?.queued_run_id || "");
  } finally {
    _studyBusy = false;
  }
}

function wireStudyPanel() {
  const button = document.getElementById("btStudyNextBtn");
  const autoBox = document.getElementById("btStudyAuto");
  if (autoBox && autoBox.dataset.wired !== "1") {
    autoBox.dataset.wired = "1";
    try {
      if (localStorage.getItem(STUDY_AUTO_KEY) === "0") autoBox.checked = false;
    } catch {
      /* keep the default, which is on */
    }
    autoBox.addEventListener("change", () => {
      try {
        localStorage.setItem(STUDY_AUTO_KEY, autoBox.checked ? "1" : "0");
      } catch {
        /* ignore */
      }
    });
  }
  if (!button || button.dataset.wired === "1") return;
  button.dataset.wired = "1";
  button.addEventListener("click", () => {
    const action = String(_studyView?.action || "start");
    void postStudyNext(action === "stop" || action === "hold").then((runId) =>
      refreshBacktestHub(runId ? { followRunId: runId } : {}),
    );
  });
}

async function refreshStudyPanel() {
  wireStudyPanel();
  const out = await api.get("/api/backtest-study");
  if (!out.ok) {
    const status = document.getElementById("btStudyStatus");
    if (status) status.textContent = safeText(out.user_message || out.error || "Could not load the study.");
    return;
  }
  _studyView = out.data || {};
  renderStudy(_studyView);
  const auto = document.getElementById("btStudyAuto")?.checked;
  if (!auto || _studyView.action !== "queue" || _studyBusy) return;
  const key = `${_studyView.study_id}|${_studyView.hypothesis}|${_studyView.trials_used}`;
  if (_studyAutoKey === key) return;
  _studyAutoKey = key;
  const runId = await postStudyNext(false);
  if (runId) await refreshBacktestHub({ followRunId: runId });
}

export async function refreshBacktestHub({ followRunId = "" } = {}) {
  const landing = document.getElementById("backtestSummaryLanding");
  wireQueueFilters();
  wireLeaderboardFilters();
  wireLandingNext();
  syncMonitorDisclosure();
  const [hubOut, listOut] = await Promise.all([
    api.get("/api/backtest-hub?limit=50"),
    api.get(`/api/backtest-runs?limit=${MONITOR_RUN_CAP}`),
  ]);
  if (!hubOut.ok) {
    const message = safeText(hubOut.user_message || hubOut.error || "Request failed.");
    const verdictEl = document.getElementById("backtestZoneVerdict");
    const detailEl = document.getElementById("backtestZoneDetail");
    if (verdictEl) verdictEl.textContent = "Could not load backtest home.";
    if (detailEl) detailEl.textContent = message;
    const board = document.getElementById("btLeaderboardBody");
    if (board) board.textContent = message;
    if (landing) landing.setAttribute("data-async-state", "error");
    return;
  }
  _hub = hubOut.data || {};
  _monitorRuns = listOut.ok && Array.isArray(listOut.data) ? listOut.data : _hub.runs || [];
  if (followRunId) {
    _selectedRunId = String(followRunId);
    _selectionPinned = true;
    _queueFilter = "live";
    try {
      sessionStorage.setItem(QUEUE_FILTER_KEY, "live");
    } catch {
      /* ignore */
    }
    writeRunToUrl(_selectedRunId);
  } else {
    applyDefaultSelection(_hub);
  }
  renderLanding(_hub);
  renderQueue(_hub, _monitorRuns);
  renderRelated(_hub);
  renderBacktestRunCompare(_monitorRuns);
  void refreshStudyPanel();
  if (_selectedRunId) {
    const listed = mergeRunLists(_hub, _monitorRuns).find((r) => r.id === _selectedRunId);
    const keepPinned = _selectionPinned && listed && !isFinishedRun(listed);
    if (listed?.has_result || listed?.status === "failed" || listed?.status === "cancelled" || !listed || keepPinned) {
      void selectBacktestRun(_selectedRunId);
    } else if (isFinishedRun(listed)) {
      _selectedRun = listed;
      renderSelected(_selectedRun);
    } else {
      const fallback = _hub.latest_success?.id;
      if (fallback && fallback !== _selectedRunId) {
        _selectedRunId = String(fallback);
        void selectBacktestRun(_selectedRunId);
      } else {
        _selectedRun = listed;
        renderSelected(_selectedRun);
      }
    }
  } else {
    _selectedRun = null;
    renderSelected(null);
  }
  scheduleActivePoll(_hub);
}
