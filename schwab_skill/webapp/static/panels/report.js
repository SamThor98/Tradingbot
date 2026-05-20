/**
 * Stock report panel — `/api/report/<ticker>` is the data source.
 *
 * This visual layout is decision-first: IC Snapshot, scenario framing,
 * portfolio-fit, and monitoring accountability lead the report, while
 * existing section tabs remain as appendix detail.
 */

import { state } from "../modules/state.js";
import { api } from "../modules/api.js";
import { safeText, safeNum, formatMoney, pct, verdictFromScore, escapeHtml } from "../modules/format.js";
import { logEvent, updateActionCenter } from "../modules/logger.js";
import { normalizeReportPayload, runReportNormalizationSmokeChecks } from "../modules/reportNormalization.js";

let smokeChecked = false;

function inferredBadge(enabled) {
  return enabled ? `<span class="report-badge inferred">inferred</span>` : "";
}

function unavailable(value) {
  if (value === null || value === undefined || String(value).trim() === "") {
    return `<span class="muted">Unavailable</span>`;
  }
  return safeText(value);
}

function fmtNumberOrDash(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return `<span class='muted'>n/a</span>`;
  return n.toFixed(digits);
}

function fmtPctValue(value, digits = 1) {
  if (value === null || value === undefined || value === "") {
    return `<span class='muted'>n/a</span>`;
  }
  const n = Number(value);
  if (!Number.isFinite(n)) return `<span class='muted'>n/a</span>`;
  const pctVal = Math.abs(n) <= 1 ? n * 100 : n;
  return `${pctVal.toFixed(digits)}%`;
}

