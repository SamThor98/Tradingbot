/**
 * SEC compare panel — diff filings between two tickers, or one ticker
 * over time. Renders the verdict card, narrative card, change card,
 * and the per-side analysis grid; falls back to an EDGAR-metadata-only
 * compare when the dedicated `/api/sec/compare` endpoint isn't
 * deployed.
 *
 * `runSecCompare` accepts an injected `getDisplayMode` so the deep-dive
 * `<details>` element can auto-expand for "pro" users.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { calculateManagementIntegrityScore } from "../modules/managementIntegrity.js";
import { YourThemeConfig } from "../modules/YourThemeConfig.js";
import { safeText, safeNum } from "../modules/format.js";
import { humanizeAnalysisMode, humanizeKey } from "../modules/humanize.js";
import { logEvent, updateActionCenter, statusClass, sentimentTagClass } from "../modules/logger.js";

export function applySecCompareMode() {
  const modeEl = document.getElementById("secCompareMode");
  const tickerB = document.getElementById("secCompareTickerB");
  const changesOnly = document.getElementById("secCompareChangesOnly");
  const modeHelp = document.getElementById("secCompareModeHelp");
  if (!modeEl || !tickerB) return;
  const mode = modeEl.value;
  const requiresSecondTicker = mode === "ticker_vs_ticker";
  tickerB.disabled = !requiresSecondTicker;
  tickerB.placeholder = requiresSecondTicker ? "Ticker B (MSFT)" : "Not required for over-time mode";
  if (modeHelp) {
    modeHelp.textContent = requiresSecondTicker
      ? "Compare two companies side by side — best for contrasting business models and risk language."
      : "Track one company over time — compares current filing language to prior periods to spot changes.";
  }
  if (changesOnly) {
    changesOnly.disabled = mode !== "ticker_over_time";
    if (mode !== "ticker_over_time") changesOnly.checked = false;
  }
}

function setProfileStatusText(text) {
  const el = document.getElementById("secCompareProfileStatus");
  if (!el) return;
  el.textContent = text;
}

function renderProfileHistory(items) {
  const list = document.getElementById("secCompareProfileHistoryList");
  if (!list) return;
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    list.innerHTML = '<li class="muted">No override history yet.</li>';
    return;
  }
  const latest = rows.slice(-5).reverse();
  list.innerHTML = latest
    .map((row) => {
      const at = safeText(row?.at || "n/a");
      const before = safeText(row?.before || "auto");
      const after = safeText(row?.after || "auto");
      const actor = safeText(row?.actor || "user");
      const reason = safeText(row?.reason || "unspecified");
      const evidence = safeText(row?.evidence_ref || "");
      return `<li><strong>${at}</strong> · ${before} -> ${after} · by ${actor} · reason: ${reason}${evidence ? ` · evidence: ${evidence}` : ""}</li>`;
    })
    .join("");
}

function renderProfileStatusFromDashboard(dashboard) {
  const profile = dashboard?.profile || {};
  const selected = safeText(profile.selected || "auto_detect");
  const mode = safeText(profile.mode || "");
  const persisted = safeText(profile.persisted_override || "");
  const lastOverride = profile.last_override || null;
  const lastSummary = lastOverride
    ? ` · last override: ${safeText(lastOverride.after || "auto")} by ${safeText(lastOverride.actor || "user")} (${safeText(lastOverride.reason || "no reason")})`
    : "";
  if (mode === "manual_override") {
    setProfileStatusText(`Profile: manual override (${humanizeKey(selected)})${lastSummary}`);
    renderProfileHistory(profile.history_tail || []);
    return;
  }
  if (persisted) {
    setProfileStatusText(`Profile: saved override (${humanizeKey(persisted)})${lastSummary}`);
    renderProfileHistory(profile.history_tail || []);
    return;
  }
  setProfileStatusText(`Profile: ${humanizeKey(selected || "auto_detect")}${lastSummary}`);
  renderProfileHistory(profile.history_tail || []);
}

function confidenceBand(confidence) {
  if (!Number.isFinite(Number(confidence))) return "Unavailable";
  const value = Number(confidence);
  if (value >= 70) return "High";
  if (value >= 45) return "Medium";
  return "Low";
}

function analysisModeLabel(mode) {
  return humanizeAnalysisMode(mode === "metadata_fallback" ? "metadata_fallback" : "full_text");
}

function parseDateSafe(value) {
  if (!value) return null;
  const dt = new Date(value);
  return Number.isFinite(dt.getTime()) ? dt : null;
}

function freshnessLabel(value) {
  const dt = parseDateSafe(value);
  if (!dt) return { label: "unknown", css: "warn", ageDays: null };
  const ageDays = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 86400000));
  if (ageDays <= 7) return { label: "fresh", css: "good", ageDays };
  if (ageDays <= 45) return { label: "watch", css: "warn", ageDays };
  return { label: "stale", css: "warn", ageDays };
}

const TIMELINE_NOISE_PATTERNS = [
  /\b(?:xbrli|us-gaap|dei):/i,
  /\b\d{6,}\b/,
  /\b(?:P\d+Y|P\d+M|P\d+D)\b/i,
  /\b(?:true|false)\b/i,
];

function timelineLooksNoisy(text) {
  const sample = safeText(text);
  if (!sample) return true;
  for (const rx of TIMELINE_NOISE_PATTERNS) {
    if (rx.test(sample)) return true;
  }
  const tokens = sample.split(/\s+/);
  if (tokens.some((t) => t.length > 30)) return true;
  const words = sample.match(/[A-Za-z]{3,}/g) || [];
  const symbols = sample.match(/[:/|_=]/g) || [];
  if (words.length < 4 && symbols.length >= 2) return true;
  return false;
}

function cleanTimelineNarrative(text, fallback, maxLen = 170) {
  const raw = safeText(text).replace(/\s+/g, " ").trim();
  if (!raw) return fallback;
  let out = raw;
  if (out.includes(".")) {
    out = `${out.split(".", 1)[0].trim()}.`;
  }
  if (timelineLooksNoisy(out)) return fallback;
  if (out.length > maxLen) out = `${out.slice(0, maxLen - 1).trimEnd()}…`;
  return out;
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function finiteNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function scoreBand(score) {
  const v = finiteNumber(score, 0);
  if (v >= 75) return "strong";
  if (v >= 50) return "watch";
  return "weak";
}

function clamp(min, value, max) {
  return Math.max(min, Math.min(max, value));
}

function severityClass(sev) {
  const key = safeText(sev || "").toLowerCase();
  if (["critical", "high"].includes(key)) return "sev-high";
  if (["moderate", "medium"].includes(key)) return "sev-med";
  return "sev-low";
}

function pctText(value, digits = 1) {
  const n = finiteNumber(value, NaN);
  if (!Number.isFinite(n)) return "n/a";
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

function usdMillionsText(value) {
  const n = finiteNumber(value, NaN);
  if (!Number.isFinite(n)) return "n/a";
  return `$${n.toFixed(1)}M`;
}

function normalizeTimelineRows(data) {
  const rawRows = asArray(
    data?.say_do_timeline
      || data?.timeline
      || data?.guidance_timeline
      || data?.historical_guidance
      || data?.kpi_timeline,
  );
  return rawRows.map((row, idx) => {
    const guidance = cleanTimelineNarrative(
      row.guidance || row.promise || row.statement || "",
      "Management reiterated execution discipline and guidance continuity.",
    );
    const actual = cleanTimelineNarrative(
      row.actual || row.realized || row.outcome || row.realized_kpi || "",
      "Realized KPI tracked within the expected operating range.",
    );
    const target = Number.isFinite(Number(row.target_value)) ? Number(row.target_value) : null;
    const realized = Number.isFinite(Number(row.actual_value)) ? Number(row.actual_value) : null;
    const variance = Number.isFinite(Number(row.variance_pct))
      ? Number(row.variance_pct)
      : target !== null && realized !== null && Math.abs(target) > 0.0001
        ? ((realized - target) / Math.abs(target)) * 100
        : null;
    const statusRaw = safeText(row.status || row.result || row.verdict || "");
    const status = statusRaw || (variance !== null ? (variance >= 0 ? "Beat" : "Miss") : "Mixed");
    return {
      id: safeText(row.id || row.quarter || row.period || `row-${idx}`),
      quarter: safeText(row.quarter || row.period || row.filing_period || `Q-${idx + 1}`),
      guidance,
      actual,
      kpi: safeText(row.kpi || row.metric || "Composite KPI"),
      target,
      realized,
      variance,
      status,
      source: safeText(row.source || row.citation || row.document || "10-Q/10-K"),
    };
  });
}

function normalizePillars(data) {
  const rawPillars = asArray(
    data?.integrity_scorecard?.pillars
      || data?.integrity?.pillars
      || data?.pillars
      || data?.scorecard?.pillars,
  );
  return rawPillars.map((p) => ({
    name: safeText(p.name || p.pillar || "Pillar"),
    score: clamp(0, Math.round(finiteNumber(p.score, 50)), 100),
    note: safeText(p.note || p.rationale || p.commentary || "No note provided."),
  }));
}

function normalizeHeatmapRows(data) {
  const rawRows = asArray(
    data?.dilution_sbc_heatmap
      || data?.heatmap
      || data?.sbc_heatmap
      || data?.dilution_heatmap
      || data?.sbc_vs_performance,
  );
  return rawRows.map((row, idx) => ({
    id: safeText(row.id || row.quarter || row.period || `h-${idx}`),
    quarter: safeText(row.quarter || row.period || `Q-${idx + 1}`),
    sbc_musd: finiteNumber(row.sbc_musd ?? row.sbc_expense_musd ?? row.sbc_expense, NaN),
    sbc_pct_rev: finiteNumber(row.sbc_pct_rev ?? row.sbc_to_revenue_pct ?? row.sbc_ratio_pct, NaN),
    net_income_musd: finiteNumber(row.net_income_musd ?? row.net_income, NaN),
    price_return_pct: finiteNumber(row.price_return_pct ?? row.stock_return_pct ?? row.return_pct, NaN),
    correlation: finiteNumber(row.correlation ?? row.corr ?? row.impact_score, NaN),
    note: safeText(row.note || row.commentary || ""),
  }));
}

function normalizeRedFlags(data) {
  const rawFlags = asArray(
    data?.red_flags
      || data?.ruthless_flags
      || data?.filing_red_flags
      || data?.forensic_divergence?.red_flag_ledger,
  );
  return rawFlags.map((f, idx) => {
    if (typeof f === "string") {
      return {
        id: `rf-${idx}`,
        title: safeText(f),
        severity: "medium",
        evidence: "SEC narrative compare",
        quarter: "n/a",
      };
    }
    return {
      id: safeText(f.id || `rf-${idx}`),
      title: safeText(f.title || f.flag || f.description || "Red flag"),
      severity: safeText(f.severity || f.level || "medium"),
      evidence: safeText(f.evidence || f.reference || f.source || "SEC filing context"),
      quarter: safeText(f.quarter || f.period || "n/a"),
    };
  });
}

function buildFallbackManagementDashboard(comparePayload, { mode, tickerA, tickerB }) {
  const compare = comparePayload?.compare || {};
  const formType = safeText(comparePayload?.form_type || "10-Q");
  const evidence = asArray(compare.evidence || compare.change_summary?.evidence_ranked);
  const materialChanges = asArray(compare.material_changes || compare.top_differences || compare.differences);
  const compareConfidence = clamp(0, Math.round(finiteNumber(compare.compare_confidence, 58)), 100);
  const redFlagLedger = normalizeRedFlags(comparePayload?.compare || {});
  const nowYear = new Date().getFullYear();
  const timeline = Array.from({ length: 6 }).map((_, idx) => {
    const offset = 6 - idx;
    const q = ((idx + 1) % 4) + 1;
    const y = nowYear - Math.floor(offset / 4);
    const delta = (idx - 2) * 1.8;
    const changeLine = safeText(materialChanges[idx] || evidence[idx]?.claim || "Management reiterated balanced growth and margin discipline.");
    return {
      quarter: `Q${q} ${y}`,
      guidance: `Guidance (${formType}): ${changeLine}`,
      actual: `Realized KPI: ${safeText(evidence[idx]?.quote || "Result tracked near guided range.")}`,
      kpi: "Revenue growth vs margin trajectory",
      target_value: 8 + idx * 0.8,
      actual_value: 8 + idx * 0.8 + delta,
      variance_pct: delta,
      status: delta >= 0 ? "Beat" : "Miss",
      source: formType,
    };
  });
  const baseScore = clamp(35, compareConfidence - redFlagLedger.length * 5, 92);
  const pillars = [
    { name: "Capital Discipline", score: clamp(20, baseScore - 2, 96), note: "Capex, buybacks, and leverage commentary consistency." },
    { name: "Shareholder Alignment", score: clamp(20, baseScore - 4, 96), note: "Insider behavior and dilution posture vs stated priorities." },
    { name: "Communication Transparency", score: clamp(20, baseScore + 3, 96), note: "Guidance clarity, restatements, and disclosure precision." },
    { name: "Operational Execution", score: clamp(20, baseScore + 1, 96), note: "Ability to translate guidance into KPI delivery." },
  ];
  const heatmap = Array.from({ length: 8 }).map((_, idx) => {
    const q = ((idx + 1) % 4) + 1;
    const y = nowYear - Math.floor((8 - idx) / 4);
    const sbcPct = 4.2 + idx * 0.55;
    const income = 950 - idx * 35;
    const returnPct = 11.5 - idx * 2.4;
    return {
      quarter: `Q${q} ${y}`,
      sbc_musd: 220 + idx * 25,
      sbc_pct_rev: sbcPct,
      net_income_musd: income,
      price_return_pct: returnPct,
      correlation: clamp(-1, 0.65 - idx * 0.14, 1),
      note: idx >= 5 ? "Dilution pressure rising while returns fade." : "Contained dilution vs growth.",
    };
  });
  const fallbackFlags = redFlagLedger.length
    ? redFlagLedger
    : [
      {
        id: "rf-fallback-1",
        title: "Possible guidance wording shift around margin assumptions.",
        severity: "medium",
        evidence: "Comparative filing language delta",
        quarter: timeline[1]?.quarter || "n/a",
      },
    ];
  return {
    source: "derived_compare_fallback",
    data_fidelity: {
      compare_evidence: safeText(compare.analysis_mode || "metadata_fallback"),
      say_do_timeline: "synthetic_fallback",
      dilution_heatmap: "synthetic_fallback",
      integrity_score: "synthetic_fallback",
      disclaimer: "Management dashboard cards are synthetic placeholders because SEC compare used metadata-only fallback.",
    },
    mode,
    ticker: tickerA,
    benchmark_ticker: tickerB || "",
    say_do_timeline: timeline,
    integrity_scorecard: {
      score: Math.round(pillars.reduce((sum, p) => sum + p.score, 0) / pillars.length),
      pillars,
    },
    dilution_sbc_heatmap: heatmap,
    red_flags: fallbackFlags,
  };
}

function normalizeManagementPayload(raw, fallbackData) {
  const payload = raw?.data?.management_dashboard || raw?.management_dashboard || raw?.data || raw || {};
  const timeline = normalizeTimelineRows(payload);
  const pillars = normalizePillars(payload);
  const heatmap = normalizeHeatmapRows(payload);
  const redFlags = normalizeRedFlags(payload);
  const scoreFromPayload = finiteNumber(
    payload?.integrity_scorecard?.score
      || payload?.integrity?.score
      || payload?.scorecard?.score
      || payload?.integrity_score,
    NaN,
  );
  const scoreFromPillars = pillars.length
    ? Math.round(pillars.reduce((sum, p) => sum + finiteNumber(p.score, 0), 0) / pillars.length)
    : NaN;
  const score = clamp(0, Math.round(Number.isFinite(scoreFromPayload) ? scoreFromPayload : (Number.isFinite(scoreFromPillars) ? scoreFromPillars : 0)), 100);
  if (!timeline.length && !pillars.length && !heatmap.length && !redFlags.length) {
    return fallbackData;
  }
  const dataFidelity = payload?.data_fidelity && typeof payload.data_fidelity === "object"
    ? payload.data_fidelity
    : (fallbackData?.data_fidelity || null);
  return {
    source: safeText(payload?.source || raw?.source || "backend"),
    mode: safeText(payload?.mode || fallbackData.mode || ""),
    ticker: safeText(payload?.ticker || fallbackData.ticker || ""),
    benchmark_ticker: safeText(payload?.benchmark_ticker || fallbackData.benchmark_ticker || ""),
    profile: payload?.profile || fallbackData?.profile || null,
    data_fidelity: dataFidelity,
    say_do_timeline: timeline.length ? timeline : fallbackData.say_do_timeline,
    integrity_scorecard: {
      score: score || fallbackData.integrity_scorecard.score,
      pillars: pillars.length ? pillars : fallbackData.integrity_scorecard.pillars,
    },
    dilution_sbc_heatmap: heatmap.length ? heatmap : fallbackData.dilution_sbc_heatmap,
    red_flags: redFlags.length ? redFlags : fallbackData.red_flags,
  };
}

function mergeManagementDashboard(base, analyst, { mode, tickerA, tickerB, ruthlessMode }) {
  const out = {
    ...(base || {}),
    ...(analyst || {}),
  };
  out.mode = safeText(out.mode || mode || "ticker_over_time");
  out.ticker = safeText(out.ticker || tickerA || "");
  out.benchmark_ticker = safeText(out.benchmark_ticker || tickerB || "");
  out.ruthless_mode = Boolean(ruthlessMode);
  out.say_do_timeline = asArray(analyst?.say_do_timeline).length
    ? analyst.say_do_timeline
    : asArray(base?.say_do_timeline);
  out.integrity_scorecard = {
    ...(base?.integrity_scorecard || {}),
    ...(analyst?.integrity_scorecard || {}),
    pillars: asArray(analyst?.integrity_scorecard?.pillars).length
      ? analyst.integrity_scorecard.pillars
      : asArray(base?.integrity_scorecard?.pillars),
  };
  out.dilution_sbc_heatmap = asArray(analyst?.dilution_sbc_heatmap).length
    ? analyst.dilution_sbc_heatmap
    : asArray(base?.dilution_sbc_heatmap);
  out.red_flags = asArray(analyst?.red_flags).length
    ? analyst.red_flags
    : asArray(base?.red_flags);
  out.data_fidelity = analyst?.data_fidelity || base?.data_fidelity || out.data_fidelity || null;
  out.profile = analyst?.profile || base?.profile || out.profile || null;
  out.source = safeText([base?.source, analyst?.source].filter(Boolean).join("+") || "fallback");
  return out;
}

async function fetchManagementDashboard({
  mode,
  tickerA,
  tickerB,
  ruthlessMode,
  comparePayload,
  profileOverride,
  deepScan,
}) {
  const fallbackData = buildFallbackManagementDashboard(comparePayload, { mode, tickerA, tickerB });
  let merged = comparePayload?.management_dashboard
    ? normalizeManagementPayload({ management_dashboard: comparePayload.management_dashboard }, fallbackData)
    : fallbackData;
  merged.mode = safeText(merged.mode || mode || "ticker_over_time");
  merged.ticker = safeText(merged.ticker || tickerA || "");
  merged.benchmark_ticker = safeText(merged.benchmark_ticker || tickerB || "");
  merged.ruthless_mode = Boolean(ruthlessMode);
  let source = comparePayload?.management_dashboard ? "bundled_compare" : "fallback";

  if (deepScan) {
    const analystOut = await calculateManagementIntegrityScore(tickerA);
    const analystData = analystOut?.ok ? analystOut.data : null;
    merged = mergeManagementDashboard(merged, analystData, { mode, tickerA, tickerB, ruthlessMode });
    if (analystData?.source) source = `${source}+${analystData.source}`;
  }

  return { ok: true, data: merged, source };
}

function renderSayDoTimeline(root, timelineRows, ruthlessMode, derivedMode) {
  if (!root) return;
  const rows = asArray(timelineRows).slice(0, 8);
  if (!rows.length) {
    root.innerHTML = "<div class='report-empty'>No guidance-to-KPI timeline rows were returned.</div>";
    return;
  }
  const derivedAttr = derivedMode ? ' data-derived="true"' : "";
  root.innerHTML = `
    <div class="mgmt-card-title-row"${derivedAttr}>
      <h4>The Say-Do Timeline</h4>
      <span id="secTimelineWindow" class="mgmt-badge mono-nums">Window: ${rows.length}Q${derivedMode ? " · modeled" : ""}</span>
    </div>
    <div class="saydo-timeline"${derivedAttr}>
      ${rows.map((row) => {
        const variance = Number.isFinite(Number(row.variance)) ? Number(row.variance) : null;
        const status = safeText(row.status || (variance !== null && variance >= 0 ? "Beat" : "Miss"));
        const statusKey = safeText(status).toLowerCase();
        const statusClassName = statusKey.includes("beat") ? "good" : statusKey.includes("miss") ? "bad" : "neutral";
        return `
          <article class="saydo-node ${ruthlessMode && statusClassName === "bad" ? "saydo-node--warn" : ""}">
            <div class="saydo-node-dot ${statusClassName}"></div>
            <div class="saydo-node-body">
              <header>
                <span class="mono-nums saydo-quarter">${safeText(row.quarter)}</span>
                <span class="pill ${statusClassName}">${safeText(status)}</span>
              </header>
              <p><span class="mono-nums">SAY:</span> ${safeText(row.guidance)}</p>
              <p><span class="mono-nums">DO:</span> ${safeText(row.actual)}</p>
              <div class="saydo-kpi-row mono-nums">
                KPI: ${safeText(row.kpi)}
                ${Number.isFinite(Number(row.target)) ? ` | Target ${safeNum(row.target, 2)}` : ""}
                ${Number.isFinite(Number(row.realized)) ? ` | Realized ${safeNum(row.realized, 2)}` : ""}
                ${variance !== null ? ` | Variance ${pctText(variance, 1)}` : ""}
              </div>
              <div class="muted mono-nums">Source: ${safeText(row.source)}</div>
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderIntegrityScorecard(root, scorecard) {
  if (!root) return;
  const score = clamp(0, finiteNumber(scorecard?.score, 0), 100);
  const band = scoreBand(score);
  const pillars = asArray(scorecard?.pillars).slice(0, 4);
  const gaugeColor = band === "strong"
    ? YourThemeConfig.chart.gauge.strong
    : band === "watch"
      ? YourThemeConfig.chart.gauge.watch
      : YourThemeConfig.chart.gauge.weak;
  root.innerHTML = `
    <div class="mgmt-card-title-row">
      <h4>The Integrity Scorecard</h4>
      <span class="mgmt-badge">4 Pillars</span>
    </div>
    <div class="integrity-score-wrap">
      <div class="integrity-gauge" style="--score:${score};--gauge:${gaugeColor}">
        <div class="integrity-gauge-inner">
          <div class="integrity-gauge-value mono-nums">${Math.round(score)}</div>
          <div class="integrity-gauge-label">${safeText(band.toUpperCase())}</div>
        </div>
      </div>
      <div class="integrity-pillars">
        ${pillars.map((pillar) => `
          <div class="integrity-pillar">
            <div class="integrity-pillar-head">
              <span>${safeText(pillar.name)}</span>
              <span class="mono-nums">${safeText(pillar.score)}</span>
            </div>
            <div class="integrity-pillar-bar"><span style="width:${clamp(0, finiteNumber(pillar.score, 0), 100)}%"></span></div>
            <p>${safeText(pillar.note)}</p>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function heatCellClass(value, thresholds) {
  if (!Number.isFinite(Number(value))) return "heat-na";
  if (value >= thresholds.high) return "heat-high";
  if (value >= thresholds.mid) return "heat-mid";
  return "heat-low";
}

function buildAnalystAnnotation(flag) {
  const title = safeText(flag?.title).toLowerCase();
  const evidence = safeText(flag?.evidence);
  if (title.includes("dilution")) return "Share count rises, ownership quality falls. Math stays undefeated.";
  if (title.includes("tsr") || title.includes("comp") || title.includes("insider")) {
    return "Compensation optimism is outpacing shareholder outcomes. Incentives need a flashlight.";
  }
  if (title.includes("guidance") || title.includes("outlook") || title.includes("hedge")) {
    return "Plenty of caveats, limited commitments. Language risk usually precedes execution risk.";
  }
  if (safeText(flag?.severity).toLowerCase().includes("high")) {
    return "High-severity discrepancy flagged. Treat this as thesis risk, not noise.";
  }
  return evidence
    ? `Cross-check filing evidence: ${evidence}`
    : "Analyst engine flagged a governance/execution anomaly. Verify source text directly.";
}

function renderDilutionHeatmap(root, heatRows, derivedMode) {
  if (!root) return;
  const rows = asArray(heatRows).slice(0, 10);
  const th = YourThemeConfig.chart.heatmap.thresholds;
  if (!rows.length) {
    root.innerHTML = "<div class='report-empty'>No SBC/dilution observations were returned.</div>";
    return;
  }
  const derivedAttr = derivedMode ? ' data-derived="true"' : "";
  root.innerHTML = `
    <div class="mgmt-card-title-row"${derivedAttr}>
      <h4>Dilution &amp; SBC Heatmap</h4>
      <span class="mgmt-badge mono-nums">SBC x NI x Price${derivedMode ? " · modeled" : ""}</span>
    </div>
    <div class="dilution-table-wrap"${derivedAttr}>
      <table class="dilution-table mono-nums">
        <thead>
          <tr>
            <th>Quarter</th>
            <th>SBC ($M)</th>
            <th>SBC % Rev</th>
            <th>Net Income ($M)</th>
            <th>Price Return</th>
            <th>Corr</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>${safeText(row.quarter)}</td>
              <td class="${heatCellClass(row.sbc_musd, th.sbcMusd)}">${usdMillionsText(row.sbc_musd)}</td>
              <td class="${heatCellClass(row.sbc_pct_rev, th.sbcPctRevenue)}">${pctText(row.sbc_pct_rev)}</td>
              <td class="${heatCellClass(-row.net_income_musd, th.netIncomeRisk)}">${usdMillionsText(row.net_income_musd)}</td>
              <td class="${heatCellClass(-row.price_return_pct, th.priceReturnRisk)}">${pctText(row.price_return_pct)}</td>
              <td class="${heatCellClass(row.correlation, th.correlationRisk)}">${Number.isFinite(Number(row.correlation)) ? finiteNumber(row.correlation, 0).toFixed(2) : "n/a"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
      <p class="muted">Hotter cells indicate higher dilution pressure and weaker earnings/price coupling.</p>
    </div>
  `;
}

function renderRedFlags(root, redFlags, ruthlessMode) {
  if (!root) return;
  const flags = asArray(redFlags);
  if (!flags.length) {
    root.innerHTML = "<div class='report-empty'>No red flags were detected from the latest payload.</div>";
    return;
  }
  const filtered = ruthlessMode ? flags.filter((f) => severityClass(f.severity) !== "sev-low") : flags;
  if (!filtered.length) {
    root.innerHTML = "<div class='report-empty'>Ruthless Mode is on: no medium/high severity flags in the selected window.</div>";
    return;
  }
  root.innerHTML = `
    <div class="mgmt-card-title-row">
      <h4>Ruthless Mode Red Flags</h4>
      <span class="mgmt-badge">${ruthlessMode ? "Ruthless: ON" : "Ruthless: OFF"}</span>
    </div>
    <div class="redflag-list">
      ${filtered.map((flag) => `
        <article class="redflag-row ${severityClass(flag.severity)} ${ruthlessMode ? "ruthless" : ""}">
          <div class="redflag-head">
            <span class="pill ${severityClass(flag.severity)}">${safeText(flag.severity || "medium")}</span>
            <span class="mono-nums">${safeText(flag.quarter)}</span>
          </div>
          <div class="redflag-title">${safeText(flag.title)}</div>
          <div class="muted">Evidence: ${safeText(flag.evidence)}</div>
          <aside class="analyst-annotation" aria-hidden="true">${safeText(buildAnalystAnnotation(flag))}</aside>
        </article>
      `).join("")}
    </div>
  `;
}

export function renderSecAnalysisCard(label, analysis) {
  if (!analysis) return "";
  const themes = (analysis.key_themes || []).slice(0, 3).map((t) => `<li>${safeText(t)}</li>`).join("");
  const risks = (analysis.risk_terms || []).slice(0, 5).join(", ") || "None highlighted";
  const guidance = safeText(analysis.guidance_signal || "neutral");
  const takeaway = safeText(analysis.high_level_takeaway || "No takeaway.");
  const verdict = safeText(analysis.verdict || "neutral");
  const filingUrl = safeText(analysis.filing_url || "");
  const confidence = Number.isFinite(Number(analysis.confidence)) ? Number(analysis.confidence) : null;
  const why = (analysis.why || []).slice(0, 3);
  const evidence = (analysis.evidence || []).slice(0, 2);
  const limits = (analysis.limits || []).slice(0, 3);
  const analysisMode = safeText(analysis.analysis_mode || "full_text");
  const warning = analysisMode !== "full_text" || limits.length
    ? `<div class="report-callout warn">Mode: ${analysisMode}. ${limits.length ? `Limits: ${safeText(limits.join("; "))}` : "Reduced confidence mode."}</div>`
    : "";
  const confidenceBandLabel = confidenceBand(confidence);
  const whatChanged = (analysis.what_changed || analysis.delta_highlights || []).slice(0, 2);
  const whyMatters = (analysis.why_it_matters || analysis.impact_notes || []).slice(0, 2);
  const falsifier = safeText(analysis.falsifier || analysis.what_would_falsify || limits[0] || "Falsifier signal unavailable.");
  return `
    <div class="compare-card">
      <h4>${safeText(label)}</h4>
      <div class="subtle">Variant view: <span class="${statusClass(verdict === "bullish" ? "good" : verdict === "bearish" ? "bad" : "neutral")}">${verdict}</span>${confidence !== null ? ` | Confidence: ${safeText(confidence)}/100 (${confidenceBandLabel})` : ""}</div>
      ${warning}
      <ul class="report-bullets">
        <li>Form: ${safeText(analysis.form)} | Filed: ${safeText(analysis.filing_date)}${filingUrl ? ` | <a href="${safeText(filingUrl)}" target="_blank" rel="noopener noreferrer">View filing</a>` : ""}</li>
        <li>Evidence quality: ${safeText(analysisModeLabel(analysisMode))}</li>
        <li>Guidance: <span class="${statusClass(guidance === "negative" ? "bad" : guidance === "positive" ? "good" : "neutral")}">${guidance}</span></li>
        <li>Risk terms: ${safeText(risks)}</li>
        <li>Takeaway: ${takeaway}</li>
      </ul>
      <div class="subtle">Decision discipline</div>
      <ul class="report-bullets">
        <li>Claim: ${takeaway}</li>
        <li>Evidence: ${safeText((evidence[0]?.quote || evidence[0]?.claim || "Unavailable"))}</li>
        <li>Confidence: ${confidence !== null ? `${safeText(confidence)}/100 (${confidenceBandLabel})` : "Unavailable"}</li>
        <li>Falsifier: ${falsifier}</li>
      </ul>
      ${whatChanged.length ? `<div class="subtle">What changed</div><ul class="report-bullets">${whatChanged.map((x) => `<li>${safeText(x)}</li>`).join("")}</ul>` : ""}
      ${whyMatters.length ? `<div class="subtle">Why it matters</div><ul class="report-bullets">${whyMatters.map((x) => `<li>${safeText(x)}</li>`).join("")}</ul>` : ""}
      ${why.length ? `<div class="subtle">Why this verdict</div><ul class="report-bullets">${why.map((w) => `<li>${safeText(w)}</li>`).join("")}</ul>` : ""}
      ${evidence.length ? `<div class="subtle">Top evidence</div><ul class="report-bullets">${evidence.map((ev) => `<li>${safeText(ev.claim || "Evidence")}: ${safeText(ev.quote || "")}</li>`).join("")}</ul>` : ""}
      <div class="subtle">Top themes</div>
      <ul class="report-bullets">${themes || "<li>No theme sentences extracted.</li>"}</ul>
    </div>
  `;
}

export function toReadableDeltaLabel(key) {
  const map = {
    revenue_mentions: "Revenue references",
    profit_mentions: "Profitability references",
    cashflow_mentions: "Cash-flow references",
    debt_mentions: "Debt references",
    liquidity_mentions: "Liquidity references",
  };
  return map[key] || String(key || "").replaceAll("_", " ");
}

export function buildNarrativeSummary(comparePayload) {
  const compare = comparePayload?.compare || {};
  if (compare.narrative_summary) return safeText(compare.narrative_summary);

  const similarities = compare.similarities || [];
  const differences = compare.differences || [];
  const material = compare.material_changes || [];
  const investor = compare.investor_takeaway || "No investor takeaway was generated.";

  const firstSimilarity = similarities[0] || "The filings share limited direct overlap.";
  const firstDifference = differences[0] || "No major contrast surfaced in the initial pass.";
  const firstMaterial = material[0] || "No strongly material disclosure change was detected.";
  return `${investor} ${firstSimilarity} ${firstDifference} ${firstMaterial}`;
}

function buildManagementFidelityBanner(dashboard, compare) {
  const fidelity = dashboard?.data_fidelity || {};
  const compareMode = safeText(fidelity.compare_evidence || compare?.analysis_mode || "full_text");
  const timelineMode = safeText(fidelity.say_do_timeline || "");
  const disclaimer = safeText(fidelity.disclaimer || "");
  const isDerived = timelineMode.includes("derived") || timelineMode.includes("synthetic") || compareMode === "metadata_fallback";
  if (!isDerived && !disclaimer) return "";
  const message = disclaimer || (
    compareMode === "metadata_fallback"
      ? "SEC compare used EDGAR metadata only. Management dashboard metrics are illustrative until full filing-text compare is available."
      : "Say-Do timeline and dilution heatmap are derived from compare deltas, not independently audited filing KPIs."
  );
  return `<div class="report-callout warn sec-fidelity-banner">${message}</div>`;
}

function isDerivedFidelity(dashboard) {
  const fidelity = dashboard?.data_fidelity || {};
  const timelineMode = safeText(fidelity.say_do_timeline || "");
  const compareMode = safeText(fidelity.compare_evidence || "");
  return (
    timelineMode.includes("derived")
    || timelineMode.includes("synthetic")
    || compareMode === "metadata_fallback"
  );
}

function setSecCompareActionRow(tickerA) {
  const row = document.getElementById("secCompareActionRow");
  if (!row) return;
  if (!tickerA) {
    row.hidden = true;
    return;
  }
  row.hidden = false;
}

export function wireSecCompareActions({ openTradeDrawer } = {}) {
  document.getElementById("secCompareOpenDossierBtn")?.addEventListener("click", () => {
    const ticker = document.getElementById("secCompareTickerA")?.value?.trim()?.toUpperCase() || "";
    if (!ticker) return;
    const reportInput = document.getElementById("reportTickerInput");
    if (reportInput) reportInput.value = ticker;
    document.getElementById("reportSectionCard")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  document.getElementById("secCompareOpenDrawerBtn")?.addEventListener("click", () => {
    const ticker = document.getElementById("secCompareTickerA")?.value?.trim()?.toUpperCase() || "";
    if (!ticker || typeof openTradeDrawer !== "function") return;
    openTradeDrawer({ tab: "decision", ticker });
  });
}

export function renderSecCompareEmpty(message) {
  const headlineRoot = document.getElementById("secCompareHeadline");
  const narrativeRoot = document.getElementById("secCompareNarrative");
  const changesRoot = document.getElementById("secCompareChanges");
  const evidenceRoot = document.getElementById("secCompareVisual");
  const timelineRoot = document.getElementById("secSayDoTimeline");
  const scorecardRoot = document.getElementById("secIntegrityScorecard");
  const heatmapRoot = document.getElementById("secDilutionHeatmap");
  const redFlagsRoot = document.getElementById("secRedFlagsPanel");
  const fidelityTop = document.getElementById("secCompareFidelityBanner");
  const msg = safeText(message || "No SEC compare data available.");
  if (headlineRoot) headlineRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (narrativeRoot) narrativeRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (changesRoot) changesRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (evidenceRoot) evidenceRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (timelineRoot) timelineRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (scorecardRoot) scorecardRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (heatmapRoot) heatmapRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (redFlagsRoot) redFlagsRoot.innerHTML = `<div class="report-empty">${msg}</div>`;
  if (fidelityTop) {
    fidelityTop.hidden = true;
    fidelityTop.innerHTML = "";
  }
  setSecCompareActionRow("");
}

export function renderSecCompareVisual(data, { getDisplayMode = () => "balanced" } = {}) {
  const headlineRoot = document.getElementById("secCompareHeadline");
  const narrativeRoot = document.getElementById("secCompareNarrative");
  const changesRoot = document.getElementById("secCompareChanges");
  const evidenceRoot = document.getElementById("secCompareVisual");
  const timelineRoot = document.getElementById("secSayDoTimeline");
  const scorecardRoot = document.getElementById("secIntegrityScorecard");
  const heatmapRoot = document.getElementById("secDilutionHeatmap");
  const redFlagsRoot = document.getElementById("secRedFlagsPanel");
  if (!headlineRoot || !narrativeRoot || !changesRoot || !evidenceRoot) return;
  if (!data || !data.ok) {
    renderSecCompareEmpty("No SEC compare data available.");
    return;
  }

  const compare = data.compare || {};
  const left = data.left || data.latest || null;
  const right = data.right || data.prior || null;
  const leftLabel = compare.left_label || "Left";
  const rightLabel = compare.right_label || "Right";
  const forensic = compare.forensic_divergence || {};
  const sentimentTag = safeText(compare.sentiment_tag || forensic.sentiment_tag || "[NEUTRAL/BOILERPLATE]");
  const similaritiesRaw = compare.top_commonalities || compare.similarities || [];
  const differencesRaw = compare.top_differences || compare.differences || [];
  const materialRaw = compare.material_changes || [];
  const similarities = similaritiesRaw.slice(0, 6).map((x) => `<li>${safeText(x)}</li>`).join("");
  const differences = differencesRaw.slice(0, 6).map((x) => `<li>${safeText(x)}</li>`).join("");
  const material = materialRaw.slice(0, 6).map((x) => `<li>${safeText(x)}</li>`).join("");
  const deltas = compare.metric_deltas || {};
  const deltaChips = Object.entries(deltas)
    .map(([k, v]) => `<span class="delta-chip">${safeText(toReadableDeltaLabel(k))}: ${safeNum(v, 0) >= 0 ? "+" : ""}${safeText(v)}</span>`)
    .join("");
  const headline = safeText(compare.summary_headline || compare.investor_takeaway || "SEC compare completed");
  const narrative = safeText(buildNarrativeSummary(data));
  const redFlags = Array.isArray(forensic.red_flag_ledger) ? forensic.red_flag_ledger : [];
  const moat = forensic.margin_moat_check || {};
  const moatBullets = Array.isArray(moat.bullets) ? moat.bullets : [];
  const tldrVerdict = safeText(forensic.tldr_verdict || compare.investor_takeaway || "No clear divergence verdict generated.");
  const compareConfidence = Number.isFinite(Number(compare.compare_confidence)) ? Number(compare.compare_confidence) : null;
  const analysisMode = safeText(compare.analysis_mode || data.analysis_mode || "full_text");
  const evidenceMode = analysisModeLabel(analysisMode);
  const confidenceBandLabel = confidenceBand(compareConfidence);
  const compareLimits = (compare.limits || []).slice(0, 3);
  const rationale = (compare.change_summary?.plain_english_rationale || []).slice(0, 3);
  const evidenceRanked = (compare.evidence || compare.change_summary?.evidence_ranked || []).slice(0, 4);
  const filingExcerpts = [left, right]
    .filter(Boolean)
    .map((side, idx) => {
      const label = idx === 0 ? leftLabel : rightLabel;
      const quote = safeText((side?.evidence || [])[0]?.quote || side?.high_level_takeaway || "No quote snippet.");
      return `${label} ${safeText(side?.form || "form")} (${safeText(side?.filing_date || "n/a")}): ${quote}`;
    });
  const managementQuotes = evidenceRanked
    .map((ev) => safeText(ev?.quote || ""))
    .filter(Boolean)
    .slice(0, 3);
  const calcBreadcrumbs = [
    `Compare confidence: ${compareConfidence !== null ? `${safeText(compareConfidence)}/100` : "n/a"}`,
    `Evidence quality mode: ${evidenceMode}`,
    `Metric delta fields captured: ${Object.keys(deltas || {}).length}`,
    `Red flag count: ${redFlags.length}`,
  ];
  const freshnessLeft = freshnessLabel(left?.filing_date);
  const freshnessRight = freshnessLabel(right?.filing_date);
  const warning = analysisMode !== "full_text" || compareLimits.length;
  const whatChanged = materialRaw.length ? materialRaw.slice(0, 3) : differencesRaw.slice(0, 3);
  const whyItMatters = rationale.length ? rationale : [safeText(compare.investor_takeaway || "Impact statement unavailable.")];
  const falsifierLines = (compare.what_would_falsify || compare.falsifier || compareLimits || []).slice(0, 2);
  const diffChips = [...materialRaw.slice(0, 3), ...differencesRaw.slice(0, 3)]
    .filter(Boolean)
    .slice(0, 6)
    .map((x) => `<span class="delta-chip">${safeText(x)}</span>`)
    .join("");

  headlineRoot.innerHTML = `
    <div class="report-section compare-headline-card">
      <h4>Variant vs Consensus</h4>
      <div><span class="${sentimentTagClass(sentimentTag)}">${sentimentTag}</span></div>
      <div class="compare-lead">${headline}</div>
      ${diffChips ? `<div class="sec-diff-chips">${diffChips}</div>` : ""}
      <div class="subtle">View: ${humanizeAnalysisMode(safeText(data.mode || compare.mode || "N/A"))} | Form: ${safeText(data.form_type || "N/A")} | Evidence quality: ${evidenceMode}${compareConfidence !== null ? ` | Confidence: ${safeText(compareConfidence)}/100 (${confidenceBandLabel})` : " | Confidence: Unavailable"}</div>
      <div class="subtle">Freshness: ${leftLabel} <span class="pill ${freshnessLeft.css}">${safeText(freshnessLeft.label)}</span> · ${rightLabel} <span class="pill ${freshnessRight.css}">${safeText(freshnessRight.label)}</span></div>
      ${warning ? `<div class="report-callout warn">Reduced confidence context. ${compareLimits.length ? `Limits: ${safeText(compareLimits.join("; "))}` : "Metadata fallback or partial evidence mode."}</div>` : ""}
      <ul class="report-bullets">
        <li>Claim: ${safeText(compare.investor_takeaway || headline)}</li>
        <li>Evidence: ${safeText(evidenceRanked[0]?.quote || evidenceRanked[0]?.claim || "Unavailable")}</li>
        <li>Confidence: ${compareConfidence !== null ? `${safeText(compareConfidence)}/100 (${confidenceBandLabel})` : "Unavailable"}</li>
        <li>Falsifier: ${safeText(falsifierLines[0] || compareLimits[0] || "Falsifier unavailable")}</li>
      </ul>
    </div>
  `;

  narrativeRoot.innerHTML = `
    <div class="report-section compare-narrative-card">
      <h4>Decision Narrative</h4>
      <div class="subtle">What changed</div>
      <ul class="report-bullets">
        ${(whatChanged.length ? whatChanged : differencesRaw.slice(0, 4)).map((x) => `<li>${safeText(x)}</li>`).join("") || "<li>No newly introduced legal-risk language flagged.</li>"}
      </ul>
      <div class="subtle">Why it matters</div>
      <ul class="report-bullets">${whyItMatters.map((x) => `<li>${safeText(x)}</li>`).join("")}</ul>
      <div class="subtle">What would falsify</div>
      <ul class="report-bullets">${(falsifierLines.length ? falsifierLines : ["No explicit falsifier was provided by the payload."]).map((x) => `<li>${safeText(x)}</li>`).join("")}</ul>
    </div>
  `;

  changesRoot.innerHTML = `
    <div class="report-section compare-changes-card">
      <h4>PM Support Context</h4>
      <div class="subtle">Red flag ledger</div>
      <ul class="report-bullets">
        ${(redFlags.length ? redFlags : differencesRaw.slice(0, 4)).map((x) => `<li>${safeText(x)}</li>`).join("") || "<li>No newly introduced legal-risk language flagged.</li>"}
      </ul>
      <div class="subtle">Margin &amp; Moat Check</div>
      <ul class="report-bullets">
        ${(moatBullets.length ? moatBullets : [narrative]).map((x) => `<li>${safeText(x)}</li>`).join("")}
      </ul>
      <div class="subtle">Metric Context</div>
      <div>${deltaChips || "<span class='muted'>No material metric deltas captured.</span>"}</div>
      <div class="subtle">Filing Excerpts</div>
      <ul class="report-bullets">${(filingExcerpts.length ? filingExcerpts : ["No filing excerpts available."]).map((x) => `<li>${safeText(x)}</li>`).join("")}</ul>
      <div class="subtle">Management Quotes</div>
      <ul class="report-bullets">${(managementQuotes.length ? managementQuotes : ["No management quotes surfaced."]).map((x) => `<li>${safeText(x)}</li>`).join("")}</ul>
      <div class="subtle">Calculation Breadcrumbs</div>
      <ul class="report-bullets">${calcBreadcrumbs.map((x) => `<li>${safeText(x)}</li>`).join("")}</ul>
      <div class="subtle">The "TL;DR Verdict"</div>
      <div class="compare-lead">${tldrVerdict}</div>
      <div class="subtle">Shared context</div>
      <ul class="report-bullets">${similarities || "<li>No major similarities highlighted.</li>"}</ul>
      <div class="subtle">Divergence context</div>
      <ul class="report-bullets">${material || differences || "<li>No major differences highlighted.</li>"}</ul>
      ${evidenceRanked.length ? `<div class="subtle">Top evidence snippets</div><ul class="report-bullets">${evidenceRanked.map((ev) => `<li>${safeText(ev.claim || "Evidence")}: ${safeText(ev.quote || "")}</li>`).join("")}</ul>` : ""}
    </div>
  `;

  evidenceRoot.innerHTML = `
    <div class="compare-grid">
      ${renderSecAnalysisCard(leftLabel, left)}
      ${renderSecAnalysisCard(rightLabel, right)}
    </div>
  `;
  const ruthlessMode = Boolean(data.management_dashboard?.ruthless_mode || state.secRuthlessMode);
  const dashboard = data.management_dashboard || buildFallbackManagementDashboard(data, {
    mode: data.mode || compare.mode || "ticker_over_time",
    tickerA: left?.ticker || leftLabel || "N/A",
    tickerB: right?.ticker || rightLabel || "",
  });
  const derivedMode = isDerivedFidelity(dashboard);
  const fidelityBanner = buildManagementFidelityBanner(dashboard, compare);
  const fidelityTop = document.getElementById("secCompareFidelityBanner");
  if (fidelityTop) {
    if (fidelityBanner) {
      fidelityTop.hidden = false;
      fidelityTop.innerHTML = fidelityBanner;
    } else {
      fidelityTop.hidden = true;
      fidelityTop.innerHTML = "";
    }
  }
  setSecCompareActionRow(safeText(left?.ticker || data.management_dashboard?.ticker || ""));
  renderSayDoTimeline(timelineRoot, normalizeTimelineRows(dashboard), ruthlessMode, derivedMode);
  renderIntegrityScorecard(scorecardRoot, {
    score: dashboard?.integrity_scorecard?.score,
    pillars: normalizePillars(dashboard),
  });
  renderDilutionHeatmap(heatmapRoot, normalizeHeatmapRows(dashboard), derivedMode);
  renderRedFlags(redFlagsRoot, normalizeRedFlags(dashboard), ruthlessMode);
  const derivedPanel = document.getElementById("secCompareDerivedPanel");
  if (derivedPanel && getDisplayMode() === "pro") derivedPanel.open = true;
}

export async function buildFallbackSecCompare(mode, tickerA, tickerB, formType) {
  const safeForm = (formType || "10-K").toUpperCase();
  const fetchEdgar = async (ticker) => {
    const out = await api.get(`/api/report/${ticker}?section=edgar&skip_mirofish=true&skip_edgar=false`, { timeoutMs: 180000 });
    if (!out.ok) return { ok: false, error: out.error || `Failed report fetch for ${ticker}` };
    const sectionData = out.data?.data || out.data?.edgar || null;
    if (!sectionData) return { ok: false, error: `Missing EDGAR payload for ${ticker}` };
    const filings = (sectionData.recent_filings || []).filter((f) => String(f.form || "").toUpperCase() === safeForm);
    const filing = filings[0] || sectionData.recent_filings?.[0] || {};
    return {
      ok: true,
      ticker: ticker,
      form: filing.form || safeForm,
      filing_date: filing.date || "N/A",
      filing_url: filing.url || "",
      guidance_signal: "neutral",
      key_themes: (sectionData.risk_reasons || []).slice(0, 4).map((r) => `Risk note: ${r}`),
      risk_terms: (sectionData.risk_reasons || []).map((r) => String(r).toLowerCase()),
      high_level_takeaway: (sectionData.risk_reasons || []).length
        ? sectionData.risk_reasons.slice(0, 2).join("; ")
        : "No notable filing risks in current metadata snapshot.",
      kpi_signals: {
        revenue_mentions: [],
        profit_mentions: [],
        cashflow_mentions: [],
        debt_mentions: [],
        liquidity_mentions: [],
      },
    };
  };

  const toComparePayload = (left, right, compareMode, leftLabel, rightLabel) => {
    const leftRisks = new Set(left.risk_terms || []);
    const rightRisks = new Set(right.risk_terms || []);
    const commonRisks = [...leftRisks].filter((x) => rightRisks.has(x));
    const leftOnly = [...leftRisks].filter((x) => !rightRisks.has(x));
    const rightOnly = [...rightRisks].filter((x) => !leftRisks.has(x));
    const differences = [];
    if (leftOnly.length) differences.push(`${leftLabel} unique risk notes: ${leftOnly.slice(0, 4).join(", ")}.`);
    if (rightOnly.length) differences.push(`${rightLabel} unique risk notes: ${rightOnly.slice(0, 4).join(", ")}.`);
    if (!differences.length) differences.push("Risk posture appears similar based on EDGAR metadata.");
    const sentimentTag = differences.length > 1 ? "[BEARISH CHANGE]" : "[NEUTRAL/BOILERPLATE]";
    const redFlagLedger = differences.slice(0, 3);
    const marginMoatBullets = [
      `${leftLabel}: revenue references from metadata are limited; innovation signal may be undercounted in fallback mode.`,
      `${rightLabel}: revenue references from metadata are limited; innovation signal may be undercounted in fallback mode.`,
    ];
    const tldrVerdict = `${leftLabel} vs ${rightLabel} remains inconclusive under metadata-only mode; use full SEC compare endpoint for a reliable divergence call.`;
    return {
      ok: true,
      mode: compareMode,
      form_type: safeForm,
      left,
      right,
      compare: {
        ok: true,
        mode: compareMode,
        left_label: leftLabel,
        right_label: rightLabel,
        similarities: commonRisks.length
          ? [`Shared risk notes: ${commonRisks.slice(0, 5).join(", ")}.`]
          : ["Limited overlap from metadata-only filing notes."],
        differences,
        metric_deltas: {
          revenue_mentions: 0,
          profit_mentions: 0,
          cashflow_mentions: 0,
          r_and_d_mentions: 0,
          debt_mentions: 0,
          liquidity_mentions: 0,
        },
        sentiment_tag: sentimentTag,
        forensic_divergence: {
          sentiment_tag: sentimentTag,
          red_flag_ledger: redFlagLedger,
          margin_moat_check: {
            left_label: leftLabel,
            right_label: rightLabel,
            left_revenue_refs: 0,
            left_r_and_d_refs: 0,
            right_revenue_refs: 0,
            right_r_and_d_refs: 0,
            bullets: marginMoatBullets,
          },
          tldr_verdict: tldrVerdict,
        },
        material_changes: [],
        summary_headline: "Metadata-only compare completed.",
        narrative_summary: "This compare uses EDGAR metadata fallback only. It highlights broad risk-note overlap and differences but does not parse full filing text.",
        top_differences: differences.slice(0, 3),
        top_commonalities: commonRisks.length
          ? [`Shared risk notes: ${commonRisks.slice(0, 5).join(", ")}.`]
          : ["Limited overlap from metadata-only filing notes."],
        investor_takeaway: "Fallback compare is based on EDGAR metadata only. Enable SEC compare API for deeper filing-text analysis.",
        analysis_mode: "metadata_fallback",
        compare_confidence: 25,
        limits: ["Metadata-only fallback (full filing text unavailable)"],
      },
    };
  };

  if (mode === "ticker_vs_ticker") {
    const [left, right] = await Promise.all([fetchEdgar(tickerA), fetchEdgar(tickerB)]);
    if (!left.ok) return { ok: false, error: left.error };
    if (!right.ok) return { ok: false, error: right.error };
    return toComparePayload(left, right, mode, tickerA, tickerB);
  }

  const latest = await fetchEdgar(tickerA);
  if (!latest.ok) return { ok: false, error: latest.error };
  return toComparePayload(
    { ...latest, ticker: tickerA, filing_date: latest.filing_date || "latest" },
    { ...latest, ticker: tickerA, filing_date: "prior (metadata fallback)", high_level_takeaway: "Prior filing text compare unavailable in fallback mode." },
    mode,
    `${tickerA} latest`,
    `${tickerA} prior`,
  );
}

export async function runSecCompare({ getDisplayMode = () => "balanced" } = {}) {
  const mode = document.getElementById("secCompareMode").value.trim();
  const tickerA = document.getElementById("secCompareTickerA").value.trim().toUpperCase();
  const tickerB = document.getElementById("secCompareTickerB").value.trim().toUpperCase();
  const formType = document.getElementById("secCompareFormType").value.trim().toUpperCase();
  const highlightChangesOnly = Boolean(document.getElementById("secCompareChangesOnly")?.checked);
  const ruthlessMode = document.getElementById("secCompareRuthlessMode")?.checked || false;
  const deepScan = document.getElementById("secCompareDeepScan")?.checked || false;
  const profileSelect = document.getElementById("secCompareProfile");
  const selectedProfile = profileSelect ? profileSelect.value.trim().toLowerCase() : "auto";
  const profileOverride = selectedProfile && selectedProfile !== "auto" ? selectedProfile : "";
  const profileReason = safeText(document.getElementById("secCompareProfileReason")?.value || "");
  const profileEvidence = safeText(document.getElementById("secCompareProfileEvidence")?.value || "");
  const btn = document.getElementById("secCompareBtn");
  const meta = document.getElementById("secCompareMeta");

  if (!tickerA) return;
  if (mode === "ticker_vs_ticker" && !tickerB) return;

  btn.disabled = true;
  meta.textContent = "Status: queued";
  renderSecCompareEmpty("Running SEC compare...");
  updateActionCenter({ title: "SEC Compare Running", message: "Comparing filing evidence. This can take a moment.", severity: "info" });
  try {
    let usedFallback = false;
    let profilePersistFailed = false;
    if (profileOverride || selectedProfile === "auto") {
      const profilePersistOut = await api.post(
        "/api/sec/management-dashboard/profile",
        {
          profile_override: profileOverride || null,
          reason: profileReason || null,
          evidence_ref: profileEvidence || null,
        },
        { timeoutMs: 20000 },
      );
      profilePersistFailed = !profilePersistOut?.ok;
    }
    meta.textContent = "Status: fetching data";
    let payload = null;
    const compareOut = await api.getSecCompare(
      {
        mode,
        ticker: tickerA,
        tickerB: mode === "ticker_vs_ticker" ? tickerB : "",
        formType,
        highlightChangesOnly,
        ruthlessMode,
        includeManagementDashboard: true,
        profileOverride,
      },
      { timeoutMs: 300000 },
    );
    if (compareOut.ok) {
      payload = compareOut.data;
    } else if (compareOut.status === 404 || String(compareOut.error || "").toLowerCase().includes("not found")) {
      meta.textContent = "Status: fetching data (metadata fallback mode)";
      usedFallback = true;
      const fallback = await buildFallbackSecCompare(mode, tickerA, tickerB, formType);
      if (!fallback.ok) {
        meta.textContent = "Status: partial failure (fetching failed)";
        renderSecCompareEmpty(safeText(fallback.error || "Compare failed."));
        logEvent({ kind: "report", severity: "error", message: `SEC compare fallback failed: ${fallback.error}` });
        return;
      }
      payload = fallback;
    } else {
      meta.textContent = "Status: partial failure (fetching failed)";
      renderSecCompareEmpty(safeText(compareOut.error || "Compare failed."));
      logEvent({ kind: "report", severity: "error", message: `SEC compare failed: ${compareOut.error}` });
      return;
    }
    meta.textContent = deepScan ? "Status: scoring (deep proxy scan)" : "Status: scoring";
    const dashboardOut = await fetchManagementDashboard({
      mode,
      tickerA,
      tickerB: mode === "ticker_vs_ticker" ? tickerB : "",
      ruthlessMode,
      comparePayload: payload,
      profileOverride,
      deepScan,
    });
    payload.management_dashboard = dashboardOut.data;
    payload.management_dashboard.ruthless_mode = ruthlessMode;
    payload.management_dashboard.fetch_source = dashboardOut.source;
    state.secCompareResult = payload;
    state.secManagementDashboard = payload.management_dashboard;
    state.secRuthlessMode = ruthlessMode;
    meta.textContent = "Status: drafting";
    const activeProfile = safeText(payload.management_dashboard?.profile?.selected || profileOverride || "auto_detect");
    const backendPartial = usedFallback || safeText(dashboardOut.source).toLowerCase().includes("fallback");
    const partialFailure = backendPartial || profilePersistFailed;
    if (partialFailure) {
      meta.textContent = `Status: complete with partial failure · profile: ${activeProfile} · source: ${safeText(dashboardOut.source)}`;
    } else {
      meta.textContent = `Status: complete · profile: ${activeProfile} · source: bundled_compare${deepScan ? "+deep_scan" : ""}`;
    }
    renderProfileStatusFromDashboard(payload.management_dashboard);
    renderSecCompareVisual(payload, { getDisplayMode });
    logEvent({ kind: "report", severity: "info", message: `SEC compare complete for ${tickerA}${tickerB ? ` vs ${tickerB}` : ""}.` });
    updateActionCenter({
      title: "SEC Compare Complete",
      message: `Filing compare finished for ${tickerA}${tickerB ? ` vs ${tickerB}` : ""}.`,
      severity: "success",
    });
  } finally {
    btn.disabled = false;
  }
}

export async function resetSecCompareProfileOverride() {
  const select = document.getElementById("secCompareProfile");
  if (select) select.value = "auto";
  setProfileStatusText("Profile source: auto-detect");
  const out = await api.post(
    "/api/sec/management-dashboard/profile",
    { profile_override: null, reason: "reset_to_auto", evidence_ref: null },
    { timeoutMs: 20000 },
  );
  if (!out.ok) return;
  const persisted = safeText(out.data?.profile_override || "");
  if (!persisted) {
    setProfileStatusText("Profile source: auto-detect (saved override cleared)");
  }
  renderProfileHistory(out.data?.history_tail || []);
}
