/**
 * Quick-check panel — fast `/api/check/<ticker>` lookup that renders a
 * summary card and (when LightweightCharts is loaded) a small candle
 * chart underneath. The chart lifecycle is tracked locally in
 * `_activeChart` so re-runs replace the existing instance cleanly.
 *
 * In-flight requests are keyed by `_quickCheckGen` so a slower AAPL
 * response cannot overwrite a later MSFT check.
 */

import { api } from "../modules/api.js";
import { getLightweightChartsProps } from "../modules/chartThemeAdapters.js";
import { humanizeFieldLabel } from "../modules/humanize.js";
import { YourThemeConfig } from "../modules/YourThemeConfig.js";
import { safeText } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import { setResearchPanelStatus } from "../modules/researchStatus.js";
import { buildOperatorAlertHtml } from "../modules/asyncState.js";
import { decorateGlossary } from "../modules/glossary.js";
import { state } from "../modules/state.js";
import {
  formatAsOf,
  payloadTicker,
  providerLabel,
  syncResearchTickerRail,
} from "../modules/researchTickerFlow.js";

export function renderQuickCheckCard(data, error) {
  const ph = document.getElementById("checkPlaceholder");
  const sum = document.getElementById("checkSummary");
  if (!sum) return;
  const gradeCard = document.getElementById("debateGradeCard");
  if (error) {
    if (ph) { ph.textContent = error; ph.classList.remove("hidden"); }
    sum.classList.add("hidden"); sum.innerHTML = "";
    if (gradeCard) { gradeCard.classList.add("hidden"); gradeCard.innerHTML = ""; }
    return;
  }
  if (ph) ph.classList.add("hidden");
  const d = data || {};

  const title = d.title || d.ticker || "Quick Check";
  const desc = (d.description || "").replace(/\*\*/g, "");
  const fields = d.fields || [];

  let fieldsHtml = "";
  if (fields.length) {
    fieldsHtml = '<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; margin-top: 10px;">';
    for (const f of fields) {
      const val = decorateGlossary((f.value || "").replace(/\*\*/g, "").replace(/\n/g, "<br>"));
      fieldsHtml += `<div class="preset-subsection" style="padding: 10px;">
        <h3 style="margin: 0 0 6px; font-size: 0.82rem;">${safeText(f.name)}</h3>
        <div style="font-size: 0.84rem; color: ${YourThemeConfig.palette.text}; line-height: 1.5;">${val}</div>
      </div>`;
    }
    fieldsHtml += "</div>";
  } else {
    const items = [];
    const price = d.price ?? d.current_price ?? d.last_price;
    const stage2 = d.stage_2 ?? d.is_stage_2;
    const vcp = d.vcp ?? d.vcp_detected;
    const score = d.signal_score ?? d.score;
    const sector = d.sector ?? d.sector_etf;
    if (price != null) items.push(`<li><strong>Price:</strong> $${Number(price).toFixed(2)}</li>`);
    if (stage2 != null) items.push(`<li><strong>In uptrend:</strong> <span class="pill ${stage2 ? 'good' : 'bad'} small">${stage2 ? 'Yes' : 'No'}</span></li>`);
    if (vcp != null) items.push(`<li><strong>Volatility pattern:</strong> <span class="pill ${vcp ? 'good' : 'bad'} small">${vcp ? 'Detected' : 'None'}</span></li>`);
    if (score != null) items.push(`<li><strong>Signal score:</strong> ${Number(score).toFixed(1)}/100</li>`);
    if (sector) items.push(`<li><strong>Sector:</strong> ${safeText(sector)}</li>`);
    const hiddenKeys = new Set(["title", "description", "color", "timestamp", "ticker", "stage_2", "is_stage_2", "vcp", "vcp_detected", "signal_score", "score", "sector", "sector_etf", "price", "current_price", "last_price", "as_of", "provider", "used_fallback", "history_price_basis", "data_quality"]);
    Object.entries(d).forEach(([k, v]) => {
      if (v != null && typeof v !== "object" && !hiddenKeys.has(k)) {
        items.push(`<li><strong>${safeText(humanizeFieldLabel(k))}:</strong> ${safeText(String(v))}</li>`);
      }
    });
    if (items.length) fieldsHtml = `<ul class="tool-summary-list">${items.join("")}</ul>`;
  }

  sum.classList.remove("hidden");
  sum.innerHTML = `
    <h4 class="tool-summary-title">${safeText(title)}</h4>
    ${desc ? `<p class="tool-summary-p" style="margin-bottom: 4px;">${safeText(desc)}</p>` : ""}
    ${fieldsHtml}
  `;
  renderDebateGradeCard(d.debate_grade);
}