function fmtMoneyOrDash(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return `<span class='muted'>n/a</span>`;
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function fmtMoneyScaled(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return `<span class='muted'>n/a</span>`;
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(n / 1e3).toFixed(2)}K`;
  return `$${n.toFixed(2)}`;
}

function makeInstitutionalSection({ id, eyebrow, title, subtitle, body }) {
  const eyebrowText = eyebrow ? `<div class="ir-section-eyebrow">${escapeHtml(eyebrow)}</div>` : "";
  const subtitleText = subtitle
    ? `<div class="ir-section-subtitle">${escapeHtml(subtitle)}</div>`
    : "";
  const idAttr = id ? ` id="ir-section-${id}"` : "";
  return `
    <section class="ir-section"${idAttr}>
      <header class="ir-section-header">
        ${eyebrowText}
        <h3 class="ir-section-title">${escapeHtml(title)}</h3>
        ${subtitleText}
      </header>
      <div class="ir-section-body">${body}</div>
    </section>
  `;
}

function buildKeyValueTable(rows, opts = {}) {
  const className = opts.compact ? "ir-kv-table compact" : "ir-kv-table";
  const rowsHtml = rows
    .map(
      (row) => `
        <tr>
          <th scope="row">${escapeHtml(row.label)}</th>
          <td class="mono-nums">${row.value}</td>
          ${row.note ? `<td class="ir-kv-note">${escapeHtml(row.note)}</td>` : ""}
        </tr>
      `,
    )
    .join("");
  const colCount = rows.some((r) => r.note) ? 3 : 2;
  return `
    <div class="table-wrap report-table-wrap">
      <table class="${className}" data-cols="${colCount}">
        <tbody>${rowsHtml}</tbody>
      </table>
    </div>
  `;
}

function buildDataTable({ headers, rows, emptyMessage = "No data available." }) {
  if (!Array.isArray(rows) || rows.length === 0) {
    return `<div class="report-callout">${escapeHtml(emptyMessage)}</div>`;
  }
  const head = `<tr>${headers.map((h) => `<th>${escapeHtml(h.label)}</th>`).join("")}</tr>`;
  const body = rows
    .map((row) => {
      const cells = headers
        .map((h, idx) => {
          const cell = row[idx];
          const cellHtml = cell == null
            ? `<span class='muted'>n/a</span>`
            : (typeof cell === "object" && cell.html ? cell.html : escapeHtml(String(cell)));
          const align = h.align === "right" ? " class=\"mono-nums right\"" : (h.align === "center" ? " class=\"center\"" : "");
          return `<td${align}>${cellHtml}</td>`;
        })
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  return `
    <div class="table-wrap report-table-wrap">
      <table class="ir-data-table report-scenario-table">
        <thead>${head}</thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

function paragraph(text) {
  return `<p class="ir-paragraph">${escapeHtml(String(text || ""))}</p>`;
}

function bulletList(items, opts = {}) {
  const list = (items || []).filter((x) => x !== null && x !== undefined && String(x).trim() !== "");
  if (!list.length) {
    return `<ul class="report-bullets"><li class="muted">${escapeHtml(opts.empty || "Unavailable")}</li></ul>`;
  }
  const liClass = opts.numbered ? "" : "";
  const tag = opts.numbered ? "ol" : "ul";
  return `<${tag} class="report-bullets">${list.map((item) => `<li${liClass ? ` class=\"${liClass}\"` : ""}>${escapeHtml(String(item))}</li>`).join("")}</${tag}>`;
}

function confidenceText(ic) {
  if (!Number.isFinite(Number(ic.confidence_score))) return safeText(ic.confidence_label || "Unavailable");
  return `${safeText(ic.confidence_label)} (${safeText(ic.confidence_score)}/100)`;
}

function renderClaimEvidence({ claim, evidence, confidence, falsifier }) {
  return `
    <div class="report-claim-grid">
      <div><span class="subtle">Claim</span><div>${unavailable(claim)}</div></div>
      <div><span class="subtle">Evidence</span><div>${unavailable(evidence)}</div></div>
      <div><span class="subtle">Confidence</span><div>${unavailable(confidence)}</div></div>
      <div><span class="subtle">Falsifier</span><div>${unavailable(falsifier)}</div></div>
    </div>
  `;
}

function getAppendixSections(normalized) {
  const d = normalized.appendix || {};
  const candidates = ["summary", "technical", "dcf", "comps", "health", "edgar", "mirofish", "synthesis"];
  return candidates.filter((key) => key === "summary" || d[key] !== undefined && d[key] !== null);
}

function buildAppendixBlocks(normalized) {
  const d = normalized.appendix || {};
  const ticker = normalized.ticker || "—";
  const tech = d.technical || null;
  const dcf = d.dcf || null;
  const health = d.health || null;
  const comps = d.comps || null;
  const edgar = d.edgar || null;
  const miro = d.mirofish || null;
  const synthesis = d.synthesis || "";
  const sectionVerdicts = {
    technical: verdictFromScore(tech?.signal_score ?? 50, 65, 45),
    dcf: verdictFromScore(dcf?.margin_of_safety ?? 0, 10, -10),
    health: (health?.flags || []).length === 0 ? "bullish" : (health.flags.length >= 3 ? "bearish" : "neutral"),
    mirofish: verdictFromScore(miro?.conviction_score ?? 0, 30, -30),
  };
  return {
    summary: `
      <div class="report-section">
        <h4>Appendix: Summary</h4>
        <div class="subtle">Legacy section-level views are preserved.</div>
        <ul class="report-bullets">
          <li>Ticker: ${safeText(ticker)}</li>
          <li>Technical Verdict: <span class="verdict ${sectionVerdicts.technical}">${sectionVerdicts.technical}</span></li>
          <li>DCF Verdict: <span class="verdict ${sectionVerdicts.dcf}">${sectionVerdicts.dcf}</span></li>
          <li>Health Verdict: <span class="verdict ${sectionVerdicts.health}">${sectionVerdicts.health}</span></li>
          <li>MiroFish Verdict: <span class="verdict ${sectionVerdicts.mirofish}">${sectionVerdicts.mirofish}</span></li>
        </ul>
      </div>`,
    technical: tech ? `
      <div class="report-section">
        <h4>Appendix: Technical <span class="verdict ${sectionVerdicts.technical}">${sectionVerdicts.technical}</span></h4>
        <ul class="report-bullets">
          <li>Price: ${formatMoney(tech.current_price)}</li>
          <li>52w Range: ${formatMoney(tech.low_52w)} - ${formatMoney(tech.high_52w)}</li>
          <li>SMA 50/150/200: ${formatMoney(tech.sma_50)} / ${formatMoney(tech.sma_150)} / ${formatMoney(tech.sma_200)}</li>
          <li>VCP: ${tech.vcp ? "YES" : "NO"} | Sector: ${safeText(tech.sector_etf)}</li>
          <li>Takeaway: ${tech.stage_2 && tech.vcp ? "Trend and volume structure are aligned." : "Setup quality is incomplete."}</li>
        </ul>
      </div>` : "",
    dcf: dcf ? `
      <div class="report-section">
        <h4>Appendix: DCF <span class="verdict ${sectionVerdicts.dcf}">${sectionVerdicts.dcf}</span></h4>
        <ul class="report-bullets">
          <li>Intrinsic Value: ${formatMoney(dcf.intrinsic_value)}</li>
          <li>Current Price: ${formatMoney(dcf.current_price)}</li>
          <li>Margin of Safety: ${safeNum(dcf.margin_of_safety).toFixed(1)}%</li>
          <li>Growth / WACC / Terminal: ${pct(dcf.growth_rate)} / ${pct(dcf.wacc)} / ${pct(dcf.terminal_growth)}</li>
          <li>Takeaway: ${safeNum(dcf.margin_of_safety) >= 0 ? "Valuation supports upside." : "Valuation implies premium pricing."}</li>
        </ul>
      </div>` : "",
    comps: comps ? `
      <div class="report-section">
        <h4>Appendix: Comps</h4>
        <ul class="report-bullets">
          <li>Peers: ${(comps.peers || []).slice(0, 6).map((p) => p.ticker).join(", ") || "—"}</li>
          <li>Median P/E: ${safeText(comps.median_pe)} | Median P/S: ${safeText(comps.median_ps)}</li>
          <li>Implied P/E: ${formatMoney(comps.implied_price_pe)} | Implied P/S: ${formatMoney(comps.implied_price_ps)}</li>
          <li>Takeaway: Comps provide cross-check against standalone DCF assumptions.</li>
        </ul>
      </div>` : "",
    health: health ? `
      <div class="report-section">
        <h4>Appendix: Health <span class="verdict ${sectionVerdicts.health}">${sectionVerdicts.health}</span></h4>
        <ul class="report-bullets">
          <li>Current Ratio: ${safeText(health.current_ratio)}</li>
          <li>Debt/Equity: ${safeText(health.debt_to_equity)}</li>
          <li>Interest Coverage: ${safeText(health.interest_coverage)}x</li>
          <li>ROE: ${pct(health.roe)} | Op Margin: ${pct(health.operating_margin)}</li>
          <li>Flags: ${(health.flags || []).length ? health.flags.slice(0, 3).join("; ") : "None"}</li>
        </ul>
      </div>` : "",
    edgar: edgar ? `
      <div class="report-section">
        <h4>Appendix: EDGAR</h4>
        <ul class="report-bullets">
          <li>Risk Tag: ${safeText(edgar.risk_tag).toUpperCase()}</li>
          <li>Recent 8-K: ${edgar.recent_8k ? "YES" : "NO"}</li>
          <li>Filing Recency: ${safeText(edgar.filing_recency_days)} day(s)</li>
          <li>Takeaway: ${(edgar.risk_reasons || []).slice(0, 2).join("; ") || "No notable filing risks."}</li>
        </ul>
      </div>` : "",
    mirofish: miro ? `
      <div class="report-section">
        <h4>Appendix: MiroFish <span class="verdict ${sectionVerdicts.mirofish}">${sectionVerdicts.mirofish}</span></h4>
        <ul class="report-bullets">
          <li>Conviction: ${safeText(miro.conviction_score)}</li>
          <li>Continuation: ${pct(miro.continuation_probability, 0)}</li>
          <li>Bull Trap: ${pct(miro.bull_trap_probability, 0)}</li>
          <li>Takeaway: ${safeText(miro.summary || "No summary provided.")}</li>
        </ul>
      </div>` : "",
    synthesis: synthesis ? `
      <div class="report-section">
        <h4>Appendix: Synthesis</h4>
        <div class="report-text">${safeText(synthesis)}</div>
      </div>` : "",
  };
}

function renderScenarioRow(row) {
  const probability = Number.isFinite(Number(row.probability)) ? `${safeNum(row.probability).toFixed(0)}%` : "<span class='muted'>Unavailable</span>";
  const returnPct = Number.isFinite(Number(row.return_pct)) ? `${safeNum(row.return_pct).toFixed(1)}%` : "<span class='muted'>Unavailable</span>";
  const target = row.price_target == null || row.price_target === "" ? "<span class='muted'>Unavailable</span>" : safeText(row.price_target);
  return `
    <tr>
      <td>${safeText(row.name)}</td>
      <td class="mono-nums">${probability}</td>
      <td class="mono-nums">${returnPct}</td>
      <td class="mono-nums">${target}</td>
    </tr>
  `;
}

function buildCoverHeader(rawData, normalized) {
  const snap = rawData?.finnhub_snapshot || {};
  const profile = snap?.profile || {};
  const quote = snap?.quote || {};
  const metrics = snap?.metrics || {};
  const ic = normalized?.ic_snapshot || {};
  const ticker = normalized?.ticker || "—";
  const companyName = profile?.name || ticker;
  const industry = profile?.finnhub_industry || profile?.industry || "Equity Research";
  const exchange = profile?.exchange || "—";
  const country = profile?.country || "Global";
  const generatedAt = rawData?.generated_at || "";
  const recommendation = ic.recommendation || "Pass";
  const confidence = ic.confidence_label
    ? `${ic.confidence_label}${Number.isFinite(Number(ic.confidence_score)) ? ` (${ic.confidence_score}/100)` : ""}`
    : "Unavailable";
  const horizon = ic.time_horizon || "3-12 months";
  const expectedReturn = ic.expected_return_base_case != null
    ? `${safeNum(ic.expected_return_base_case).toFixed(1)}%`
    : "n/a";
  const recClass = String(recommendation).toLowerCase().includes("long") || String(recommendation).toLowerCase().includes("buy")
    ? "ir-rec-long"
    : (String(recommendation).toLowerCase().includes("short") || String(recommendation).toLowerCase().includes("sell") ? "ir-rec-short" : "ir-rec-pass");

  return `
    <header class="ir-cover">
      <div class="ir-cover-eyebrow">Institutional Research Report · ${escapeHtml(industry)}</div>
      <h2 class="ir-cover-title">${escapeHtml(companyName)} <span class="ir-cover-ticker">(${escapeHtml(ticker)})</span></h2>
      <div class="ir-cover-meta">
        <span>Exchange: <strong>${escapeHtml(exchange)}</strong></span>
        <span>Region: <strong>${escapeHtml(country)}</strong></span>
        <span>Prepared: <strong>${escapeHtml(generatedAt || "—")}</strong></span>
      </div>
      <div class="ir-cover-strip">
        <div class="ir-cover-cell"><span class="ir-cover-cell-label">Recommendation</span><span class="ir-cover-cell-value ${recClass}">${escapeHtml(recommendation)}</span></div>
        <div class="ir-cover-cell"><span class="ir-cover-cell-label">Confidence</span><span class="ir-cover-cell-value">${escapeHtml(confidence)}</span></div>
        <div class="ir-cover-cell"><span class="ir-cover-cell-label">Expected Return (Base)</span><span class="ir-cover-cell-value mono-nums">${escapeHtml(expectedReturn)}</span></div>
        <div class="ir-cover-cell"><span class="ir-cover-cell-label">Horizon</span><span class="ir-cover-cell-value">${escapeHtml(horizon)}</span></div>
      </div>
      <div class="ir-cover-quote">
        <span>Current Price: <strong class="mono-nums">${fmtMoneyOrDash(quote?.current)}</strong></span>
        <span>52-Week Range: <strong class="mono-nums">${fmtMoneyOrDash(metrics?.["52week_low"])} – ${fmtMoneyOrDash(metrics?.["52week_high"])}</strong></span>
      </div>
    </header>
  `;
}

function buildExecutiveSummarySection(rawData, normalized) {
  const ic = normalized?.ic_snapshot || {};
  const thesis = normalized?.thesis || {};
  const technical = rawData?.technical || {};
  const dcf = rawData?.dcf || {};
  const snap = rawData?.finnhub_snapshot || {};
  const trends = snap?.recommendation_trends || {};
  const profile = snap?.profile || {};
  const businessName = profile?.name || normalized?.ticker || "This issuer";

  const signalScore = technical?.signal_score;
  const mos = dcf?.margin_of_safety;
  const buyVotes = (Number(trends?.buy) || 0) + (Number(trends?.strong_buy) || 0);
  const sellVotes = (Number(trends?.sell) || 0) + (Number(trends?.strong_sell) || 0);

  const headlineParagraph = thesis.claim
    ? thesis.claim
    : `${businessName} is currently framed as ${ic.recommendation || "Pass"} with ${ic.confidence_label || "unknown"} confidence over a ${ic.time_horizon || "3-12 month"} horizon.`;

  const supportingParagraph = (
    `${normalized.ticker || "The issuer"} screens with technical signal ${Number.isFinite(Number(signalScore)) ? `${safeNum(signalScore).toFixed(0)}/100` : "n/a"} ` +
    `and DCF margin of safety ${Number.isFinite(Number(mos)) ? `${safeNum(mos).toFixed(1)}%` : "n/a"}. ` +
    `Street positioning shows ${buyVotes} bullish vs ${sellVotes} bearish recommendation votes from Finnhub. ` +
    `Portfolio risk-budget impact reads ${normalized?.portfolio_fit?.risk_budget_impact || "Unavailable"}.`
  );

  const claimEvidence = `
    <div class="report-claim-grid">
      <div><span class="subtle">Claim</span><div>${unavailable(thesis.claim)}</div></div>
      <div><span class="subtle">Evidence</span><div>${unavailable(thesis.evidence)}</div></div>
      <div><span class="subtle">Confidence</span><div>${unavailable(thesis.confidence)}</div></div>
      <div><span class="subtle">Falsifier</span><div>${unavailable(thesis.falsifier)}</div></div>
    </div>
  `;

  const thesisAndRisks = `
    <div class="report-split-grid">
      <div>
        <div class="ir-subhead">Top Thesis Points</div>
        ${bulletList(ic.top_thesis_points, { empty: "Unavailable" })}
      </div>
      <div>
        <div class="ir-subhead">Top Risks</div>
        ${bulletList(ic.top_risks, { empty: "Unavailable" })}
      </div>
    </div>
  `;

  const body = `
    ${paragraph(headlineParagraph)}
    ${paragraph(supportingParagraph)}
    ${claimEvidence}
    ${thesisAndRisks}
  `;

  return makeInstitutionalSection({
    id: "executive_summary",
    eyebrow: "Investment Summary",
    title: "Executive Investment Summary",
    subtitle: "Decision-first synthesis for IC review and position expression.",
    body,
  });
}

function buildBusinessModelSection(rawData, normalized) {
  const snap = rawData?.finnhub_snapshot || {};
  const profile = snap?.profile || {};
  const ticker = normalized?.ticker || "—";
  const technical = rawData?.technical || {};

  const rows = [
    { label: "Issuer", value: escapeHtml(profile?.name || ticker) },
    { label: "Industry", value: escapeHtml(profile?.finnhub_industry || profile?.industry || "n/a") },
    { label: "Exchange", value: escapeHtml(profile?.exchange || "n/a") },
    { label: "Country / Currency", value: escapeHtml(`${profile?.country || "n/a"} / ${profile?.currency || "n/a"}`) },
    { label: "Market Cap", value: fmtMoneyScaled(profile?.market_cap) },
    { label: "IPO Date", value: escapeHtml(profile?.ipo || "n/a") },
    { label: "Sector ETF Proxy", value: escapeHtml(technical?.sector_etf || "Unknown") },
  ];

  const narrative = (
    `${profile?.name || ticker} operates in ${profile?.finnhub_industry || "its core market"} ` +
    `and is referenced against the ${technical?.sector_etf || "sector"} ETF for relative-strength context. ` +
    `Operating geography is ${profile?.country || "global"}; reporting currency is ${profile?.currency || "USD"}.`
  );

  const body = `
    ${paragraph(narrative)}
    ${buildKeyValueTable(rows)}
  `;

  return makeInstitutionalSection({
    id: "business_model",
    eyebrow: "Part I",
    title: "Company and Business Model",
    subtitle: "Issuer profile, geography, and operating context.",
    body,
  });
}

function buildFundamentalsSection(rawData) {
  const snap = rawData?.finnhub_snapshot || {};
  const metrics = snap?.metrics || {};
  const earnings = Array.isArray(snap?.earnings) ? snap.earnings.slice(0, 6) : [];

  const fundamentalsTable = buildDataTable({
    headers: [
      { label: "Metric" },
      { label: "Value", align: "right" },
      { label: "Commentary" },
    ],
    rows: [
      ["Revenue Growth (TTM YoY)", { html: fmtPctValue(metrics?.revenue_growth_ttm_yoy) }, "Top-line growth momentum"],
      ["EPS Growth (TTM YoY)", { html: fmtPctValue(metrics?.eps_growth_ttm_yoy) }, "Earnings trajectory"],
      ["Operating Margin (TTM)", { html: fmtPctValue(metrics?.operating_margin_ttm) }, "Operating efficiency"],
      ["Net Margin (TTM)", { html: fmtPctValue(metrics?.net_margin_ttm) }, "Bottom-line profitability"],
      ["Return on Equity (TTM)", { html: fmtPctValue(metrics?.roe_ttm) }, "Capital efficiency"],
      ["Return on Assets (TTM)", { html: fmtPctValue(metrics?.roa_ttm) }, "Asset productivity"],
      ["Current Ratio (Q)", { html: fmtNumberOrDash(metrics?.current_ratio_quarterly, 2) }, "Short-term liquidity"],
      ["Debt / Equity (Q)", { html: fmtNumberOrDash(metrics?.debt_to_equity_quarterly, 2) }, "Leverage posture"],
    ],
    emptyMessage: "Fundamental metrics unavailable.",
  });

  const earningsTable = buildDataTable({
    headers: [
      { label: "Period" },
      { label: "Actual EPS", align: "right" },
      { label: "Estimate EPS", align: "right" },
      { label: "Surprise %", align: "right" },
    ],
    rows: earnings.map((row) => [
      row?.period || "n/a",
      { html: fmtNumberOrDash(row?.actual, 2) },
      { html: fmtNumberOrDash(row?.estimate, 2) },
      { html: fmtPctValue(row?.surprise_percent, 1) },
    ]),
    emptyMessage: "No recent earnings prints from Finnhub.",
  });

  const narrative = (
    "Earnings dispersion and margin trajectory remain central to near-term re-rating potential. " +
    "Read these metrics together with valuation context: profitable growth at expanding margins typically supports multiple expansion, " +
    "while declining margins or earnings misses can compress multiples even when growth is intact."
  );

  const body = `
    ${paragraph(narrative)}
    <div class="ir-subhead">Headline Fundamental Metrics</div>
    ${fundamentalsTable}
    <div class="ir-subhead">Recent Earnings Cadence</div>
    ${earningsTable}
  `;

  return makeInstitutionalSection({
    id: "fundamentals",
    eyebrow: "Part II",
    title: "Fundamental Performance",
    subtitle: "Growth, margins, capital efficiency, and earnings cadence.",
    body,
  });
}

function buildValuationTechnicalSection(rawData) {
  const snap = rawData?.finnhub_snapshot || {};
  const metrics = snap?.metrics || {};
  const dcf = rawData?.dcf || {};
  const technical = rawData?.technical || {};
  const comps = rawData?.comps || {};

  const valuationTable = buildDataTable({
    headers: [
      { label: "Metric" },
      { label: "Value", align: "right" },
    ],
    rows: [
      ["DCF Intrinsic Value", { html: fmtMoneyOrDash(dcf?.intrinsic_value) }],
      ["DCF Margin of Safety", { html: fmtPctValue(dcf?.margin_of_safety) }],
      ["P/E (TTM)", { html: fmtNumberOrDash(metrics?.pe_ttm) }],
      ["P/B (Annual)", { html: fmtNumberOrDash(metrics?.pb_annual) }],
      ["P/S (TTM)", { html: fmtNumberOrDash(metrics?.ps_ttm) }],
      ["EV / EBITDA", { html: fmtNumberOrDash(metrics?.ev_to_ebitda) }],
      ["EV / Sales", { html: fmtNumberOrDash(metrics?.ev_to_sales) }],
      ["Median Peer P/E", { html: fmtNumberOrDash(comps?.median_pe) }],
      ["Implied Price (P/E)", { html: fmtMoneyOrDash(comps?.implied_price_pe) }],
      ["Implied Price (P/S)", { html: fmtMoneyOrDash(comps?.implied_price_ps) }],
    ],
    emptyMessage: "Valuation metrics unavailable.",
  });

  const technicalTable = buildDataTable({
    headers: [
      { label: "Metric" },
      { label: "Value", align: "right" },
    ],
    rows: [
      ["Current Price", { html: fmtMoneyOrDash(technical?.current_price) }],
      ["52w High / Low", { html: `${fmtMoneyOrDash(technical?.high_52w)} / ${fmtMoneyOrDash(technical?.low_52w)}` }],
      ["SMA 50 / 150 / 200", { html: `${fmtMoneyOrDash(technical?.sma_50)} / ${fmtMoneyOrDash(technical?.sma_150)} / ${fmtMoneyOrDash(technical?.sma_200)}` }],
      ["Stage 2", { html: technical?.stage_2 ? "<strong>YES</strong>" : "NO" }],
      ["VCP Volume Pattern", { html: technical?.vcp ? "<strong>YES</strong>" : "NO" }],
      ["Signal Score", { html: fmtNumberOrDash(technical?.signal_score, 1) + " / 100" }],
      ["Sector ETF", { html: escapeHtml(technical?.sector_etf || "Unknown") }],
    ],
    emptyMessage: "Technical structure unavailable.",
  });

  const trendDescription = technical?.stage_2
    ? "Trend structure currently sits in Stage 2, supporting constructive trend-continuation framing."
    : "Trend structure does not currently meet Stage 2 conditions, which limits breakout reliability.";
  const valuationDescription = Number.isFinite(Number(dcf?.margin_of_safety)) && Number(dcf.margin_of_safety) > 0
    ? "Intrinsic-value framework points to a positive margin of safety, supporting a long-side valuation underwrite."
    : "Intrinsic-value framework does not currently provide a clean valuation cushion, raising the bar for the trend and catalyst case.";

  const narrative = `${trendDescription} ${valuationDescription}`;

  const body = `
    ${paragraph(narrative)}
    <div class="ir-subhead">Valuation</div>
    ${valuationTable}
    <div class="ir-subhead">Technical Positioning</div>
    ${technicalTable}
  `;

  return makeInstitutionalSection({
    id: "valuation_technical",
    eyebrow: "Part III",
    title: "Valuation and Technical Positioning",
    subtitle: "Intrinsic value, multiples, and trend structure.",
    body,
  });
}

function buildSecNarrativeSection(rawData) {
  const edgar = rawData?.edgar || {};
  const filing = edgar?.filing_analysis || {};
  const summaryHeadline = filing?.summary_headline || filing?.high_level_takeaway || "";
  const narrativeSummary = filing?.narrative_summary || "";
  const filingsTable = buildDataTable({
    headers: [
      { label: "Form" },
      { label: "Date" },
      { label: "Description" },
    ],
    rows: (edgar?.recent_filings || []).slice(0, 6).map((f) => [
      f?.form || "—",
      f?.date || "—",
      f?.description || "—",
    ]),
    emptyMessage: "No recent EDGAR filings surfaced for this issuer.",
  });

  const riskTagText = (edgar?.risk_tag || "unknown").toString().toUpperCase();
  const recencyText = edgar?.filing_recency_days != null ? `${edgar.filing_recency_days} day(s)` : "n/a";

  const narrative = summaryHeadline
    ? `Filing intelligence headline: ${summaryHeadline}`
    : "Filing intelligence headline is unavailable. Treat narrative interpretation with caution.";
  const detail = narrativeSummary || "Detailed filing narrative is unavailable; rely on disclosed risk tag and filing cadence.";

  const body = `
    ${paragraph(narrative)}
    ${paragraph(detail)}
    <div class="ir-subhead">Filing Snapshot</div>
    ${buildKeyValueTable([
      { label: "Risk Tag", value: escapeHtml(riskTagText) },
      { label: "Recent 8-K?", value: edgar?.recent_8k ? "YES" : "NO" },
      { label: "Latest Filing Recency", value: escapeHtml(recencyText) },
      { label: "Risk Reasons", value: edgar?.risk_reasons?.length ? escapeHtml((edgar.risk_reasons || []).slice(0, 3).join("; ")) : "<span class='muted'>None</span>" },
    ])}
    <div class="ir-subhead">Recent Filings</div>
    ${filingsTable}
  `;

  return makeInstitutionalSection({
    id: "sec_narrative",
    eyebrow: "Part IV",
    title: "SEC Narrative and Filing Deltas",
    subtitle: "Filing intelligence, disclosure drift, and risk posture.",
    body,
  });
}

function buildPortfolioFitSection(normalized) {
  const fit = normalized?.portfolio_fit || {};
  const fallback = fit?.fallback_message ? `<div class="report-callout warn">${escapeHtml(fit.fallback_message)}</div>` : "";

  const rows = [
    { label: "Sector Tag", value: escapeHtml(fit?.sector || "Unknown") },
    { label: "Sector Overlap %", value: fit?.sector_overlap_pct != null ? `${safeNum(fit.sector_overlap_pct).toFixed(2)}%` : "<span class='muted'>n/a</span>" },
    { label: "Concentration Contribution %", value: fit?.concentration_contribution_pct != null ? `${safeNum(fit.concentration_contribution_pct).toFixed(2)}%` : "<span class='muted'>n/a</span>" },
    { label: "Correlation / Overlap Proxy", value: escapeHtml(String(fit?.correlation_overlap_proxy || "Unavailable")) },
    { label: "Risk Budget Impact", value: escapeHtml(String(fit?.risk_budget_impact || "Unavailable")) },
    { label: "Exposure Budget Remaining %", value: fit?.exposure_budget_remaining_pct != null ? `${safeNum(fit.exposure_budget_remaining_pct).toFixed(2)}%` : "<span class='muted'>n/a</span>" },
  ];

  const narrative = (
    `Portfolio fit summarizes how this position would interact with current holdings. ` +
    `Risk budget impact is read as ${fit?.risk_budget_impact || "Unavailable"}, ` +
    `with sector overlap ${fit?.sector_overlap_pct != null ? `at ${safeNum(fit.sector_overlap_pct).toFixed(2)}%` : "unavailable"}.`
  );

  const body = `
    ${fallback}
    ${paragraph(narrative)}
    ${buildKeyValueTable(rows)}
  `;

  return makeInstitutionalSection({
    id: "portfolio_fit",
    eyebrow: "Part V",
    title: "Portfolio Fit and Risk Budget",
    subtitle: "Sector overlap, concentration, and sizing context.",
    body,
  });
}

function buildCatalystsRisksSection(normalized) {
  const catalysts = normalized?.catalyst_calendar || [];
  const risks = normalized?.risk_register || [];
  const ic = normalized?.ic_snapshot || {};

  const matrix = buildDataTable({
    headers: [
      { label: "Type" },
      { label: "Item" },
    ],
    rows: [
      ...catalysts.slice(0, 8).map((c) => ["Catalyst", String(c)]),
      ...risks.slice(0, 8).map((r) => ["Risk", String(r)]),
    ],
    emptyMessage: "No catalysts or risks surfaced from current inputs.",
  });

  const invalidationCallout = ic?.invalidation_criteria
    ? `<div class="report-callout"><strong>Invalidation:</strong> ${escapeHtml(ic.invalidation_criteria)}</div>`
    : "";

  const body = `
    ${paragraph("Catalysts and risks are aggregated from filing cadence, sentiment signals, and quantitative checks. Invalidation criteria define when the thesis must be re-underwritten.")}
    ${invalidationCallout}
    ${matrix}
  `;

  return makeInstitutionalSection({
    id: "catalysts_risks",
    eyebrow: "Part VI",
    title: "Catalyst and Risk Matrix",
    subtitle: "Forward catalysts, risks, and invalidation triggers.",
    body,
  });
}

function buildScenarioSection(normalized) {
  const scenarios = normalized?.scenarios || {};
  const inferredBadgeHtml = scenarios.inferred ? "<span class='report-badge inferred'>inferred</span>" : "";
  const warning = scenarios.warning ? `<div class="report-callout warn">${escapeHtml(scenarios.warning)}</div>` : "";
  const rows = (scenarios.rows || []).map((row) => [
    row.name || "—",
    { html: Number.isFinite(Number(row.probability)) ? `${safeNum(row.probability).toFixed(0)}%` : "<span class='muted'>n/a</span>" },
    { html: Number.isFinite(Number(row.return_pct)) ? `${safeNum(row.return_pct).toFixed(1)}%` : "<span class='muted'>n/a</span>" },
    { html: row.price_target == null || row.price_target === "" ? "<span class='muted'>n/a</span>" : escapeHtml(String(row.price_target)) },
    row.rationale || "—",
  ]);

  const scenarioTable = buildDataTable({
    headers: [
      { label: "Scenario" },
      { label: "Probability", align: "right" },
      { label: "Return Target", align: "right" },
      { label: "Price Target", align: "right" },
      { label: "Rationale" },
    ],
    rows,
    emptyMessage: "Scenario analysis unavailable.",
  });

  const kpis = `
    <div class="report-scenario-kpis">
      <div><span class="subtle">Expected Value</span><div class="mono-nums">${scenarios.expected_value_pct != null ? `${safeNum(scenarios.expected_value_pct).toFixed(2)}%` : "<span class='muted'>n/a</span>"}</div></div>
      <div><span class="subtle">Upside / Downside Ratio</span><div class="mono-nums">${scenarios.upside_downside_ratio != null ? `${safeNum(scenarios.upside_downside_ratio).toFixed(2)}x` : "<span class='muted'>n/a</span>"}</div></div>
    </div>
  `;

  const sensitivity = `
    <div class="ir-subhead">Sensitivity Notes</div>
    ${bulletList(scenarios.sensitivity_bullets, { empty: "No sensitivity bullets supplied." })}
  `;

  const body = `
    ${warning}
    ${scenarioTable}
    ${kpis}
    ${sensitivity}
  `;

  return makeInstitutionalSection({
    id: "scenarios",
    eyebrow: "Scenario Framing",
    title: `Scenario Analysis ${inferredBadgeHtml}`,
    subtitle: "Probability-weighted base, bull, and bear cases with sensitivity notes.",
    body,
  });
}

function buildMonitoringSection(normalized) {
  const plan = normalized?.monitoring_plan || {};
  const body = `
    <div class="report-claim-grid">
      <div><span class="subtle">Position Expression</span><div>${unavailable(plan.claim)}</div></div>
      <div><span class="subtle">Evidence</span><div>${unavailable(plan.evidence)}</div></div>
      <div><span class="subtle">Confidence</span><div>${unavailable(plan.confidence)}</div></div>
      <div><span class="subtle">Falsifier</span><div>${unavailable(plan.falsifier)}</div></div>
    </div>
    <div class="ir-subhead">Triggers</div>
    ${bulletList(plan.triggers, { empty: "No triggers defined." })}
    <div class="subtle">Review cadence: ${escapeHtml(plan.review_cadence || "Unavailable")}</div>
  `;

  return makeInstitutionalSection({
    id: "monitoring",
    eyebrow: "Monitoring",
    title: "Monitoring Plan",
    subtitle: "Cadence, kill switches, and review triggers.",
    body,
  });
}

function buildTrustHeaderSection(rawData) {
  const trust = rawData?.report_trust || {};
  const trusted = Boolean(trust?.trusted);
  const statusClassName = trusted ? "good" : "warn";
  const statusText = trusted ? "Trusted" : "Needs Review";
  const confidence = Number.isFinite(Number(trust?.data_confidence))
    ? `${(Number(trust.data_confidence) * 100).toFixed(1)}%`
    : "n/a";
  const citation = Number.isFinite(Number(trust?.citation_completeness))
    ? `${(Number(trust.citation_completeness) * 100).toFixed(1)}%`
    : "n/a";
  const conflicts = Number.isFinite(Number(trust?.unresolved_conflict_count))
    ? String(trust.unresolved_conflict_count)
    : "n/a";
  const freshness = trust?.freshness_status?.ok ? "fresh" : "stale";

  const body = `
    <div class="report-scenario-kpis">
      <div><span class="subtle">Trust Status</span><div class="pill ${statusClassName}">${statusText}</div></div>
      <div><span class="subtle">Data Confidence</span><div class="mono-nums">${confidence}</div></div>
      <div><span class="subtle">Citation Completeness</span><div class="mono-nums">${citation}</div></div>
      <div><span class="subtle">Conflicts</span><div class="mono-nums">${conflicts}</div></div>
      <div><span class="subtle">Freshness</span><div class="mono-nums">${freshness}</div></div>
    </div>
  `;
  return makeInstitutionalSection({
    id: "trust_header",
    eyebrow: "Trust Gate",
    title: "Report Trust Header",
    subtitle: "Source-of-truth gate for this generated report.",
    body,
  });
}

function parseIsoDate(value) {
  if (!value) return null;
  const dt = new Date(value);
  return Number.isFinite(dt.getTime()) ? dt : null;
}

function freshnessBadgeFromDate(value) {
  const dt = parseIsoDate(value);
  if (!dt) return { label: "unknown", css: "warn", ageDays: null };
  const ageDays = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 86400000));
  if (ageDays <= 7) return { label: "fresh", css: "good", ageDays };
  if (ageDays <= 45) return { label: "watch", css: "warn", ageDays };
  return { label: "stale", css: "warn", ageDays };
}

