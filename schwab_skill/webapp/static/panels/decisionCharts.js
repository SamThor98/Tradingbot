/**
 * Signal-edge evidence visuals for the decision dashboard (Phase 2).
 * Pure render helpers — DOM ids owned by index.html / decisionDashboard.js.
 */

import { safeText, safeNum, escapeHtml } from "../modules/format.js";

const DEFAULT_PF_MEAN_GATE = 1.2;
const DEFAULT_WORST_ERA_GATE = 1.0;

function gateClass(passed) {
  if (passed === true) return "good";
  if (passed === false) return "bad";
  return "neutral";
}

function fmtPf(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
}

function fmtPct(value, digits = 1) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(digits)}%` : "—";
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
  const pfMean = safeNum(stack.pf_mean ?? signalEdge.hold_21_40d_pf, NaN);
  const worstEra = safeNum(stack.worst_era_pf, NaN);
  const earlyStop = safeNum(signalEdge.early_stopout_pct, NaN);
  const retention = safeNum(stack.retention_pct, NaN);

  const pfMeanPass = Number.isFinite(pfMean) ? pfMean >= pfMeanGate : null;
  const worstEraPass = Number.isFinite(worstEra) ? worstEra >= worstEraGate : null;
  const promotionPass = readiness.release_gate_ready === true;
  const stackPass = stack.passes_promotion_gates;

  const tiles = [
    {
      label: "Mean PF",
      value: fmtPf(pfMean),
      sub: `gate ≥ ${pfMeanGate.toFixed(2)}`,
      state: pfMeanPass ?? stackPass,
    },
    {
      label: "Worst-era PF",
      value: fmtPf(worstEra),
      sub: `gate ≥ ${worstEraGate.toFixed(2)}`,
      state: worstEraPass ?? stackPass,
    },
    {
      label: "Early stops",
      value: fmtPct(earlyStop),
      sub: "21–40d cohort",
      state: Number.isFinite(earlyStop) ? earlyStop < 35 : null,
    },
    {
      label: "Stack retention",
      value: fmtPct(retention),
      sub: "experiment keep rate",
      state: Number.isFinite(retention) ? retention >= 70 : null,
    },
    {
      label: "Release gate",
      value: promotionPass ? "Ready" : "Blocked",
      sub: "validation + SLO",
      state: promotionPass,
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
          ? "Loading PF evidence and promotion gates…"
          : "Run signal-stack counterfactual to populate multi-scenario PF evidence.";
    container.innerHTML = `
      <div class="decision-pf-chart decision-pf-chart--placeholder" data-state="${escapeHtml(opts.state)}">
        <div class="decision-pf-row">
          <span class="decision-pf-label">Mean PF</span>
          <div class="decision-pf-bar-track"><div class="decision-pf-bar-fill" style="width:${opts.state === "loading" ? 44 : 0}%"></div></div>
          <span class="decision-pf-value mono-nums">${opts.state === "loading" ? "…" : "—"}</span>
        </div>
        <div class="decision-pf-row">
          <span class="decision-pf-label">Worst era</span>
          <div class="decision-pf-bar-track"><div class="decision-pf-bar-fill decision-pf-bar-fill--worst" style="width:${opts.state === "loading" ? 32 : 0}%"></div></div>
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

  if (!scenarios.length) {
    const pfMean = safeNum(stack.pf_mean, NaN);
    const worstEra = safeNum(stack.worst_era_pf, NaN);
    if (!Number.isFinite(pfMean) && !Number.isFinite(worstEra)) {
      container.innerHTML = `<p class="muted">Run signal-stack counterfactual to populate multi-scenario PF evidence.</p>`;
      return;
    }
    container.innerHTML = `
      <div class="decision-pf-chart">
        <div class="decision-pf-row">
          <span class="decision-pf-label">Stack PF mean</span>
          <div class="decision-pf-bar-track"><div class="decision-pf-bar-fill" style="width:${Math.min(100, (pfMean / 2) * 100)}%"></div></div>
          <span class="decision-pf-value mono-nums">${fmtPf(pfMean)}</span>
        </div>
        <div class="decision-pf-row">
          <span class="decision-pf-label">Worst-era PF</span>
          <div class="decision-pf-bar-track"><div class="decision-pf-bar-fill decision-pf-bar-fill--worst" style="width:${Math.min(100, (worstEra / 2) * 100)}%"></div></div>
          <span class="decision-pf-value mono-nums">${fmtPf(worstEra)}</span>
        </div>
      </div>
      <p class="muted decision-pf-gates-note">Promotion gates: mean PF ≥ ${pfMeanGate.toFixed(2)}, worst-era PF ≥ ${worstEraGate.toFixed(2)}</p>
    `;
    return;
  }

  const maxPf = Math.max(
    pfMeanGate * 1.1,
    ...scenarios.map((s) => safeNum(s.pf_mean, 0)),
    ...scenarios.map((s) => safeNum(s.worst_era_pf, 0)),
    1.5,
  );

  const rows = scenarios
    .map((row) => {
      const label = safeText(row.label || row.key || "scenario").replace(/_/g, " ");
      const pf = safeNum(row.pf_mean, NaN);
      const worst = safeNum(row.worst_era_pf, NaN);
      const pass = row.passes_promotion_gates === true;
      const pfWidth = Number.isFinite(pf) ? Math.min(100, (pf / maxPf) * 100) : 0;
      const worstWidth = Number.isFinite(worst) ? Math.min(100, (worst / maxPf) * 100) : 0;
      return `
        <div class="decision-pf-scenario ${pass ? "decision-pf-scenario--pass" : "decision-pf-scenario--fail"}">
          <div class="decision-pf-scenario-head">
            <strong>${escapeHtml(label)}</strong>
            <span class="pill ${pass ? "good" : "bad"}">${pass ? "PASS" : "FAIL"}</span>
          </div>
          <div class="decision-pf-row">
            <span class="decision-pf-label">Mean PF</span>
            <div class="decision-pf-bar-track">
              <div class="decision-pf-bar-fill" style="width:${pfWidth}%"></div>
              <div class="decision-pf-gate-line" style="left:${Math.min(100, (pfMeanGate / maxPf) * 100)}%" title="PF mean gate ${pfMeanGate.toFixed(2)}"></div>
            </div>
            <span class="decision-pf-value mono-nums">${fmtPf(pf)}</span>
          </div>
          <div class="decision-pf-row">
            <span class="decision-pf-label">Worst era</span>
            <div class="decision-pf-bar-track">
              <div class="decision-pf-bar-fill decision-pf-bar-fill--worst" style="width:${worstWidth}%"></div>
              <div class="decision-pf-gate-line" style="left:${Math.min(100, (worstEraGate / maxPf) * 100)}%" title="Worst-era gate ${worstEraGate.toFixed(2)}"></div>
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
    <div class="decision-pf-chart decision-pf-chart--scenarios">${rows}</div>
    <p class="muted decision-pf-gates-note">
      Gates: mean PF ≥ ${pfMeanGate.toFixed(2)}, worst-era PF ≥ ${worstEraGate.toFixed(2)}
      ${rec ? ` · Recommendation: ${escapeHtml(rec)}` : ""}
      ${reason ? ` — ${escapeHtml(reason.slice(0, 120))}${reason.length > 120 ? "…" : ""}` : ""}
    </p>
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
      <span class="decision-summary-pill neutral">${escapeHtml(opts.message || "Decision evidence pending")}</span>
    `;
    return;
  }
  const signalEdge = payload.signal_edge || {};
  const readiness = payload.promotion_readiness || {};
  const reliability = payload.reliability || {};
  const stack = signalEdge.signal_stack_counterfactual || {};
  const edgeState = safeText(signalEdge.state || "unknown").replace(/_/g, " ");
  const pfMean = fmtPf(stack.pf_mean);
  const worstEra = fmtPf(stack.worst_era_pf);
  const release = readiness.release_gate_ready === true ? "ready" : "blocked";
  const rel = reliability.validation_passed === true && reliability.slo_gate_passed === true ? "healthy" : "at risk";
  container.innerHTML = `
    <span class="decision-summary-pill ${release === "ready" ? "good" : "bad"}">Release ${release}</span>
    <span class="decision-summary-pill ${rel === "healthy" ? "good" : "warn"}">${rel}</span>
    <span class="decision-summary-pill neutral">PF ${pfMean} / worst ${worstEra}</span>
    <span class="decision-summary-pill neutral">${escapeHtml(edgeState)}</span>
  `;
}
