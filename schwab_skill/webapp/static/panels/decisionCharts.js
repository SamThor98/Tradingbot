/**
 * Signal-edge evidence visuals for the decision dashboard (Phase 2).
 * Pure render helpers — DOM ids owned by index.html / decisionDashboard.js.
 */

import { decorateGlossary } from "../modules/glossary.js";
import { safeText, safeNum, escapeHtml, formatPercentPoints } from "../modules/format.js";

const DEFAULT_PF_MEAN_GATE = 1.2;
const DEFAULT_WORST_ERA_GATE = 1.0;
const HEADLINE_SCENARIO_KEY = "exit_grace_breakout_buffer_0.010";
const BOOK_WIDTH_SOURCE = "validation_artifacts/cap37_unsorted_vs_live.json";

/** Name the profit-factor story the Mean PF tile is allowed to show. */
export function stackPfProvenance(signalEdge = {}) {
  const stack = signalEdge.signal_stack_counterfactual || {};
  const runId = safeText(signalEdge.run_id || "control_legacy_aug");
  const scenarioKey = safeText(stack.headline_scenario_key || HEADLINE_SCENARIO_KEY);
  const artifact = safeText(stack.artifact || `validation_artifacts/signal_stack_counterfactual_${runId}.json`);
  const generated = safeText(stack.generated_at || "");
  const generatedDay = generated ? generated.slice(0, 10) : "";
  const stackMean = safeNum(stack.pf_mean, NaN);
  if (Number.isFinite(stackMean)) {
    const when = generatedDay ? ` · ${generatedDay}` : " · artifact date missing";
    return {
      pfMean: stackMean,
      fallback: false,
      label: `${scenarioKey} · ${runId}${when}`,
      artifact,
    };
  }
  const hold = safeNum(signalEdge.hold_21_40d_pf, NaN);
  if (Number.isFinite(hold)) {
    return {
      pfMean: hold,
      fallback: true,
      label: `fallback: hold_21_40d_pf · early_stopout_cohorts_${runId}.json`,
      artifact: `validation_artifacts/early_stopout_cohorts_${runId}.json`,
    };
  }
  return {
    pfMean: NaN,
    fallback: false,
    label: `${scenarioKey} · ${runId} · scenario PF missing`,
    artifact,
  };
}

function releaseGateSub(readiness = {}) {
  const checks = Array.isArray(readiness.checks) ? readiness.checks : [];
  const ablation = checks.find((row) => row && row.name === "ablation");
  if (ablation && ablation.passed !== null && ablation.passed !== undefined) {
    return "validation + SLO + ablation";
  }
  return "validation + SLO";
}

function gateClass(passed) {
  if (passed === true) return "good";
  if (passed === false) return "bad";
  return "neutral";
}

function buildGateTileStates(signalEdge = {}, readiness = {}) {
  const stack = signalEdge.signal_stack_counterfactual || {};
  const gates = stack.promotion_gates || {};
  const pfMeanGate = safeNum(gates.pf_mean_min, DEFAULT_PF_MEAN_GATE);
  const worstEraGate = safeNum(gates.worst_era_pf_min, DEFAULT_WORST_ERA_GATE);
  const pfMean = stackPfProvenance(signalEdge).pfMean;
  const worstEra = safeNum(stack.worst_era_pf, NaN);
  const earlyStop = safeNum(signalEdge.early_stopout_pct, NaN);
  const retention = safeNum(stack.retention_pct, NaN);
  const pfMeanPass = Number.isFinite(pfMean) ? pfMean >= pfMeanGate : null;
  const worstEraPass = Number.isFinite(worstEra) ? worstEra >= worstEraGate : null;
  const promotionPass = readiness.release_gate_ready === true;
  const stackPass = stack.passes_promotion_gates;
  return [
    pfMeanPass ?? stackPass,
    worstEraPass ?? stackPass,
    Number.isFinite(earlyStop) ? earlyStop < 35 : null,
    Number.isFinite(retention) ? retention >= 70 : null,
    promotionPass,
  ];
}