function buildEvidenceTraceSection(rawData) {
  const trust = rawData?.report_trust || {};
  const edgar = rawData?.edgar || {};
  const generatedAt = rawData?.generated_at || "";
  const freshness = freshnessBadgeFromDate(generatedAt);
  const filings = (edgar?.recent_filings || []).slice(0, 3);
  const filingExcerpts = filings.length
    ? filings.map((f) => `${safeText(f.form || "Filing")} (${safeText(f.date || "n/a")}): ${safeText(f.description || "No description.")}`)
    : [];
  const managementQuotes = (trust?.analyst_take || [])
    .map((row) => safeText(row?.text || ""))
    .filter(Boolean)
    .slice(0, 3);
  const dcf = rawData?.dcf || {};
  const technical = rawData?.technical || {};
  const pctText = (value, digits = 2) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "n/a";
    const pctVal = Math.abs(n) <= 1 ? n * 100 : n;
    return `${pctVal.toFixed(digits)}%`;
  };
  const numText = (value, digits = 1) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "n/a";
    return n.toFixed(digits);
  };
  const calcBreadcrumbs = [
    `DCF growth rate: ${pctText(dcf?.growth_rate, 2)}`,
    `DCF WACC: ${pctText(dcf?.wacc, 2)}`,
    `Terminal growth: ${pctText(dcf?.terminal_growth, 2)}`,
    `Margin of safety: ${pctText(dcf?.margin_of_safety, 1)}`,
    `Technical signal score: ${numText(technical?.signal_score, 1)}/100`,
  ];
  const freshnessLine = generatedAt
    ? `Generated at ${escapeHtml(generatedAt)} · age ${freshness.ageDays ?? "n/a"} day(s)`
    : "Generated timestamp unavailable.";

  const body = `
    <div class="report-scenario-kpis">
      <div><span class="subtle">Timestamp Freshness</span><div class="pill ${freshness.css}">${escapeHtml(freshness.label)}</div></div>
      <div><span class="subtle">Generated At</span><div class="mono-nums">${escapeHtml(generatedAt || "n/a")}</div></div>
      <div><span class="subtle">Source Confidence</span><div class="mono-nums">${Number.isFinite(Number(trust?.data_confidence)) ? `${(Number(trust.data_confidence) * 100).toFixed(1)}%` : "n/a"}</div></div>
    </div>
    <div class="subtle">${freshnessLine}</div>
    <div class="ir-subhead">Filing Excerpts</div>
    ${bulletList(filingExcerpts, { empty: "No filing excerpts available." })}
    <div class="ir-subhead">Management Quotes</div>
    ${bulletList(managementQuotes, { empty: "No management quotes surfaced." })}
    <div class="ir-subhead">Calculation Breadcrumbs</div>
    ${bulletList(calcBreadcrumbs, { empty: "No calculation breadcrumbs available." })}
  `;

  return makeInstitutionalSection({
    id: "evidence_trace",
    eyebrow: "Traceability",
    title: "Evidence and Freshness",
    subtitle: "Filing excerpts, management quotes, and calculation breadcrumbs used in this report.",
    body,
  });
}