function renderDebateGradeCard(grade) {
  const card = document.getElementById("debateGradeCard");
  if (!card) return;
  if (!grade || typeof grade !== "object") {
    card.classList.add("hidden");
    card.innerHTML = "";
    return;
  }
  const letter = grade.qual_letter ? safeText(grade.qual_letter) : "—";
  const pillars = [
    ["Trend", grade.qual_trend, grade.qual_coverage_trend],
    ["Setup", grade.qual_setup, grade.qual_coverage_setup],
    ["Risk", grade.qual_asymmetry, grade.qual_coverage_asymmetry],
    ["Extension risk", grade.qual_crowding, grade.qual_coverage_crowding],
  ];
  const bars = pillars.map(([name, score, coverage]) => {
    if (score == null || Number.isNaN(Number(score))) {
      return `<div class="debate-grade-pillar"><span>${safeText(name)}</span><span class="muted">Abstained</span></div>`;
    }
    const width = Math.max(0, Math.min(100, Number(score)));
    const cov = coverage == null ? "" : ` <span class="muted">coverage ${Math.round(Number(coverage) * 100)}%</span>`;
    return `<div class="debate-grade-pillar">
      <span>${safeText(name)}${cov}</span>
      <span class="debate-grade-bar" style="--debate-fill:${width}%"><b>${width.toFixed(0)}</b></span>
    </div>`;
  }).join("");
  const seats = Array.isArray(grade.seats) ? grade.seats : [];
  const seatHtml = seats.map((seat) => (
    `<li><strong>${safeText(seat.seat)}:</strong> ${safeText(seat.line)}</li>`
  )).join("");
  let disagree = "No committee votes on this bar.";
  if (grade.disagreement_source === "votes" && grade.qual_disagreement != null) {
    disagree = `Disagreement ${Number(grade.qual_disagreement).toFixed(2)}`;
  }
  const business = grade.qual_business == null
    ? "Abstained"
    : String(Math.round(Number(grade.qual_business)));
  const committee = grade.committee && grade.committee.chair
    ? `<p class="debate-grade-chair">Committee: ${safeText(grade.committee.chair)}</p>`
    : "";
  const debateScore = grade.debate_score == null
    ? ""
    : `<p class="debate-grade-disagree">Committee score ${Number(grade.debate_score).toFixed(0)} (beside the checklist, does not change rank)</p>`;
  card.classList.remove("hidden");
  card.innerHTML = `
    <p class="debate-grade-label">${safeText(grade.label || "Uncalibrated shadow. Does not change rank.")}</p>
    <p class="debate-grade-letter" data-letter="${letter}">${letter}</p>
    <p class="debate-grade-chair">${safeText(grade.chair || "")}</p>
    <div class="debate-grade-pillars">${bars}</div>
    <p class="debate-grade-business muted">Business: ${safeText(business)}</p>
    ${committee}
    ${debateScore}
    <p class="debate-grade-disagree">${safeText(disagree)}</p>
    ${seatHtml ? `<ul class="debate-grade-seats">${seatHtml}</ul>` : ""}
  `;
}

let _activeChart = null;
let _quickCheckGen = 0;
let _quickCheckBusy = false;

function isCurrentGen(gen) {
  return gen === _quickCheckGen;
}