/** One-line gate board summary for W2b signal-edge header. */
export function summarizeDecisionGates(signalEdge = {}, readiness = {}, opts = {}) {
  if (opts.state === "loading") {
    return { state: "loading", text: "Loading PF gates and promotion thresholds…" };
  }
  if (opts.state === "error") {
    return { state: "error", text: "Signal-edge gate evidence unavailable — retry refresh." };
  }
  if (opts.state === "empty") {
    return {
      state: "empty",
      text: "Run signal-stack counterfactual or validation to populate PF gates.",
    };
  }
  const states = buildGateTileStates(signalEdge, readiness);
  const pass = states.filter((s) => s === true).length;
  const fail = states.filter((s) => s === false).length;
  const unknown = states.filter((s) => s === null).length;
  const stack = signalEdge.signal_stack_counterfactual || {};
  const provenance = stackPfProvenance(signalEdge);
  const pfMean = provenance.pfMean;
  const worstEra = safeNum(stack.worst_era_pf, NaN);
  const pfLine = Number.isFinite(pfMean)
    ? `PF ${pfMean.toFixed(2)}${Number.isFinite(worstEra) ? ` / worst ${worstEra.toFixed(2)}` : ""} · ${provenance.label}`
    : `PF evidence pending · ${provenance.label}`;
  if (fail > 0) {
    return {
      state: "partial",
      text: `${pass} of 5 gates pass · ${fail} below threshold · ${pfLine}`,
    };
  }
  if (unknown > 0) {
    return {
      state: "partial",
      text: `${pass} gates pass · ${unknown} awaiting evidence · ${pfLine}`,
    };
  }
  return { state: "success", text: `All 5 gates pass · ${pfLine}` };
}

function fmtPf(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
}

function fmtPct(value, digits = 1) {
  // percent points from analyze_early_stopout_cohorts / signal-stack retention_pct
  return formatPercentPoints(value, digits);
}

function pfScaleMax(values, gates) {
  const nums = [...values, ...gates].map((n) => Number(n)).filter((n) => Number.isFinite(n));
  const dataMax = nums.length ? Math.max(...nums) : 1.5;
  return Math.max(1.5, dataMax * 1.1, DEFAULT_PF_MEAN_GATE * 1.1);
}

function pfWidthPct(pf, maxPf) {
  if (!Number.isFinite(pf) || !Number.isFinite(maxPf) || maxPf <= 0) return null;
  return Math.min(100, Math.max(0, (pf / maxPf) * 100));
}

function pfGateLineHtml(gate, maxPf, title) {
  if (!Number.isFinite(gate) || !Number.isFinite(maxPf) || maxPf <= 0) return "";
  const left = Math.min(100, Math.max(0, (gate / maxPf) * 100));
  return `<div class="decision-pf-gate-line" style="left:${left}%" title="${escapeHtml(title)}"></div>`;
}

function placeholderStateClass(state) {
  if (state === "error") return "bad";
  if (state === "loading") return "warn";
  return "neutral";
}

function renderGatePlaceholders(container, state = "empty", message = "") {
  const stateClass = placeholderStateClass(state);
  const values = state === "loading" ? ["…", "…", "…", "…", "…"] : ["—", "—", "—", "—", "—"];
  const labels = ["Mean PF", "Worst-era PF", "Early stops", "Retention", "Release gate"];
  const subs = state === "error"
    ? ["unavailable", "unavailable", "unavailable", "unavailable", "retry"]
    : state === "loading"
      ? ["loading", "loading", "loading", "loading", "loading"]
      : ["run counterfactual", "run validation", "no cohort", "no experiment", "not recorded"];
  container.innerHTML = labels
    .map(
      (label, idx) => `
    <div class="decision-gate-tile decision-gate-tile--${stateClass}" data-gate-state="${stateClass}">
      <span class="decision-gate-label">${escapeHtml(label)}</span>
      <strong class="decision-gate-value mono-nums">${escapeHtml(values[idx])}</strong>
      <small class="decision-gate-sub muted">${escapeHtml(subs[idx])}</small>
    </div>
  `,
    )
    .join("");
  if (message) {
    container.insertAdjacentHTML(
      "beforeend",
      `<p class="decision-gate-message muted">${escapeHtml(message)}</p>`,
    );
  }
}