function buildFactsTakeHypothesesSection(rawData) {
  const trust = rawData?.report_trust || {};
  const facts = Array.isArray(trust?.verified_facts) ? trust.verified_facts : [];
  const analystTake = Array.isArray(trust?.analyst_take) ? trust.analyst_take : [];
  const hypotheses = Array.isArray(trust?.hypotheses) ? trust.hypotheses : [];

  const factsHtml = facts.length
    ? `<ul class="report-bullets">${facts
        .map((f) => `<li><strong>${escapeHtml(String(f.id || ""))}</strong>: ${escapeHtml(String(f.statement || ""))} <span class="muted">[${escapeHtml(String(f.source || "unknown"))}]</span></li>`)
        .join("")}</ul>`
    : `<ul class="report-bullets"><li class="muted">No verified facts provided.</li></ul>`;

  const takeHtml = analystTake.length
    ? `<ul class="report-bullets">${analystTake
        .map((b) => {
          const cites = Array.isArray(b.citation_ids) ? b.citation_ids : [];
          const citeText = cites.length ? ` [cites: ${cites.map((c) => escapeHtml(String(c))).join(", ")}]` : "";
          return `<li>${escapeHtml(String(b.text || ""))}<span class="muted">${citeText}</span></li>`;
        })
        .join("")}</ul>`
    : `<ul class="report-bullets"><li class="muted">No analyst take blocks provided.</li></ul>`;

  const hypoHtml = hypotheses.length
    ? `<ul class="report-bullets">${hypotheses
        .map((h) => `<li>${escapeHtml(String(h.text || ""))} <span class="muted">(${escapeHtml(String(h.status || "tentative"))})</span></li>`)
        .join("")}</ul>`
    : `<ul class="report-bullets"><li class="muted">No hypotheses currently open.</li></ul>`;

  const body = `
    <div class="ir-subhead">Verified Facts</div>
    ${factsHtml}
    <div class="ir-subhead">Analyst Take (claim-block citations)</div>
    ${takeHtml}
    <div class="ir-subhead">Hypotheses</div>
    ${hypoHtml}
  `;
  return makeInstitutionalSection({
    id: "facts_take_hypotheses",
    eyebrow: "Source of Truth",
    title: "Facts, Analyst Take, and Hypotheses",
    subtitle: "Facts-first ordering with claim-block citation references.",
    body,
  });
}

