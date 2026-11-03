import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { escapeHtml, safeNum, safeText } from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";
import { setResearchStatusStrip } from "../modules/researchStatus.js";
import { payloadTicker } from "../modules/researchTickerFlow.js";

const DOSSIER_EXPORT_BTN_IDS = [
  "dossierDownloadJsonBtn",
  "dossierDownloadMdBtn",
  "dossierDownloadPdfBtn",
  "dossierDownloadModelWorkbookBtn",
];

let _dossierGen = 0;
let _preflightGen = 0;

function setDossierMeta(message, severity = "muted") {
  const meta = document.getElementById("dossierMeta");
  if (!meta) return;
  meta.className = severity === "good" ? "ok" : severity === "warn" ? "warn" : "muted";
  meta.textContent = message;
}

function hideDossierRetry() {
  document.getElementById("dossierRetryBtn")?.remove();
}

function showDossierRetry() {
  const meta = document.getElementById("dossierMeta");
  if (!meta || document.getElementById("dossierRetryBtn")) return;
  const btn = document.createElement("button");
  btn.id = "dossierRetryBtn";
  btn.type = "button";
  btn.className = "btn small secondary";
  btn.textContent = "Retry";
  btn.addEventListener("click", () => void runResearchDossier());
  meta.insertAdjacentElement("afterend", btn);
}

function resolveDossierTicker() {
  const reportTicker = document.getElementById("reportTickerInput")?.value?.trim()?.toUpperCase() || "";
  if (reportTicker) return reportTicker;
  const quickCheckTicker = document.getElementById("tickerInput")?.value?.trim()?.toUpperCase() || "";
  if (quickCheckTicker) {
    const reportInput = document.getElementById("reportTickerInput");
    if (reportInput) reportInput.value = quickCheckTicker;
    return quickCheckTicker;
  }
  const secCompareTicker = document.getElementById("secCompareTickerA")?.value?.trim()?.toUpperCase() || "";
  if (secCompareTicker) {
    const reportInput = document.getElementById("reportTickerInput");
    if (reportInput) reportInput.value = secCompareTicker;
    return secCompareTicker;
  }
  return "";
}

function pill(status) {
  const safeStatus = safeText(status || "unknown");
  const cls = safeStatus === "ok" || safeStatus === "complete" ? "good" : safeStatus === "blocked" || safeStatus === "missing" ? "bad" : "warn";
  return `<span class="pill ${cls}">${escapeHtml(safeStatus)}</span>`;
}

function renderDossierPreflight(data) {
  const panel = document.getElementById("dossierReadinessPanel");
  if (!panel) return;
  const items = Array.isArray(data?.items) ? data.items : [];
  if (!items.length) {
    panel.innerHTML = `<div class="report-empty">Run preflight to check dossier data sources.</div>`;
    return;
  }
  const rows = items.map((item) => `
    <li>
      ${pill(item.status)}
      <strong>${escapeHtml(item.label || item.id || "Source")}</strong>
      <span class="muted">${escapeHtml(item.detail || "")}</span>
    </li>
  `).join("");
  panel.innerHTML = `
    <div class="dossier-provenance-banner">
      <div class="dossier-provenance-title">Dossier readiness: ${escapeHtml(data.status || "unknown")}</div>
      <ul class="report-bullets dossier-source-list">${rows}</ul>
    </div>
  `;
}

export function syncDossierExportEnabled() {
  const input = resolveDossierTicker();
  const loaded = payloadTicker(state.lastResearchDossier, "");
  const ok = Boolean(loaded && loaded === input && state.lastResearchDossier?.compose_id);
  const title = ok
    ? ""
    : (loaded
      ? `Regenerate dossier for ${input || "the current ticker"} before export. Showing ${loaded}.`
      : "Generate a dossier first.");
  DOSSIER_EXPORT_BTN_IDS.forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = !ok;
    if (title) btn.title = title;
    else btn.removeAttribute("title");
  });
}

export function applyDossierTickerStale(inputTicker) {
  const input = String(inputTicker || resolveDossierTicker() || "").trim().toUpperCase();
  const shown = payloadTicker(state.lastResearchDossier, "");
  const reportShown = payloadTicker(state.lastReportData, "");
  syncDossierExportEnabled();
  if (!shown && !reportShown) return;
  const staleDossier = shown && shown !== input;
  const staleReport = reportShown && reportShown !== input;
  if (!staleDossier && !staleReport) return;
  const shownLabel = staleDossier ? shown : reportShown;
  setDossierMeta(`Showing ${shownLabel} — regenerate for ${input || "the new ticker"}.`, "warn");
  setResearchStatusStrip(
    "reportStatusStrip",
    "partial",
    `Showing ${shownLabel}. Generate for ${input || "the new ticker"}.`,
    staleDossier
      ? `Dossier (and exports) still belong to ${shown}.`
      : `Debug report still belongs to ${reportShown}.`,
  );
}