/**
 * @param {HTMLElement|null} container
 * @param {object} signalEdge
 * @param {object} readiness
 */
export function renderDecisionGateTiles(container, signalEdge = {}, readiness = {}, opts = {}) {
  if (!container) return;
  if (opts.state === "loading" || opts.state === "error" || opts.state === "empty") {
    renderGatePlaceholders(container, opts.state, opts.message || "");
    return;
  }
  const stack = signalEdge.signal_stack_counterfactual || {};
  const gates = stack.promotion_gates || {};
  const pfMeanGate = safeNum(gates.pf_mean_min, DEFAULT_PF_MEAN_GATE);
  const worstEraGate = safeNum(gates.worst_era_pf_min, DEFAULT_WORST_ERA_GATE);
  const pfMean = stackPfProvenance(signalEdge).pfMean;
  const worstEra = safeNum(stack.worst_era_pf, NaN);
  const earlyStop = safeNum(signalEdge.early_stopout_pct, NaN);
  const retention = safeNum(stack.retention_pct, NaN);
  const promotionPass = readiness.release_gate_ready === true;

  const tileStates = buildGateTileStates(signalEdge, readiness);
  const tiles = [
    {
      label: "Mean PF",
      value: fmtPf(pfMean),
      sub: `${stackPfProvenance(signalEdge).label} · gate ≥ ${pfMeanGate.toFixed(2)}`,
      state: tileStates[0],
    },
    {
      label: "Worst-era PF",
      value: fmtPf(worstEra),
      sub: `gate ≥ ${worstEraGate.toFixed(2)} · ${safeText((signalEdge.signal_stack_counterfactual || {}).headline_scenario_key || HEADLINE_SCENARIO_KEY)}`,
      state: tileStates[1],
    },
    {
      label: "Early stops",
      value: fmtPct(earlyStop),
      sub: "21–40d cohort",
      state: tileStates[2],
    },
    {
      label: "Stack retention",
      value: fmtPct(retention),
      sub: "experiment keep rate",
      state: tileStates[3],
    },
    {
      label: "Release gate",
      value: promotionPass ? "Ready" : "Blocked",
      sub: releaseGateSub(readiness),
      state: tileStates[4],
    },
  ];

  container.innerHTML = tiles
    .map(
      (tile) => `
    <div class="decision-gate-tile decision-gate-tile--${gateClass(tile.state)}" data-gate-state="${gateClass(tile.state)}">
      <span class="decision-gate-label">${escapeHtml(tile.label)}</span>
      <strong class="decision-gate-value mono-nums">${escapeHtml(tile.value)}</strong>
      <small class="decision-gate-sub muted">${escapeHtml(tile.sub)}</small>
    </div>
  `,
    )
    .join("");
}

/**
 * @param {HTMLElement|null} container
 * @param {object} signalEdge
 */