function buildAppendixSection(normalized, tab) {
  const blocks = buildAppendixBlocks(normalized);
  const block = blocks[tab] || blocks.summary;
  return makeInstitutionalSection({
    id: "appendix",
    eyebrow: "Appendix",
    title: "Appendix and Section Detail",
    subtitle: "Switch tabs above to view section-level data behind the institutional view.",
    body: block,
  });
}

function buildDisclaimerSection() {
  const body = `
    <p class="ir-paragraph">
      This report is generated automatically for informational research workflows.
      It is not investment advice and should not be relied upon as a sole basis for trading decisions.
      Verify all data points against primary sources before acting on any framing presented above.
    </p>
  `;
  return makeInstitutionalSection({
    id: "disclaimer",
    eyebrow: "Disclaimer",
    title: "Disclaimer",
    subtitle: "Use of this report.",
    body,
  });
}

export function renderReportTabs(data) {
  const tabs = document.getElementById("reportTabs");
  tabs.innerHTML = "";
  if (!data) return;
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Report appendix sections");
  const normalized = normalizeReportPayload(data, {
    portfolioRisk: state.lastPortfolioRiskData,
    portfolioSnapshot: state.lastPortfolioData,
  });
  const available = getAppendixSections(normalized);
  if (!available.includes(state.activeReportTab)) state.activeReportTab = "summary";
  available.forEach((key) => {
    const btn = document.createElement("button");
    const tabId = `report-tab-${key}`;
    const panelId = `report-panel-${key}`;
    const selected = state.activeReportTab === key;
    btn.className = `report-tab ${selected ? "active" : ""}`;
    btn.id = tabId;
    btn.type = "button";
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", selected ? "true" : "false");
    btn.setAttribute("aria-controls", panelId);
    btn.tabIndex = selected ? 0 : -1;
    btn.textContent = key === "summary" ? "Appendix: Summary" : `Appendix: ${key[0].toUpperCase()}${key.slice(1)}`;
    btn.addEventListener("click", () => {
      state.activeReportTab = key;
      renderReportTabs(data);
      renderReportVisual(data);
    });
    tabs.appendChild(btn);
  });
}

