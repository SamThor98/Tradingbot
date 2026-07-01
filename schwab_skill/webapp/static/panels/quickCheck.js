/**
 * Quick-check panel — fast `/api/check/<ticker>` lookup that renders a
 * summary card and (when LightweightCharts is loaded) a small candle
 * chart underneath. The chart lifecycle is tracked locally in
 * `_activeChart` so re-runs replace the existing instance cleanly.
 */

import { api } from "../modules/api.js";
import { getLightweightChartsProps } from "../modules/chartThemeAdapters.js";
import { humanizeFieldLabel } from "../modules/humanize.js";
import { YourThemeConfig } from "../modules/YourThemeConfig.js";
import { safeText, prettyJson } from "../modules/format.js";
import { logEvent } from "../modules/logger.js";
import { setResearchStatusStrip } from "../modules/researchStatus.js";

export function renderQuickCheckCard(data, error) {
  const ph = document.getElementById("checkPlaceholder");
  const sum = document.getElementById("checkSummary");
  const det = document.getElementById("checkJsonDetails");
  const pre = document.getElementById("checkOutput");
  if (!sum) return;
  if (error) {
    if (ph) { ph.textContent = error; ph.classList.remove("hidden"); }
    sum.classList.add("hidden"); sum.innerHTML = "";
    if (det) det.classList.add("hidden");
    if (pre) pre.textContent = "";
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
      const val = (f.value || "").replace(/\*\*/g, "").replace(/\n/g, "<br>");
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
    const hiddenKeys = new Set(["title", "description", "color", "timestamp", "ticker", "stage_2", "is_stage_2", "vcp", "vcp_detected", "signal_score", "score", "sector", "sector_etf", "price", "current_price", "last_price"]);
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
  if (det) det.classList.remove("hidden");
  if (pre) pre.textContent = prettyJson(data);
}

let _activeChart = null;

export async function renderTickerChart(ticker) {
  const container = document.getElementById("tickerChartContainer");
  if (!container || typeof LightweightCharts === "undefined") return;
  container.classList.remove("hidden");
  container.innerHTML = "";

  const out = await api.get(`/api/chart/${encodeURIComponent(ticker)}`);
  if (!out.ok || !out.data?.candles?.length) {
    const reason = out?.error || out?.data?.recovery?.summary || "";
    const reasonLine = reason ? `<div class="muted" style="padding:0 12px 12px; font-size:0.82rem;">${safeText(reason)}</div>` : "";
    container.innerHTML = `
      <div class="muted" style="padding:12px 12px 4px;">No chart data available for ${safeText(ticker)}.</div>
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

export async function quickCheck() {
  const ticker = document.getElementById("tickerInput").value.trim().toUpperCase();
  if (!ticker) {
    setResearchStatusStrip(
      "quickCheckStatusStrip",
      "empty",
      "No ticker entered.",
      "Enter a ticker to load chart, score, and raw evidence.",
    );
    return;
  }
  const ph = document.getElementById("checkPlaceholder");
  renderQuickCheckCard(null, "");
  setResearchStatusStrip(
    "quickCheckStatusStrip",
    "loading",
    `Checking ${ticker}.`,
    "Loading quick score, chart context, and raw evidence.",
  );
  if (ph) {
    ph.setAttribute("data-async-state", "loading");
    ph.innerHTML = `<span class="async-state async-state--loading" role="status">
      <span class="async-spinner" aria-hidden="true"></span>
      <span>Checking ${safeText(ticker)}…</span>
    </span>`;
  }
  const out = await api.get(`/api/check/${ticker}`);
  if (!out.ok) {
    const msg = out.user_message || out.error || "Request failed";
    renderQuickCheckCard(null, "");
    if (ph) {
      ph.setAttribute("data-async-state", "error");
      ph.innerHTML = `<span class="async-state async-state--error" role="alert">
        <span>Check failed: ${safeText(String(msg))}</span>
        <button type="button" class="btn small secondary" data-check-retry>Retry</button>
      </span>`;
      ph.querySelector("[data-check-retry]")?.addEventListener("click", () => void quickCheck());
    }
    setResearchStatusStrip(
      "quickCheckStatusStrip",
      "error",
      `Check failed for ${ticker}.`,
      safeText(String(msg)),
    );
    logEvent({ kind: "system", severity: "error", message: `Check ${ticker} failed: ${out.error}` });
    return;
  }
  if (ph) ph.setAttribute("data-async-state", "success");
  renderQuickCheckCard(out.data, null);
  setResearchStatusStrip(
    "quickCheckStatusStrip",
    "success",
    `${ticker} quick check ready.`,
    "Review chart, score, and raw evidence before deeper research.",
  );
  const reportInput = document.getElementById("reportTickerInput");
  if (reportInput && !reportInput.value.trim()) reportInput.value = ticker;
  const secInput = document.getElementById("secCompareTickerA");
  if (secInput && !secInput.value.trim()) secInput.value = ticker;
  renderTickerChart(ticker);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("research_summary_refresh"));
  }
  logEvent({ kind: "system", severity: "info", message: `Check complete for ${ticker}.` });
}