export function renderDecisionPfChart(container, signalEdge = {}) {
  if (!container) return;
  const opts = arguments[2] || {};
  if (opts.state === "loading" || opts.state === "error" || opts.state === "empty") {
    const label =
      opts.state === "error"
        ? opts.message || "Decision PF evidence is unavailable."
        : opts.state === "loading"
          ? "Loading PF vs the 1.20 mean / 1.00 worst-era gates…"
          : "Run signal-stack counterfactual to populate multi-scenario PF evidence.";
    container.innerHTML = `
      <div class="decision-pf-chart decision-pf-chart--placeholder" data-state="${escapeHtml(opts.state)}">
        <div class="decision-pf-row">
          <span class="decision-pf-label">Mean PF</span>
          <div class="decision-pf-bar-track">${
            opts.state === "loading"
              ? `<div class="decision-pf-bar-fill ol-skeleton" style="width:44%"></div>`
              : ""
          }</div>
          <span class="decision-pf-value mono-nums">${opts.state === "loading" ? "…" : "—"}</span>
        </div>
        <div class="decision-pf-row">
          <span class="decision-pf-label">Worst era</span>
          <div class="decision-pf-bar-track">${
            opts.state === "loading"
              ? `<div class="decision-pf-bar-fill decision-pf-bar-fill--worst ol-skeleton" style="width:32%"></div>`
              : ""
          }</div>
          <span class="decision-pf-value mono-nums">${opts.state === "loading" ? "…" : "—"}</span>
        </div>
      </div>
      <p class="muted decision-pf-gates-note">${escapeHtml(label)}</p>
    `;
    return;
  }
  const stack = signalEdge.signal_stack_counterfactual || {};
  const scenarios = Array.isArray(stack.scenarios) ? stack.scenarios : [];
  const gates = stack.promotion_gates || {};
  const pfMeanGate = safeNum(gates.pf_mean_min, DEFAULT_PF_MEAN_GATE);
  const worstEraGate = safeNum(gates.worst_era_pf_min, DEFAULT_WORST_ERA_GATE);

  const provenance = stackPfProvenance(signalEdge);
  if (!scenarios.length) {
    const stackMean = safeNum(stack.pf_mean, NaN);
    const pfMean = Number.isFinite(stackMean) ? stackMean : (provenance.fallback ? provenance.pfMean : NaN);
    const worstEra = safeNum(stack.worst_era_pf, NaN);
    const meanLabel = provenance.fallback ? "hold_21_40d_pf fallback" : "Stack PF mean";
    if (!Number.isFinite(pfMean) && !Number.isFinite(worstEra)) {
      container.innerHTML = `<p class="muted">${escapeHtml(provenance.label)}. Run signal-stack counterfactual to populate multi-scenario PF evidence.</p>`;
      return;
    }
    const maxPf = pfScaleMax([pfMean, worstEra], [pfMeanGate, worstEraGate]);
    const meanW = pfWidthPct(pfMean, maxPf);
    const worstW = pfWidthPct(worstEra, maxPf);
    container.innerHTML = `
      <div class="decision-pf-chart">
        <p class="decision-pf-axis muted">Scale 0–${maxPf.toFixed(2)} PF · gates at ${pfMeanGate.toFixed(2)} / ${worstEraGate.toFixed(2)} · ${escapeHtml(provenance.label)}</p>
        <div class="decision-pf-row">
          <span class="decision-pf-label">${escapeHtml(meanLabel)}</span>
          <div class="decision-pf-bar-track">
            ${meanW == null ? "" : `<div class="decision-pf-bar-fill" style="width:${meanW}%"></div>`}
            ${pfGateLineHtml(pfMeanGate, maxPf, `PF mean gate ${pfMeanGate.toFixed(2)}`)}
            ${pfGateLineHtml(worstEraGate, maxPf, `Worst-era gate ${worstEraGate.toFixed(2)}`)}
          </div>
          <span class="decision-pf-value mono-nums">${fmtPf(pfMean)}</span>
        </div>
        <div class="decision-pf-row">
          <span class="decision-pf-label">Worst-era PF</span>
          <div class="decision-pf-bar-track">
            ${worstW == null ? "" : `<div class="decision-pf-bar-fill decision-pf-bar-fill--worst" style="width:${worstW}%"></div>`}
            ${pfGateLineHtml(pfMeanGate, maxPf, `PF mean gate ${pfMeanGate.toFixed(2)}`)}
            ${pfGateLineHtml(worstEraGate, maxPf, `Worst-era gate ${worstEraGate.toFixed(2)}`)}
          </div>
          <span class="decision-pf-value mono-nums">${fmtPf(worstEra)}</span>
        </div>
      </div>
      <p class="muted decision-pf-gates-note">${escapeHtml(provenance.label)}. Promotion gates: mean PF ≥ ${pfMeanGate.toFixed(2)}, worst-era PF ≥ ${worstEraGate.toFixed(2)}. Not phase-1 control_legacy. Not bare_pts52w_cap37.</p>
    `;
    return;
  }

  const maxPf = pfScaleMax(
    [
      ...scenarios.map((s) => safeNum(s.pf_mean, NaN)),
      ...scenarios.map((s) => safeNum(s.worst_era_pf, NaN)),
    ],
    [pfMeanGate, worstEraGate],
  );

  const rows = scenarios
    .map((row) => {
      const label = safeText(row.label || row.key || "scenario").replace(/_/g, " ");
      const pf = safeNum(row.pf_mean, NaN);
      const worst = safeNum(row.worst_era_pf, NaN);
      const pass = row.passes_promotion_gates === true;
      const pfWidth = pfWidthPct(pf, maxPf);
      const worstWidth = pfWidthPct(worst, maxPf);
      return `
        <div class="decision-pf-scenario ${pass ? "decision-pf-scenario--pass" : "decision-pf-scenario--fail"}">
          <div class="decision-pf-scenario-head">
            <strong>${escapeHtml(label)}</strong>
            <span class="pill ${pass ? "good" : "bad"}">${pass ? "PASS" : "FAIL"}</span>
          </div>
          <div class="decision-pf-row">
            <span class="decision-pf-label">Mean PF</span>
            <div class="decision-pf-bar-track">
              ${pfWidth == null ? "" : `<div class="decision-pf-bar-fill" style="width:${pfWidth}%"></div>`}
              ${pfGateLineHtml(pfMeanGate, maxPf, `PF mean gate ${pfMeanGate.toFixed(2)}`)}
            </div>
            <span class="decision-pf-value mono-nums">${fmtPf(pf)}</span>
          </div>
          <div class="decision-pf-row">
            <span class="decision-pf-label">Worst era</span>
            <div class="decision-pf-bar-track">
              ${worstWidth == null ? "" : `<div class="decision-pf-bar-fill decision-pf-bar-fill--worst" style="width:${worstWidth}%"></div>`}
              ${pfGateLineHtml(worstEraGate, maxPf, `Worst-era gate ${worstEraGate.toFixed(2)}`)}
            </div>
            <span class="decision-pf-value mono-nums">${fmtPf(worst)}</span>
          </div>
        </div>
      `;
    })
    .join("");

  const rec = safeText(stack.recommendation || "").replace(/_/g, " ");
  const reason = safeText(stack.reason || "");
  container.innerHTML = `
    <div class="decision-pf-chart decision-pf-chart--scenarios">
      <p class="decision-pf-axis muted">Scale 0–${maxPf.toFixed(2)} PF · gates at ${pfMeanGate.toFixed(2)} / ${worstEraGate.toFixed(2)} · ${escapeHtml(stackPfProvenance(signalEdge).label)}</p>
      ${rows}
    </div>
    <p class="muted decision-pf-gates-note">
      Gates: mean PF ≥ ${pfMeanGate.toFixed(2)}, worst-era PF ≥ ${worstEraGate.toFixed(2)}
      ${rec ? ` · Recommendation: ${escapeHtml(rec)}` : ""}
      ${reason ? ` — ${escapeHtml(reason.slice(0, 120))}${reason.length > 120 ? "…" : ""}` : ""}
    </p>
  `;
}