export function renderReportVisual(data) {
  const root = document.getElementById("reportVisual");
  if (!root) return;
  if (!data) {
    root.innerHTML = `<div class="report-empty">No report data.</div>`;
    return;
  }

  const normalized = normalizeReportPayload(data, {
    portfolioRisk: state.lastPortfolioRiskData,
    portfolioSnapshot: state.lastPortfolioData,
  });
  const tab = state.activeReportTab || "summary";

  root.setAttribute("role", "tabpanel");
  root.setAttribute("id", `report-panel-${tab}`);
  root.setAttribute("aria-labelledby", `report-tab-${tab}`);
  root.innerHTML = `
    <article class="ir-document">
      ${buildTrustHeaderSection(data)}
      ${buildFactsTakeHypothesesSection(data)}
      ${buildEvidenceTraceSection(data)}
      ${buildCoverHeader(data, normalized)}
      ${buildExecutiveSummarySection(data, normalized)}
      ${buildBusinessModelSection(data, normalized)}
      ${buildFundamentalsSection(data)}
      ${buildValuationTechnicalSection(data)}
      ${buildSecNarrativeSection(data)}
      ${buildPortfolioFitSection(normalized)}
      ${buildCatalystsRisksSection(normalized)}
      ${buildScenarioSection(normalized)}
      ${buildMonitoringSection(normalized)}
      ${buildAppendixSection(normalized, tab)}
      ${buildDisclaimerSection()}
    </article>
  `;
}

export function applyReportViewMode() {
  const raw = document.getElementById("reportOutput");
  const visual = document.getElementById("reportVisual");
  const btn = document.getElementById("toggleReportViewBtn");
  if (!raw || !visual || !btn) return;
  if (state.reportRawView) {
    raw.style.display = "block";
    visual.style.display = "none";
    btn.textContent = "Show Visual";
  } else {
    raw.style.display = "none";
    visual.style.display = "grid";
    btn.textContent = "Show Raw JSON";
  }
}

function setReportRunStatus(message, severity = "muted") {
  const el = document.getElementById("reportRunStatus");
  if (!el) return;
  el.textContent = message;
  el.classList.remove("muted", "warn", "good");
  if (severity === "warn") el.classList.add("warn");
  else if (severity === "good") el.classList.add("good");
  else el.classList.add("muted");
}