export async function renderTickerChart(ticker, gen = _quickCheckGen) {
  const container = document.getElementById("tickerChartContainer");
  if (!container || typeof LightweightCharts === "undefined") return;
  const requested = String(ticker || "").trim().toUpperCase();
  container.classList.remove("hidden");
  container.innerHTML = "";

  const out = await api.get(`/api/chart/${encodeURIComponent(requested)}`);
  if (!isCurrentGen(gen)) return;
  const chartTicker = payloadTicker(out.data, requested);
  if (chartTicker && chartTicker !== requested) return;
  if (!out.ok || !out.data?.candles?.length) {
    const reason = out?.error || out?.data?.recovery?.summary || "";
    const reasonLine = reason ? `<div class="muted" style="padding:0 12px 12px; font-size:0.82rem;">${safeText(reason)}</div>` : "";
    container.innerHTML = `
      <div class="muted" style="padding:12px 12px 4px;">No chart data available for ${safeText(requested)}.</div>
      ${reasonLine}
    `;
    return;
  }

  const chartTheme = getLightweightChartsProps();
  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: 280,
    layout: chartTheme.layout,
    grid: chartTheme.grid,
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: chartTheme.rightPriceScale,
    timeScale: { ...chartTheme.timeScale, timeVisible: false },
  });
  const candleSeries = chart.addCandlestickSeries(chartTheme.candlestick);
  candleSeries.setData(out.data.candles);

  const volSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "",
    scaleMargins: { top: 0.85, bottom: 0 },
  });
  volSeries.setData(out.data.candles.map((c) => ({
    time: c.time,
    value: c.volume,
    color: c.close >= c.open ? chartTheme.volumeColors.up : chartTheme.volumeColors.down,
  })));

  chart.timeScale().fitContent();
  _activeChart = chart;

  const ro = new ResizeObserver(() => {
    if (_activeChart) _activeChart.applyOptions({ width: container.clientWidth });
  });
  ro.observe(container);
}

function paintQuickCheckSurface(stateName, title, detail, extras = {}) {
  const hideSnapshot = extras.hidden === true || stateName === "empty" || stateName === "loading";
  return setResearchPanelStatus({
    stripId: "quickCheckStatusStrip",
    snapshotId: "quickCheckSnapshot",
    sectionId: "quickCheckSection",
    stateName,
    title,
    detail,
    hint: extras.hint || "chart · score · evidence",
    output: extras.output,
    data: extras.data,
    action: extras.action,
    meter: extras.meter,
    hidden: hideSnapshot,
  });
}

function dataTileFromPayload(data) {
  const asOf = formatAsOf(data?.as_of);
  const usedFallback = Boolean(data?.used_fallback);
  const label = providerLabel(data?.provider, usedFallback);
  return {
    value: label,
    sub: asOf ? `as of ${asOf}` : "last daily bar",
    tone: usedFallback ? "warn" : "success",
  };
}

export function applyQuickCheckTickerStale(inputTicker) {
  if (_quickCheckBusy) return;
  const shown = String(state.lastQuickCheckTicker || "").trim().toUpperCase();
  const input = String(inputTicker || "").trim().toUpperCase();
  if (!shown || shown === input) return;
  paintQuickCheckSurface(
    "partial",
    `Showing ${shown}. Press Check for ${input || "the new ticker"}.`,
    `Quick check still displays ${shown}. Chart and score are not ${input || "the current input"}.`,
    {
      output: { value: "Stale", sub: "check", tone: "warn" },
      data: { value: "Stale", sub: `showing ${shown}`, tone: "warn" },
      action: { value: "Check", sub: input || "new ticker", tone: "warn" },
    },
  );
}