function bookWidthCopy(book = {}) {
  const verdict = safeText(book.verdict || "");
  if (!book.present) {
    return {
      state: "empty",
      text: "No book-width shadow yet. Run the cap-37 compare to see top 5, top 10, and the full p76 book.",
    };
  }
  if (verdict === "top10_ready_not_applied") {
    return {
      state: "partial",
      text: "Top 10 clears the PF gates and its range sits above the live top 5. The live list stays at five names until you sign that change and cut dollars per name by the same ratio.",
    };
  }
  if (verdict === "p76_all_ready_not_applied") {
    return {
      state: "partial",
      text: "The full p76 book clears the PF gates and its range sits above the live top 5. Top 10 did not separate. The live list stays at five names until you sign a wider book and cut dollars per name by the same ratio.",
    };
  }
  if (verdict === "wider_ahead_intervals_overlap") {
    return {
      state: "partial",
      text: "A wider p76 book looks better on the point estimate, but the ranges still overlap the live top 5. Keep five names.",
    };
  }
  if (verdict === "keep_live_top5" && book.capped_rank_verdict === "top5_not_the_lever") {
    const removed = Number(book.names_removed_by_top5);
    const removed3 = Number(book.names_removed_by_top3);
    const top5 = Number.isFinite(removed)
      ? `top 5 removes ${Math.round(removed)} names`
      : "top 5 barely changes membership";
    const top3 = Number.isFinite(removed3) ? ` Top 3 removes ${Math.round(removed3)} names.` : "";
    return {
      state: "partial",
      text: `Widening the uncapped p76 list does not clear the gates. After the 52-week cap, ${top5} from the capped p76 book and still misses a 1.20 mean.${top3} Keep the live list. Those capped rows are the raw book, separate from the July buffer-and-exit stack.`,
    };
  }
  if (verdict === "keep_live_top5") {
    const live = (book.arms || []).find((arm) => arm.role === "live");
    const reference = (book.arms || []).find((arm) => arm.role === "reference");
    const liveFails = live && live.clears_floors === false;
    const refClears = reference && reference.clears_floors === true;
    if (liveFails && refClears) {
      return {
        state: "partial",
        text: "Adding names inside the p76 trim does not clear the gates. The cap-only book, which drops that rank trim, does. That change stays a shadow. The live daily list stays at five.",
      };
    }
    if (liveFails) {
      return {
        state: "partial",
        text: "The wider p76 books do not beat the live top 5, and the live top 5 is still under the worst-era gate. Keep five names.",
      };
    }
    return {
      state: "success",
      text: "The wider p76 books do not beat the live top 5 on both gates. Keep five names.",
    };
  }
  return {
    state: "partial",
    text: book.note || "Book-width shadow is missing a capacity verdict. Re-run the compare.",
  };
}