export async function loadResearchDossierPreflight() {
  const ticker = resolveDossierTicker();
  const gen = ++_preflightGen;
  if (!ticker) {
    renderDossierPreflight({ status: "warn", items: [{ id: "ticker", label: "Ticker", status: "warn", detail: "Enter a ticker first." }] });
    return;
  }
  try {
    const out = await api.getResearchDossierPreflight(ticker, { timeoutMs: 45000 });
    if (gen !== _preflightGen) return;
    const shown = payloadTicker(out.data, ticker);
    if (shown && shown !== ticker) return;
    if (out.ok) renderDossierPreflight(out.data);
  } catch (err) {
    if (gen !== _preflightGen) return;
    renderDossierPreflight({ status: "warn", items: [{ id: "preflight", label: "Preflight", status: "warn", detail: String(err) }] });
  }
}

function markdownToPreviewHtml(markdown) {
  const lines = String(markdown || "").split(/\r?\n/);
  const out = [];
  let inList = false;
  let inTable = false;
  const closeBlocks = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
    if (inTable) {
      out.push("</tbody></table>");
      inTable = false;
    }
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      closeBlocks();
      continue;
    }
    if (line.startsWith("|") && line.endsWith("|")) {
      if (/^\|\s*-+/.test(line)) continue;
      if (!inTable) {
        closeBlocks();
        out.push("<table><tbody>");
        inTable = true;
      }
      const cells = line.slice(1, -1).split("|").map((cell) => `<td>${escapeHtml(cell.trim())}</td>`).join("");
      out.push(`<tr>${cells}</tr>`);
      continue;
    }
    if (line.startsWith("- ")) {
      if (inTable) {
        out.push("</tbody></table>");
        inTable = false;
      }
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${escapeHtml(line.slice(2))}</li>`);
      continue;
    }
    closeBlocks();
    if (line.startsWith("### ")) out.push(`<h4>${escapeHtml(line.slice(4))}</h4>`);
    else if (line.startsWith("## ")) out.push(`<h3>${escapeHtml(line.slice(3))}</h3>`);
    else if (line.startsWith("# ")) out.push(`<h2>${escapeHtml(line.slice(2))}</h2>`);
    else if (line !== "---") out.push(`<p>${escapeHtml(line)}</p>`);
  }
  closeBlocks();
  return out.join("");
}

function buildDossierProvenanceBanner(data) {
  const trust = data?.report_trust || {};
  const sources = Array.isArray(data?.source_metadata) ? data.source_metadata : [];
  const fallbacks = Array.isArray(data?.fallback_notes) ? data.fallback_notes : [];
  const sourceRows = sources.map((row) => {
    const status = safeText(row.status || "unknown");
    return `<li>${pill(status)} <strong>${escapeHtml(row.name || "source")}</strong> <span class="muted">${escapeHtml(row.detail || "")}</span></li>`;
  }).join("");
  const fallbackBlock = fallbacks.length
    ? `<div class="subtle">Degraded sources</div><ul class="report-bullets">${fallbacks.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>`
    : "";
  return `
    <div class="dossier-provenance-banner">
      <div class="dossier-provenance-title">Evidence trail</div>
      <div class="subtle">Trust: ${pill(trust.trusted ? "ok" : "warn")} Data confidence ${safeNum((Number(trust.data_confidence) || 0) * 100, 0)}%</div>
      <ul class="report-bullets dossier-source-list">${sourceRows || "<li>No source metadata returned.</li>"}</ul>
      ${fallbackBlock}
    </div>
  `;
}

function buildDossierQualityBadge(data) {
  const quality = data?.sections?.finnhub_catalysts_risks?.snapshot?.quality || null;
  if (!quality || typeof quality !== "object") {
    return `<div class="dossier-quality-badge dossier-quality-badge--unknown">Finnhub data quality: unknown</div>`;
  }
  const passed = Number(quality.core_checks_passed);
  const total = Number(quality.core_checks_total);
  const coverageText = Number.isFinite(passed) && Number.isFinite(total) && total > 0 ? ` (${passed}/${total} core checks)` : "";
  if (quality.ok) return `<div class="dossier-quality-badge dossier-quality-badge--good">Finnhub data quality: good${coverageText}</div>`;
  return `<div class="dossier-quality-badge dossier-quality-badge--degraded">Finnhub data quality: degraded${coverageText}</div>`;
}

function sectionStatus(data) {
  const sections = data?.sections || {};
  const fin = sections.finnhub_catalysts_risks?.snapshot || {};
  return [
    { label: "Executive", status: data?.executive_pitch?.thesis ? "complete" : "partial" },
    { label: "Fundamentals", status: Object.keys(fin.metrics || {}).length ? "complete" : "partial" },
    { label: "SEC", status: sections.sec_narrative?.analyze?.ok ? "complete" : "partial" },
    { label: "Book context", status: sections.portfolio_and_sector_context?.portfolio_summary?.positions_count !== undefined ? "complete" : "partial" },
    { label: "Catalysts", status: (sections.finnhub_catalysts_risks?.catalysts || []).length ? "complete" : "partial" },
    { label: "Management", status: sections.management_integrity?.integrity_scorecard ? "complete" : "partial" },
    { label: "Forensic", status: sections.forensic?.ok ? "complete" : "partial" },
  ];
}

function buildStructuredCards(data) {
  const pitch = data?.executive_pitch || {};
  const sections = data?.sections || {};
  const sec = sections.sec_narrative?.analyze || {};
  const portfolio = sections.portfolio_and_sector_context?.portfolio_risk || {};
  const statusRows = sectionStatus(data).map((row) => `<li>${pill(row.status)} ${escapeHtml(row.label)}</li>`).join("");
  const grade = data?.debate_grade;
  const gradeCard = grade && grade.qual_letter
    ? `<section class="mini-card"><h3>Shadow grade</h3><p>${escapeHtml(grade.qual_letter)} · ${escapeHtml(grade.label || "Uncalibrated shadow. Does not change rank.")}</p><p class="muted">${escapeHtml(grade.chair || "")}</p></section>`
    : "";
  return `
    <div class="report-grid">
      ${gradeCard}
      <section class="mini-card"><h3>Executive Pitch</h3><p>${escapeHtml(pitch.recommendation || "WATCH")} / ${escapeHtml(pitch.confidence_label || "n/a")} (${escapeHtml(pitch.confidence_score || "n/a")}/100)</p></section>
      <section class="mini-card"><h3>SEC Headline</h3><p>${escapeHtml(sec.summary_headline || sec.error || "SEC narrative unavailable.")}</p></section>
      <section class="mini-card"><h3>Book context</h3><p>${escapeHtml(portfolio.concentration?.hhi_label || "Operator book context unavailable.")}</p></section>
      <section class="mini-card"><h3>Section Status</h3><ul class="report-bullets">${statusRows}</ul></section>
    </div>
  `;
}

function setDossierPreview(data, markdownText = "") {
  const writeup = document.getElementById("dossierWriteup");
  if (writeup) {
    writeup.classList.remove("hidden");
    const html = markdownText ? markdownToPreviewHtml(markdownText) : `<div class="report-empty">Narrative preview unavailable. Use Download Markdown as fallback.</div>`;
    writeup.innerHTML = `<article class="ir-document ir-dossier-preview">${buildDossierProvenanceBanner(data)}${buildDossierQualityBadge(data)}${buildStructuredCards(data)}${html}</article>`;
  }
}

function triggerBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "research_dossier.bin";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1200);
}

function handleDossierRuntimeUnavailable(responseLike) {
  if (!responseLike || responseLike.status !== 404) return false;
  const msg = responseLike.error || "Dossier endpoint unavailable in this runtime. Update the API service and retry.";
  setDossierMeta(msg, "warn");
  updateActionCenter({ title: "Dossier Unavailable", message: msg, severity: "warn" });
  return true;
}

export async function runResearchDossier() {
  const ticker = resolveDossierTicker();
  if (!ticker) {
    setDossierMeta("Enter a ticker in Full Report or SEC Compare first.", "warn");
    updateActionCenter({ title: "Ticker Required", message: "Set a ticker before generating a dossier.", severity: "warn" });
    return;
  }
  const gen = ++_dossierGen;
  await loadResearchDossierPreflight();
  if (gen !== _dossierGen) return;
  const btn = document.getElementById("dossierBtn");
  hideDossierRetry();
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Generating…";
  }
  setDossierMeta(`Generating full report for ${ticker}…`);
  setResearchStatusStrip("reportStatusStrip", "loading", `Generating full report for ${ticker}.`, "Building narrative preview, provenance, quality badge, and export package.");
  try {
    const out = await api.getResearchDossier(ticker, { timeoutMs: 300000, includeMarkdown: true });
    if (gen !== _dossierGen) return;
    if (handleDossierRuntimeUnavailable(out)) {
      showDossierRetry();
      return;
    }
    if (!out.ok) {
      setDossierMeta(out.user_message || out.error || "Full report failed.", "warn");
      setResearchStatusStrip("reportStatusStrip", "error", `Full report failed for ${ticker}.`, safeText(out.user_message || out.error || "Request failed."));
      logEvent({ kind: "report", severity: "error", message: `Dossier ${ticker} failed: ${out.error}` });
      showDossierRetry();
      return;
    }
    const shown = payloadTicker(out.data, ticker);
    if (shown && shown !== ticker) return;
    state.lastResearchDossier = out.data;
    const mdPreview = safeText(out.data?.markdown_preview || "");
    setDossierPreview(out.data, mdPreview);
    const fallbackCount = Array.isArray(out.data?.fallback_notes) ? out.data.fallback_notes.length : 0;
    setDossierMeta(fallbackCount ? `Dossier ready for ${shown} (${fallbackCount} fallback notes)` : `Dossier ready for ${shown}`, fallbackCount ? "warn" : "good");
    setResearchStatusStrip("reportStatusStrip", fallbackCount ? "partial" : "success", `Dossier ready for ${shown}.`, fallbackCount ? "Review provenance before export." : "Narrative preview and exports are ready.");
    syncDossierExportEnabled();
  } finally {
    if (gen === _dossierGen && btn) {
      btn.disabled = false;
      btn.textContent = "Generate full report";
    }
  }
}

export async function downloadResearchDossier(format = "json") {
  const ticker = resolveDossierTicker();
  if (!ticker) {
    setDossierMeta("Enter a ticker in Full Report or SEC Compare first.", "warn");
    return;
  }
  const loaded = payloadTicker(state.lastResearchDossier, "");
  if (!loaded || loaded !== ticker) {
    setDossierMeta(
      loaded
        ? `Dossier is for ${loaded}. Generate for ${ticker} before export.`
        : `Generate a dossier for ${ticker} before export.`,
      "warn",
    );
    syncDossierExportEnabled();
    return;
  }
  const composeId = state.lastResearchDossier?.compose_id || "";
  const idByFormat = { json: "dossierDownloadJsonBtn", md: "dossierDownloadMdBtn", pdf: "dossierDownloadPdfBtn" };
  const btn = document.getElementById(idByFormat[format]);
  if (btn) btn.disabled = true;
  try {
    const out = await api.downloadResearchDossier(ticker, format, { timeoutMs: 300000, composeId });
    if (handleDossierRuntimeUnavailable(out)) return;
    if (!out.ok) {
      setDossierMeta(out.error || "Download failed.", "warn");
      return;
    }
    triggerBlobDownload(out.data.blob, out.data.filename);
    setDossierMeta(`Downloaded ${out.data.filename}`, "good");
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function downloadResearchFundamentalWorkbook() {
  const ticker = resolveDossierTicker();
  if (!ticker) {
    setDossierMeta("Enter a ticker in Full Report or SEC Compare first.", "warn");
    return;
  }
  const loaded = payloadTicker(state.lastResearchDossier, "");
  if (!loaded || loaded !== ticker) {
    setDossierMeta(
      loaded
        ? `Dossier is for ${loaded}. Generate for ${ticker} before export.`
        : `Generate a dossier for ${ticker} before export.`,
      "warn",
    );
    syncDossierExportEnabled();
    return;
  }
  const btn = document.getElementById("dossierDownloadModelWorkbookBtn");
  if (btn) btn.disabled = true;
  try {
    const out = await api.downloadResearchFundamentalWorkbook(ticker, { timeoutMs: 300000, composeId: state.lastResearchDossier?.compose_id || "" });
    if (handleDossierRuntimeUnavailable(out)) return;
    if (!out.ok) {
      setDossierMeta(out.error || "Workbook download failed.", "warn");
      return;
    }
    triggerBlobDownload(out.data.blob, out.data.filename);
    setDossierMeta(`Downloaded ${out.data.filename}`, "good");
  } finally {
    if (btn) btn.disabled = false;
  }
}