export async function runReport() {
  if (!smokeChecked) {
    smokeChecked = true;
    const smoke = runReportNormalizationSmokeChecks();
    if (!smoke.ok) {
      logEvent({ kind: "report", severity: "warn", message: "Normalization smoke checks reported non-fatal issues." });
    }
  }

  const ticker = document.getElementById("reportTickerInput").value.trim().toUpperCase();
  if (!ticker) return;
  const section = document.getElementById("reportSection").value.trim();
  const skipMirofish = document.getElementById("skipMirofish").checked;
  const skipEdgar = document.getElementById("skipEdgar").checked;
  const btn = document.getElementById("reportBtn");
  const output = document.getElementById("reportOutput");
  const visual = document.getElementById("reportVisual");

  btn.disabled = true;
  btn.textContent = "Running...";
  setReportRunStatus("Status: queued", "muted");
  output.textContent = "Generating report...";
  visual.innerHTML = `<div class="report-empty">Generating visual report...</div>`;
  updateActionCenter({ title: "Report Running", message: `Generating report for ${ticker}...`, severity: "info" });

  try {
    const qs = new URLSearchParams();
    if (section) qs.set("section", section);
    qs.set("skip_mirofish", String(skipMirofish));
    qs.set("skip_edgar", String(skipEdgar));
    setReportRunStatus("Status: fetching data", "muted");
    const out = await api.get(`/api/report/${ticker}?${qs.toString()}`, { timeoutMs: 300000 });
    if (!out.ok) {
      output.textContent = out.error || "Report failed.";
      visual.innerHTML = `<div class="report-empty">${safeText(out.error || "Report failed.")}</div>`;
      setReportRunStatus("Status: partial failure (fetching/scoring failed)", "warn");
      logEvent({ kind: "report", severity: "error", message: `Report ${ticker} failed: ${out.error}` });
      return;
    }

    state.lastReportData = out.data;
    state.reportRawView = false;
    applyReportViewMode();
    setReportRunStatus("Status: scoring", "muted");
    let portfolioRiskFailed = false;
    try {
      const portfolioRiskOut = await api.get("/api/portfolio/risk", { timeoutMs: 20000 });
      state.lastPortfolioRiskData = portfolioRiskOut.ok ? portfolioRiskOut.data : null;
      if (!portfolioRiskOut.ok) portfolioRiskFailed = true;
    } catch {
      state.lastPortfolioRiskData = null;
      portfolioRiskFailed = true;
    }

    state.activeReportTab = "summary";
    setReportRunStatus("Status: drafting", "muted");
    output.textContent = JSON.stringify(out.data, null, 2);
    renderReportTabs(out.data);
    renderReportVisual(out.data);
    if (portfolioRiskFailed) {
      setReportRunStatus("Status: complete with partial failure (portfolio risk unavailable)", "warn");
    } else {
      setReportRunStatus("Status: complete", "good");
    }
    logEvent({ kind: "report", severity: "info", message: `Report complete for ${ticker}${section ? ` (${section})` : ""}.` });
    updateActionCenter({ title: "Report Complete", message: `Full report ready for ${ticker}.`, severity: "success" });
  } finally {
    btn.disabled = false;
    btn.textContent = "Run Report";
  }
}

function setDossierMeta(message, severity = "muted") {
  const meta = document.getElementById("dossierMeta");
  if (!meta) return;
  meta.textContent = message;
  meta.classList.remove("muted", "warn", "good");
  if (severity === "warn") meta.classList.add("warn");
  else if (severity === "good") meta.classList.add("good");
  else meta.classList.add("muted");
}

function isMarkdownTableSeparator(line) {
  // Matches separator rows like "|---|---:|:---:|"
  if (!line.startsWith("|")) return false;
  const cells = splitMarkdownTableRow(line);
  if (!cells.length) return false;
  return cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function splitMarkdownTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|")) return [];
  const inner = trimmed.replace(/^\|/, "").replace(/\|$/, "");
  return inner.split("|").map((cell) => cell.trim());
}

function polishDossierMarkdownForPreview(markdown) {
  const phraseRewrites = [
    [
      "is evaluated through a blended institutional framework that integrates market structure, valuation underwriting, filing intelligence, and scenario-based risk control.",
      "is assessed across market structure, valuation, filing signals, and scenario risk.",
    ],
    [
      "The objective is not to defend a side, but to rank the probability distribution and identify whether reward-to-risk is improving or deteriorating.",
      "The objective is to rank probabilities and determine whether reward-to-risk is improving.",
    ],
  ];
  const lines = String(markdown || "").split(/\r?\n/);
  const out = [];
  for (let line of lines) {
    const trimmed = line.trim();
    const lower = trimmed.toLowerCase();
    if (
      trimmed.includes(" | ")
      && (
        lower.startsWith("prepared:")
        || lower.startsWith("analyst:")
        || lower.startsWith("coverage:")
        || lower.startsWith("region:")
        || lower.startsWith("document type:")
        || lower.startsWith("current price:")
        || lower.startsWith("recommendation:")
      )
    ) {
      trimmed.split("|").map((p) => p.trim()).filter(Boolean).forEach((part) => out.push(`- ${part}`));
      continue;
    }
    for (const [before, after] of phraseRewrites) {
      line = line.replace(before, after);
    }
    if (!trimmed.startsWith("|") && trimmed.length > 240) {
      const bits = trimmed.split(/(?<=[.!?])\s+/).filter(Boolean);
      let compact = bits.slice(0, 2).join(" ");
      if (!compact || compact.length > 240) compact = `${trimmed.slice(0, 239).trimEnd()}…`;
      out.push(compact);
      continue;
    }
    out.push(line);
  }
  return out.join("\n");
}

function alignmentFromSeparatorCell(cell) {
  const text = cell.trim();
  const left = text.startsWith(":");
  const right = text.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  return "left";
}