function bookWidthPlaceholder(container, state, message) {
  const label =
    state === "error"
      ? message || "Book-width evidence is unavailable."
      : state === "loading"
        ? "Loading the live top 5 against the wider p76 books…"
        : "No book-width shadow yet. Run the cap-37 compare to see top 5, top 10, and the full p76 book.";
  container.dataset.state = state;
  container.innerHTML = `
    <h4 class="decision-board-title">Book width</h4>
    <p class="decision-signal-edge-summary" data-state="${escapeHtml(state)}">${decorateGlossary(escapeHtml(label))}</p>
    <div class="decision-pf-chart decision-pf-chart--placeholder" data-state="${escapeHtml(state)}">
      <div class="decision-pf-row">
        <span class="decision-pf-label">Mean PF</span>
        <div class="decision-pf-bar-track">${
          state === "loading" ? `<div class="decision-pf-bar-fill ol-skeleton" style="width:40%"></div>` : ""
        }</div>
        <span class="decision-pf-value mono-nums">${state === "loading" ? "…" : "—"}</span>
      </div>
    </div>
  `;
}

/**
 * Live top 5 versus wider p76 books. Shadow only: apply_live stays false.
 * @param {HTMLElement|null} container
 * @param {object} bookWidth
 */
export function renderDecisionBookWidth(container, bookWidth = {}, opts = {}) {
  if (!container) return;
  if (opts.state === "loading" || opts.state === "error") {
    bookWidthPlaceholder(container, opts.state, opts.message || "");
    return;
  }
  const book = bookWidth && typeof bookWidth === "object" ? bookWidth : {};
  if (!book.present || !Array.isArray(book.arms) || !book.arms.length) {
    bookWidthPlaceholder(container, "empty", "");
    return;
  }
  const summary = bookWidthCopy(book);
  const floors = book.floors || {};
  const pfMeanGate = safeNum(floors.pf_mean, DEFAULT_PF_MEAN_GATE);
  const worstEraGate = safeNum(floors.worst_era_pf, DEFAULT_WORST_ERA_GATE);
  const maxPf = pfScaleMax(
    book.arms.flatMap((arm) => [safeNum(arm.pf_mean, NaN), safeNum(arm.worst_era_pf, NaN)]),
    [pfMeanGate, worstEraGate],
  );
  const recommended = safeText(book.recommended_arm || "");
  const rows = book.arms
    .map((arm) => {
      const label = safeText(arm.label || arm.key || "book");
      const role = safeText(arm.role || "shadow");
      const pf = safeNum(arm.pf_mean, NaN);
      const worst = safeNum(arm.worst_era_pf, NaN);
      const pass = arm.clears_floors === true;
      const fail = arm.clears_floors === false;
      const pfWidth = pfWidthPct(pf, maxPf);
      const worstWidth = pfWidthPct(worst, maxPf);
      const n = safeNum(arm.n, NaN);
      const lo = safeNum(arm.pf_lo, NaN);
      const hi = safeNum(arm.pf_hi, NaN);
      const range =
        Number.isFinite(lo) && Number.isFinite(hi) ? `range ${lo.toFixed(2)}–${hi.toFixed(2)}` : "";
      const count = Number.isFinite(n) ? `${Math.round(n).toLocaleString()} trades` : "";
      const meta = [count, range].filter(Boolean).join(" · ");
      const isLive = role === "live";
      const isCandidate = !isLive && recommended !== "" && recommended === safeText(arm.key || "");
      const roleClass = isLive ? "decision-book-arm--live" : "";
      const candidate = isCandidate ? "decision-book-arm--candidate" : "";
      const passClass = pass ? "decision-pf-scenario--pass" : fail ? "decision-pf-scenario--fail" : "";
      const pill = isLive ? "LIVE" : isCandidate ? "CANDIDATE" : role === "reference" ? "REFERENCE" : role === "capped" ? "CAPPED" : "SHADOW";
      const gatePill = pass ? "PASS" : fail ? "FAIL" : "—";
      const gateClassName = pass ? "good" : fail ? "bad" : "neutral";
      return `
        <div class="decision-pf-scenario decision-book-arm ${roleClass} ${candidate} ${passClass}">
          <div class="decision-pf-scenario-head">
            <strong>${escapeHtml(label)}</strong>
            <span class="decision-book-pills">
              <span class="pill ${isLive ? "warn" : isCandidate ? "good" : "neutral"}">${escapeHtml(pill)}</span>
              <span class="pill ${gateClassName}">${escapeHtml(gatePill)}</span>
            </span>
          </div>
          <div class="decision-pf-row">
            <span class="decision-pf-label">Mean PF</span>
            <div class="decision-pf-bar-track">
              ${pfWidth == null ? "" : `<div class="decision-pf-bar-fill" style="width:${pfWidth}%"></div>`}
              ${pfGateLineHtml(pfMeanGate, maxPf, `PF mean gate ${pfMeanGate.toFixed(2)}`)}
            </div>
            <span class="decision-pf-value mono-nums">${fmtPf(pf)}</span>
          </div>
          <div class="decision-pf-row">
            <span class="decision-pf-label">Worst era</span>
            <div class="decision-pf-bar-track">
              ${worstWidth == null ? "" : `<div class="decision-pf-bar-fill decision-pf-bar-fill--worst" style="width:${worstWidth}%"></div>`}
              ${pfGateLineHtml(worstEraGate, maxPf, `Worst-era gate ${worstEraGate.toFixed(2)}`)}
            </div>
            <span class="decision-pf-value mono-nums">${fmtPf(worst)}</span>
          </div>
          ${meta ? `<p class="decision-book-meta muted">${escapeHtml(meta)}</p>` : ""}
        </div>
      `;
    })
    .join("");
  const generated = safeText(book.generated_at || "").slice(0, 10);
  container.dataset.state = summary.state;
  container.innerHTML = `
    <h4 class="decision-board-title">Book width</h4>
    <p class="decision-signal-edge-summary" data-state="${escapeHtml(summary.state)}">${decorateGlossary(escapeHtml(summary.text))}</p>
    <div class="decision-pf-chart decision-pf-chart--scenarios decision-book-chart">
      <p class="decision-pf-axis muted">Scale 0–${maxPf.toFixed(2)} PF · gates at ${pfMeanGate.toFixed(2)} / ${worstEraGate.toFixed(2)} · live list unchanged</p>
      ${rows}
    </div>
    <p class="muted decision-pf-gates-note">${
      book.capped_rank_verdict
        ? "Live list stays at five. Capped rows apply the rank trim after the 52-week cap on the raw book. They do not replace the July buffer-and-exit stack."
        : "Same p76 rank trim as live. Only the daily name count changes."
    } Source: ${escapeHtml(safeText(book.source || BOOK_WIDTH_SOURCE))}. Separate from the signal-stack scenario. ${generated ? `Shadow dated ${escapeHtml(generated)}.` : "Artifact date missing."}</p>
  `;
}