export async function quickCheck() {
  const ticker = document.getElementById("tickerInput").value.trim().toUpperCase();
  if (!ticker) {
    paintQuickCheckSurface(
      "empty",
      "No ticker entered.",
      "Enter a ticker to load chart, score, and a brief card.",
      {
        output: { value: "None", sub: "check" },
        data: { value: "—", sub: "ticker" },
        action: { value: "Wait", sub: "enter symbol" },
      },
    );
    return;
  }
  const gen = ++_quickCheckGen;
  _quickCheckBusy = true;
  const ph = document.getElementById("checkPlaceholder");
  renderQuickCheckCard(null, "");
  paintQuickCheckSurface(
    "loading",
    `Checking ${ticker}.`,
    "Loading quick score and chart context.",
    {
      output: { value: "…", sub: "check" },
      data: { value: "…", sub: "market" },
      action: { value: "Wait", sub: "hold" },
    },
  );
  if (ph) {
    ph.setAttribute("data-async-state", "loading");
    ph.innerHTML = `<span class="async-state async-state--loading" role="status">
      <span class="async-spinner" aria-hidden="true"></span>
      <span>Checking ${safeText(ticker)}…</span>
    </span>`;
  }
  const out = await api.get(`/api/check/${ticker}`);
  if (!isCurrentGen(gen)) return;
  if (!out.ok) {
    _quickCheckBusy = false;
    const msg = out.user_message || out.error || "Request failed";
    renderQuickCheckCard(null, "");
    if (ph) {
      ph.setAttribute("data-async-state", "error");
      ph.innerHTML = buildOperatorAlertHtml({
        tone: "bad",
        headline: "Data unavailable",
        detail: `Check failed: ${safeText(String(msg))}`,
        retry: true,
        retryAttr: "data-check-retry",
      });
      ph.querySelector("[data-check-retry]")?.addEventListener("click", () => void quickCheck());
    }
    paintQuickCheckSurface(
      "error",
      `Check failed for ${ticker}.`,
      safeText(String(msg)),
      {
        output: { value: "—", sub: "check" },
        data: { value: "—", sub: "market" },
        action: { value: "Retry", sub: "reload", tone: "bad" },
      },
    );
    logEvent({ kind: "system", severity: "error", message: `Check ${ticker} failed: ${out.error}` });
    return;
  }
  const shownTicker = payloadTicker(out.data, ticker);
  if (shownTicker && shownTicker !== ticker) {
    _quickCheckBusy = false;
    return;
  }
  const dataQuality = out.data?.data_quality || "";
  state.lastQuickCheckTicker = shownTicker || ticker;
  state.lastQuickCheckQuality = dataQuality || "ok";
  state.lastQuickCheckAt = out.data?.as_of || new Date().toISOString();
  state.lastQuickCheckProvider = out.data?.provider || "";
  renderQuickCheckCard(out.data, null);
  // Surface data_quality verbatim: a payload with no real market data must
  // never be presented as a fresh, high-confidence result.
  if (dataQuality) {
    if (ph) ph.setAttribute("data-async-state", "success");
    paintQuickCheckSurface(
      "partial",
      `${shownTicker}: no market data found.`,
      `Symbol may be invalid or unsupported. Data quality: ${safeText(String(dataQuality))}`,
      {
        output: { value: "Degraded", sub: "check" },
        data: { value: "Missing", sub: safeText(String(dataQuality)), tone: "bad" },
        action: { value: "Verify", sub: "symbol" },
        meter: null,
      },
    );
    logEvent({
      kind: "system",
      severity: "warn",
      message: `Check ${shownTicker}: Data quality: ${safeText(String(dataQuality))}`,
    });
  } else {
    if (ph) ph.setAttribute("data-async-state", "success");
    const scoreRaw = out.data?.signal_score ?? out.data?.score;
    const scoreNum = Number(scoreRaw);
    const dataTile = dataTileFromPayload(out.data);
    const asOf = formatAsOf(out.data?.as_of);
    paintQuickCheckSurface(
      "success",
      `${shownTicker} quick check ready.`,
      asOf
        ? `Last daily bar ${asOf} — review the brief card and chart before deeper research.`
        : "Review the brief card and chart before deeper research.",
      {
        output: { value: "Ready", sub: "check" },
        data: dataTile,
        action: { value: "Review", sub: "brief" },
        meter: Number.isFinite(scoreNum)
          ? { value: scoreNum, label: "Signal score", unit: "points" }
          : null,
      },
    );
  }
  syncResearchTickerRail(shownTicker || ticker, { sourceId: "tickerInput" });
  _quickCheckBusy = false;
  void renderTickerChart(shownTicker || ticker, gen);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("research_summary_refresh"));
    // Panels that need the checked symbol but must not slow the check itself
    // (e.g. event risk, which may build a valuation) load off this event.
    window.dispatchEvent(new CustomEvent("research_quick_check_complete", {
      detail: { ticker: shownTicker || ticker },
    }));
  }
  logEvent({ kind: "system", severity: "info", message: `Check complete for ${shownTicker || ticker}.` });
}