function applyInlineEmphasis(text) {
  // Escape first, then re-introduce inline emphasis using bold/italic markers.
  let out = escapeHtml(text);
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a class="ir-link" href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^\\])\*([^*]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/`([^`]+)`/g, '<code class="ir-inline-code">$1</code>');
  out = out.replace(/\[(\d{1,3})\]/g, '<sup class="ir-cite">[$1]</sup>');
  return out;
}

function renderMarkdownTable(rawRows) {
  if (!Array.isArray(rawRows) || rawRows.length === 0) return "";
  const rowCells = rawRows.map(splitMarkdownTableRow).filter((cells) => cells.length > 0);
  if (!rowCells.length) return "";

  // Detect header + separator pattern.
  let headers = [];
  let alignments = [];
  let bodyRows = rowCells;
  if (rowCells.length >= 2 && isMarkdownTableSeparator(rawRows[1])) {
    headers = rowCells[0];
    alignments = rowCells[1].map(alignmentFromSeparatorCell);
    bodyRows = rowCells.slice(2);
  } else {
    headers = rowCells[0];
    alignments = headers.map(() => "left");
    bodyRows = rowCells.slice(1);
  }

  const headerHtml = `<tr>${headers
    .map((h, i) => {
      const align = alignments[i] || "left";
      const alignClass = align === "right" ? " class=\"right\"" : (align === "center" ? " class=\"center\"" : "");
      return `<th${alignClass}>${applyInlineEmphasis(h)}</th>`;
    })
    .join("")}</tr>`;

  const bodyHtml = bodyRows
    .map((cells) => {
      const padded = cells.length < headers.length
        ? cells.concat(new Array(headers.length - cells.length).fill(""))
        : cells.slice(0, headers.length);
      return `<tr>${padded
        .map((cell, i) => {
          const align = alignments[i] || "left";
          const alignClass = align === "right" ? " class=\"right mono-nums\"" : (align === "center" ? " class=\"center\"" : "");
          return `<td${alignClass}>${applyInlineEmphasis(cell)}</td>`;
        })
        .join("")}</tr>`;
    })
    .join("");

  return `
    <div class="table-wrap report-table-wrap">
      <table class="ir-data-table report-scenario-table">
        <thead>${headerHtml}</thead>
        <tbody>${bodyHtml}</tbody>
      </table>
    </div>
  `;
}

function markdownToPreviewHtml(markdown) {
  const lines = polishDossierMarkdownForPreview(markdown).split(/\r?\n/);
  const out = [];
  let listOpen = false;
  let para = [];
  let inTable = false;
  let tableRows = [];

  const flushParagraph = () => {
    if (!para.length) return;
    const text = applyInlineEmphasis(para.join(" ").trim());
    if (text) out.push(`<p class="ir-paragraph">${text}</p>`);
    para = [];
  };

  const flushList = () => {
    if (!listOpen) return;
    out.push("</ul>");
    listOpen = false;
  };

  const flushTable = () => {
    if (!inTable) return;
    out.push(renderMarkdownTable(tableRows));
    inTable = false;
    tableRows = [];
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      flushList();
      flushTable();
      continue;
    }
    if (trimmed === "---") {
      flushParagraph();
      flushList();
      flushTable();
      out.push(`<hr class="ir-divider" />`);
      continue;
    }
    if (trimmed.startsWith("|")) {
      flushParagraph();
      flushList();
      inTable = true;
      tableRows.push(trimmed);
      continue;
    }
    flushTable();
    if (trimmed.startsWith("### ")) {
      flushParagraph();
      flushList();
      out.push(`<h5 class="ir-h5">${applyInlineEmphasis(trimmed.slice(4))}</h5>`);
      continue;
    }
    if (trimmed.startsWith("## ")) {
      flushParagraph();
      flushList();
      out.push(`<h4 class="ir-h4">${applyInlineEmphasis(trimmed.slice(3))}</h4>`);
      continue;
    }
    if (trimmed.startsWith("# ")) {
      flushParagraph();
      flushList();
      out.push(`<h3 class="ir-h3">${applyInlineEmphasis(trimmed.slice(2))}</h3>`);
      continue;
    }
    if (trimmed.startsWith("- ")) {
      flushParagraph();
      if (!listOpen) {
        out.push('<ul class="report-bullets">');
        listOpen = true;
      }
      out.push(`<li>${applyInlineEmphasis(trimmed.slice(2))}</li>`);
      continue;
    }
    para.push(trimmed);
  }

  flushParagraph();
  flushList();
  flushTable();
  return out.join("");
}

function setDossierPreview(data, markdownText = "") {
  const writeup = document.getElementById("dossierWriteup");
  const details = document.getElementById("dossierDetails");
  const out = document.getElementById("dossierOutput");
  if (!details || !out) return;
  if (writeup) {
    writeup.classList.remove("hidden");
    const html = markdownText
      ? markdownToPreviewHtml(markdownText)
      : `<div class="report-empty">Narrative preview unavailable. Use Download Markdown as fallback.</div>`;
    writeup.innerHTML = `<article class="ir-document ir-dossier-preview">${html}</article>`;
  }
  details.classList.remove("hidden");
  out.textContent = JSON.stringify(data, null, 2);
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

function resolveDossierTicker() {
  const reportTicker = document.getElementById("reportTickerInput")?.value?.trim()?.toUpperCase() || "";
  if (reportTicker) return reportTicker;
  // Allow SEC compare users to generate a dossier without retyping.
  const secCompareTicker = document.getElementById("secCompareTickerA")?.value?.trim()?.toUpperCase() || "";
  if (secCompareTicker) {
    const reportInput = document.getElementById("reportTickerInput");
    if (reportInput) reportInput.value = secCompareTicker;
    return secCompareTicker;
  }
  return "";
}

function handleDossierRuntimeUnavailable(responseLike) {
  if (!responseLike || responseLike.status !== 404) return false;
  const msg = "Dossier endpoint unavailable in this runtime";
  setDossierMeta(msg, "warn");
  updateActionCenter({
    title: "Dossier Unavailable",
    message: msg,
    severity: "warn",
  });
  return true;
}

export async function runResearchDossier() {
  const ticker = resolveDossierTicker();
  if (!ticker) {
    setDossierMeta("Enter a ticker in Full Report or SEC Compare first.", "warn");
    updateActionCenter({
      title: "Ticker Required",
      message: "Set a ticker in Full Report or SEC Compare before generating a dossier.",
      severity: "warn",
    });
    return;
  }
  const btn = document.getElementById("dossierBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Generating...";
  }
  setDossierMeta(`Generating dossier for ${ticker}...`);
  updateActionCenter({ title: "Dossier Running", message: `Building research dossier for ${ticker}...`, severity: "info" });
  try {
    const out = await api.getResearchDossier(ticker, { timeoutMs: 300000 });
    if (handleDossierRuntimeUnavailable(out)) return;
    if (!out.ok) {
      setDossierMeta(out.error || "Dossier generation failed.", "warn");
      logEvent({ kind: "report", severity: "error", message: `Dossier ${ticker} failed: ${out.error}` });
      return;
    }
    state.lastResearchDossier = out.data;
    let mdPreview = "";
    const mdOut = await api.downloadResearchDossier(ticker, "md", { timeoutMs: 300000 });
    if (mdOut?.ok && mdOut?.data?.blob) {
      try {
        mdPreview = await mdOut.data.blob.text();
      } catch {
        mdPreview = "";
      }
    }
    setDossierPreview(out.data, mdPreview);
    const fallbackCount = Array.isArray(out.data?.fallback_notes) ? out.data.fallback_notes.length : 0;
    setDossierMeta(
      fallbackCount
        ? `Dossier ready for ${ticker} (${fallbackCount} fallback note${fallbackCount === 1 ? "" : "s"})`
        : `Dossier ready for ${ticker}`,
      fallbackCount ? "warn" : "good",
    );
    updateActionCenter({
      title: "Dossier Ready",
      message: fallbackCount
        ? `${ticker} dossier generated with ${fallbackCount} fallback note${fallbackCount === 1 ? "" : "s"}.`
        : `${ticker} dossier generated and ready for download.`,
      severity: fallbackCount ? "warn" : "success",
    });
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Generate Dossier";
    }
  }
}

export async function downloadResearchDossier(format = "json") {
  const ticker = resolveDossierTicker();
  if (!ticker) {
    setDossierMeta("Enter a ticker in Full Report or SEC Compare first.", "warn");
    updateActionCenter({
      title: "Ticker Required",
      message: "Set a ticker in Full Report or SEC Compare before downloading dossier exports.",
      severity: "warn",
    });
    return;
  }
  const buttonMap = {
    json: "dossierDownloadJsonBtn",
    md: "dossierDownloadMdBtn",
    pdf: "dossierDownloadPdfBtn",
  };
  const btn = document.getElementById(buttonMap[format] || "");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Downloading...";
  }
  setDossierMeta(`Downloading ${format.toUpperCase()} export...`);
  try {
    const out = await api.downloadResearchDossier(ticker, format, { timeoutMs: 300000 });
    if (handleDossierRuntimeUnavailable(out)) return;
    if (!out.ok) {
      setDossierMeta(out.error || "Download failed.", "warn");
      logEvent({ kind: "report", severity: "error", message: `Dossier ${format} download failed: ${out.error}` });
      return;
    }
    triggerBlobDownload(out.data.blob, out.data.filename);
    setDossierMeta(`Downloaded ${out.data.filename}`, "good");
    updateActionCenter({
      title: "Download Complete",
      message: `${out.data.filename} saved from the latest dossier export.`,
      severity: "success",
    });
  } finally {
    if (btn) {
      btn.disabled = false;
      if (format === "json") btn.textContent = "Download JSON";
      else if (format === "md") btn.textContent = "Download Markdown";
      else btn.textContent = "Download PDF";
    }
  }
}

export async function downloadResearchFundamentalWorkbook() {
  const ticker = resolveDossierTicker();
  if (!ticker) {
    setDossierMeta("Enter a ticker in Full Report or SEC Compare first.", "warn");
    updateActionCenter({
      title: "Ticker Required",
      message: "Set a ticker in Full Report or SEC Compare before downloading the model workbook.",
      severity: "warn",
    });
    return;
  }
  const btn = document.getElementById("dossierDownloadModelWorkbookBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Downloading...";
  }
  setDossierMeta("Downloading fundamental model workbook...", "muted");
  try {
    const out = await api.downloadResearchFundamentalWorkbook(ticker, { timeoutMs: 300000 });
    if (handleDossierRuntimeUnavailable(out)) return;
    if (!out.ok) {
      setDossierMeta(out.error || "Workbook download failed.", "warn");
      logEvent({ kind: "report", severity: "error", message: `Workbook download failed: ${out.error}` });
      return;
    }
    triggerBlobDownload(out.data.blob, out.data.filename);
    setDossierMeta(`Downloaded ${out.data.filename}`, "good");
    updateActionCenter({
      title: "Workbook Ready",
      message: `${out.data.filename} saved.`,
      severity: "success",
    });
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Download Fundamental Model Workbook (.xlsx)";
    }
  }
}