/**
 * Compact strip for the collapsed decision-dashboard disclosure summary.
 * @param {HTMLElement|null} container
 * @param {object} payload
 */
export function renderDecisionSummaryStrip(container, payload = {}) {
  if (!container) return;
  const opts = arguments[2] || {};
  if (opts.state === "loading" || opts.state === "error" || opts.state === "empty") {
    const cls = opts.state === "error" ? "bad" : opts.state === "loading" ? "warn" : "neutral";
    const label = opts.state.charAt(0).toUpperCase() + opts.state.slice(1);
    container.innerHTML = `
      <span class="decision-summary-pill ${cls}">${escapeHtml(label)}</span>
      <span class="decision-summary-pill neutral">${escapeHtml(opts.message || opts.detail || "Decision evidence pending")}</span>
    `;
    return;
  }
  const signalEdge = payload.signal_edge || {};
  const readiness = payload.promotion_readiness || {};
  const reliability = payload.reliability || {};
  const stack = signalEdge.signal_stack_counterfactual || {};
  const edgeState = safeText(signalEdge.state || "unknown").replace(/_/g, " ");
  const provenance = stackPfProvenance(signalEdge);
  const pfMean = fmtPf(provenance.pfMean);
  const worstEra = fmtPf(stack.worst_era_pf);
  const release = readiness.release_gate_ready === true ? "ready" : "blocked";
  const rel = reliability.validation_passed === true && reliability.slo_gate_passed === true ? "healthy" : "at risk";
  container.innerHTML = `
    <span class="decision-summary-pill ${release === "ready" ? "good" : "bad"}">Release ${release}</span>
    <span class="decision-summary-pill ${rel === "healthy" ? "good" : "warn"}">${rel}</span>
    <span class="decision-summary-pill neutral">PF ${pfMean} / worst ${worstEra}</span>
    <span class="decision-summary-pill neutral">${escapeHtml(provenance.label)}</span>
    <span class="decision-summary-pill neutral">${escapeHtml(edgeState)}</span>
  `;
}
