import { safeNum } from "./format.js";

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asObject(value) {
  return isObject(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function hasValue(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

function firstDefined(...values) {
  for (const value of values) {
    if (hasValue(value)) return value;
  }
  return null;
}

function asPctPoint(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  if (n >= 0 && n <= 1) return n * 100;
  return n;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function toConfidence(value) {
  if (typeof value === "string") {
    const raw = value.trim().toLowerCase();
    if (!raw) return { score: null, label: "Unavailable", inferred: false };
    if (raw === "high") return { score: 80, label: "High", inferred: false };
    if (raw === "medium" || raw === "med") return { score: 60, label: "Medium", inferred: false };
    if (raw === "low") return { score: 35, label: "Low", inferred: false };
  }
  const n = asPctPoint(value);
  if (n === null) return { score: null, label: "Unavailable", inferred: false };
  const score = clamp(Math.round(n), 0, 100);
  const label = score >= 70 ? "High" : score >= 45 ? "Medium" : "Low";
  return { score, label, inferred: false };
}

function normalizeRecommendation(rawRecommendation, scoreHint = 50) {
  const raw = String(rawRecommendation || "").trim().toLowerCase();
  if (["long", "buy", "overweight", "bullish"].includes(raw)) return { value: "Long", inferred: false };
  if (["short", "sell", "underweight", "bearish"].includes(raw)) return { value: "Short", inferred: false };
  if (["pass", "hold", "neutral", "watch"].includes(raw)) return { value: "Pass", inferred: false };
  if (scoreHint >= 62) return { value: "Long", inferred: true };
  if (scoreHint <= 40) return { value: "Short", inferred: true };
  return { value: "Pass", inferred: true };
}

function inferScore(appendix) {
  const technical = asObject(appendix.technical);
  const dcf = asObject(appendix.dcf);
  const health = asObject(appendix.health);
  const mirofish = asObject(appendix.mirofish);

  const techScore = hasValue(technical.signal_score) ? clamp(safeNum(technical.signal_score), 0, 100) : 50;
  const dcfScore = hasValue(dcf.margin_of_safety) ? clamp(50 + safeNum(dcf.margin_of_safety), 0, 100) : 50;
  const healthPenalty = asArray(health.flags).length * 8;
  const healthScore = clamp(70 - healthPenalty, 0, 100);
  const miroScore = hasValue(mirofish.conviction_score)
    ? clamp(50 + safeNum(mirofish.conviction_score), 0, 100)
    : 50;
  return Math.round((techScore + dcfScore + healthScore + miroScore) / 4);
}

function inferExpectedReturnBase(appendix) {
  const dcf = asObject(appendix.dcf);
  const mirofish = asObject(appendix.mirofish);
  const dcfMos = asPctPoint(dcf.margin_of_safety);
  const continuation = asPctPoint(mirofish.continuation_probability);
  const bullTrap = asPctPoint(mirofish.bull_trap_probability);
  const continuationSignal = continuation === null || bullTrap === null ? null : (continuation - bullTrap) * 0.15;
  const blended = firstDefined(dcfMos, continuationSignal, 8);
  return clamp(Math.round(safeNum(blended, 8) * 10) / 10, -60, 120);
}

function inferConfidence(appendix, scoreHint) {
  const technical = asObject(appendix.technical);
  const dcf = asObject(appendix.dcf);
  const health = asObject(appendix.health);
  const mirofish = asObject(appendix.mirofish);
  const samples = [];
  if (hasValue(technical.signal_score)) samples.push(clamp(safeNum(technical.signal_score), 0, 100));
  if (hasValue(dcf.margin_of_safety)) samples.push(clamp(50 + safeNum(dcf.margin_of_safety), 0, 100));
  if (hasValue(mirofish.conviction_score)) samples.push(clamp(50 + safeNum(mirofish.conviction_score), 0, 100));
  if (hasValue(health.flags)) samples.push(clamp(70 - asArray(health.flags).length * 8, 0, 100));
  if (!samples.length) samples.push(scoreHint);
  const avg = samples.reduce((acc, n) => acc + n, 0) / samples.length;
  const dispersion = samples.reduce((acc, n) => acc + Math.abs(n - avg), 0) / samples.length;
  const score = clamp(Math.round(44 + Math.abs(avg - 50) * 0.85 - dispersion * 0.35), 20, 90);
  return {
    score,
    label: score >= 70 ? "High" : score >= 45 ? "Medium" : "Low",
    inferred: true,
  };
}

function normalizeList(input, fallback = [], limit = 3) {
  const list = asArray(input)
    .map((item) => String(item ?? "").trim())
    .filter((item) => item.length > 0);
  const merged = list.length ? list : fallback;
  return merged.slice(0, limit);
}

function inferTopThesisPoints(appendix) {
  const technical = asObject(appendix.technical);
  const dcf = asObject(appendix.dcf);
  const mirofish = asObject(appendix.mirofish);
  const points = [];
  if (technical.stage_2) points.push("Trend regime remains constructive (Stage 2 alignment).");
  if (technical.vcp) points.push("Volatility contraction supports asymmetric entry timing.");
  if (hasValue(dcf.margin_of_safety) && safeNum(dcf.margin_of_safety) > 0) {
    points.push(`Valuation supports upside with margin of safety at ${safeNum(dcf.margin_of_safety).toFixed(1)}%.`);
  }
  if (hasValue(mirofish.summary)) points.push(String(mirofish.summary));
  if (!points.length) points.push("Cross-framework thesis support is currently limited.");
  return points.slice(0, 3);
}

function inferRisks(appendix) {
  const health = asObject(appendix.health);
  const edgar = asObject(appendix.edgar);
  const risks = [];
  asArray(health.flags).forEach((flag) => risks.push(`Balance sheet / operating flag: ${flag}`));
  asArray(edgar.risk_reasons).forEach((risk) => risks.push(`Filing risk factor: ${risk}`));
  if (!risks.length) risks.push("Primary risk factors were not explicitly surfaced by the API.");
  return risks.slice(0, 3);
}

function inferCatalysts(appendix) {
  const technical = asObject(appendix.technical);
  const edgar = asObject(appendix.edgar);
  const catalysts = [];
  if (hasValue(technical.sector_etf)) catalysts.push(`Near-term sector tape confirmation via ${technical.sector_etf}.`);
  if (edgar.recent_8k) catalysts.push("Recent 8-K indicates event-driven headline sensitivity.");
  if (hasValue(edgar.filing_recency_days)) catalysts.push(`Upcoming disclosure cadence likely within ~${edgar.filing_recency_days} day(s).`);
  if (!catalysts.length) catalysts.push("Catalyst timeline is unavailable from current report inputs.");
  return catalysts.slice(0, 3);
}

function inferInvalidation(recommendation, appendix) {
  const technical = asObject(appendix.technical);
  const dcf = asObject(appendix.dcf);
  if (recommendation === "Long") {
    return firstDefined(
      appendix?.ic_snapshot?.invalidation_criteria,
      `Invalidate if price loses trend support near SMA-200 (${technical.sma_200 || "N/A"}) or valuation edge turns negative (MOS < 0%).`,
    );
  }
  if (recommendation === "Short") {
    return firstDefined(
      appendix?.ic_snapshot?.invalidation_criteria,
      `Invalidate if downside thesis is disproven by sustained break above resistance and MOS re-rates above 0% (current ${hasValue(dcf.margin_of_safety) ? `${safeNum(dcf.margin_of_safety).toFixed(1)}%` : "N/A"}).`,
    );
  }
  return firstDefined(
    appendix?.ic_snapshot?.invalidation_criteria,
    "Invalidate pass stance if risk-reward improves materially or new evidence changes conviction tier.",
  );
}

function normalizeScenarioRows(rawRows) {
  const rows = [];
  const source = asObject(rawRows);
  ["base", "bull", "bear"].forEach((name) => {
    const node = asObject(source[name]);
    const probability = asPctPoint(firstDefined(node.probability, node.prob, node.weight));
    const returnPct = asPctPoint(firstDefined(node.return_pct, node.expected_return, node.target_return, node.price_return));
    const priceTarget = firstDefined(node.price_target, node.target_price, node.target);
    rows.push({
      name: name[0].toUpperCase() + name.slice(1),
      probability: probability === null ? null : clamp(probability, 0, 100),
      return_pct: returnPct,
      price_target: priceTarget,
      rationale: firstDefined(node.rationale, node.note, node.thesis),
    });
  });
  return rows;
}

function inferScenarioRows(icSnapshot, appendix) {
  const base = safeNum(icSnapshot.expected_return_base_case, 0);
  const confidence = safeNum(icSnapshot.confidence_score, 50);
  const recommendation = icSnapshot.recommendation;
  let baseProb = 50;
  let bullProb = 30;
  let bearProb = 20;
  if (recommendation === "Short") {
    bullProb = 20;
    bearProb = 35;
  }
  if (confidence >= 70) {
    baseProb = 55;
    if (recommendation === "Long") {
      bullProb = 33;
      bearProb = 12;
    } else if (recommendation === "Short") {
      bullProb = 12;
      bearProb = 33;
    } else {
      bullProb = 22;
      bearProb = 23;
    }
  }
  const tech = asObject(appendix.technical);
  const dcf = asObject(appendix.dcf);
  return [
    {
      name: "Base",
      probability: baseProb,
      return_pct: base,
      price_target: firstDefined(dcf.intrinsic_value, tech.current_price),
      rationale: "Central case based on blended valuation and setup quality.",
    },
    {
      name: "Bull",
      probability: bullProb,
      return_pct: Math.round((base + Math.max(8, Math.abs(base) * 0.7)) * 10) / 10,
      price_target: hasValue(dcf.intrinsic_value) ? safeNum(dcf.intrinsic_value) * 1.1 : null,
      rationale: "Upside case assumes stronger execution and multiple support.",
    },
    {
      name: "Bear",
      probability: 100 - baseProb - bullProb,
      return_pct: Math.round((base - Math.max(10, Math.abs(base) * 0.8)) * 10) / 10,
      price_target: hasValue(dcf.current_price) ? safeNum(dcf.current_price) * 0.88 : null,
      rationale: "Downside case assumes thesis drift and weaker market regime.",
    },
  ];
}

function computeExpectedValue(rows) {
  const valid = rows.filter((row) => Number.isFinite(Number(row.probability)) && Number.isFinite(Number(row.return_pct)));
  if (!valid.length) return null;
  return valid.reduce((acc, row) => acc + (safeNum(row.probability) / 100) * safeNum(row.return_pct), 0);
}

function computeUpsideDownside(rows) {
  const bull = rows.find((row) => row.name.toLowerCase() === "bull");
  const bear = rows.find((row) => row.name.toLowerCase() === "bear");
  if (!bull || !bear) return null;
  const upside = safeNum(bull.return_pct);
  const downsideAbs = Math.abs(safeNum(bear.return_pct));
  if (!downsideAbs) return null;
  return upside / downsideAbs;
}

function normalizePortfolioFit(rawPortfolioFit, context, icSnapshot, appendix) {
  const explicit = asObject(rawPortfolioFit);
  const risk = asObject(context?.portfolioRisk);
  const technical = asObject(appendix.technical);
  const sectorTag = firstDefined(explicit.sector, technical.sector, technical.sector_etf, "Unknown");
  const sectorRows = asArray(risk.sector_allocation);
  const positions = asArray(risk.positions_weighted);

  const suggestedSizePct = safeNum(icSnapshot.suggested_position_size_pct, 0);
  const sectorOverlap = firstDefined(
    explicit.sector_overlap_pct,
    explicit.sector_overlap,
    sectorRows.find((row) => String(row.sector || "").toLowerCase() === String(sectorTag || "").toLowerCase())?.weight_pct,
  );
  const topPositionPct = safeNum(risk?.concentration?.top_position_pct, 0);
  const concentrationContribution = firstDefined(
    explicit.concentration_contribution_pct,
    explicit.concentration_contribution,
    suggestedSizePct ? topPositionPct + suggestedSizePct : null,
  );

  const sameSectorCount = sectorRows.filter((row) => String(row.sector || "").toLowerCase() === String(sectorTag || "").toLowerCase()).length;
  const correlationProxy = firstDefined(
    explicit.correlation_proxy,
    explicit.overlap_proxy,
    sameSectorCount > 0 ? "Elevated overlap with current sector sleeve." : "Low direct overlap visible in current holdings.",
  );

  let riskBudgetImpact = String(firstDefined(explicit.risk_budget_impact, explicit.risk_budget_hint, "") || "").trim();
  if (!riskBudgetImpact) {
    const concentration = safeNum(risk?.concentration?.hhi, 0);
    const overlap = safeNum(sectorOverlap, 0);
    if (suggestedSizePct >= 3 || concentration > 2400 || overlap > 30) riskBudgetImpact = "High";
    else if (suggestedSizePct >= 1.5 || overlap > 18 || positions.length >= 20) riskBudgetImpact = "Medium";
    else riskBudgetImpact = "Low";
  }

  const exposureBudgetRemaining = firstDefined(
    explicit.exposure_budget_remaining_pct,
    explicit.exposure_budget_remaining,
    Number.isFinite(Number(sectorOverlap)) ? Math.max(0, 30 - safeNum(sectorOverlap) - suggestedSizePct) : null,
  );

  const hasComputableData = hasValue(sectorOverlap) || hasValue(concentrationContribution) || hasValue(exposureBudgetRemaining) || hasValue(explicit.risk_budget_impact);
  return {
    sector: sectorTag,
    sector_overlap_pct: hasValue(sectorOverlap) ? Number(sectorOverlap) : null,
    concentration_contribution_pct: hasValue(concentrationContribution) ? Number(concentrationContribution) : null,
    correlation_overlap_proxy: correlationProxy || null,
    risk_budget_impact: riskBudgetImpact || "Unavailable",
    exposure_budget_remaining_pct: hasValue(exposureBudgetRemaining) ? Number(exposureBudgetRemaining) : null,
    has_live_data: hasComputableData,
    fallback_message: hasComputableData
      ? null
      : "Live portfolio/risk context is unavailable. Portfolio fit shown in safe fallback mode.",
  };
}

function normalizeMonitoringPlan(rawMonitoringPlan, icSnapshot, scenarios) {
  const plan = asObject(rawMonitoringPlan);
  const triggers = normalizeList(
    firstDefined(plan.triggers, plan.monitor_triggers),
    [
      "Review setup after each material filing or guidance update.",
      "Recompute scenario probabilities on regime change and risk-budget shifts.",
      "Escalate when invalidation criteria are met.",
    ],
    4,
  );
  return {
    triggers,
    review_cadence: firstDefined(plan.review_cadence, plan.cadence, "Weekly or on catalyst"),
    claim: `Position expression: ${icSnapshot.recommendation}`,
    evidence: `Expected value (${scenarios.expected_value_pct != null ? `${scenarios.expected_value_pct.toFixed(1)}%` : "Unavailable"}) and risk budget context drive monitoring cadence.`,
    confidence: `${icSnapshot.confidence_label}${icSnapshot.confidence_score != null ? ` (${icSnapshot.confidence_score}/100)` : ""}`,
    falsifier: icSnapshot.invalidation_criteria,
  };
}

function buildThesis(icSnapshot, topThesisPoints) {
  return {
    claim: `${icSnapshot.recommendation} with base-case return of ${
      icSnapshot.expected_return_base_case != null ? `${icSnapshot.expected_return_base_case.toFixed(1)}%` : "Unavailable"
    }.`,
    evidence: topThesisPoints[0] || "Evidence unavailable.",
    confidence: `${icSnapshot.confidence_label}${icSnapshot.confidence_score != null ? ` (${icSnapshot.confidence_score}/100)` : ""}`,
    falsifier: icSnapshot.invalidation_criteria,
  };
}

function unwrapPayload(rawPayload) {
  const payload = asObject(rawPayload);
  if (payload.section && payload.data) return { ticker: payload.ticker, appendix: { [payload.section]: payload.data } };
  return { ticker: payload.ticker, appendix: payload };
}

export function normalizeReportPayload(rawPayload, context = {}) {
  const { ticker, appendix } = unwrapPayload(rawPayload);
  const inferred = [];
  const warnings = [];
  const scoreHint = inferScore(appendix);
  const rawIcSnapshot = asObject(appendix.ic_snapshot);

  const recommendation = normalizeRecommendation(
    firstDefined(rawIcSnapshot.recommendation, rawIcSnapshot.decision, appendix.recommendation),
    scoreHint,
  );
  if (recommendation.inferred) inferred.push("ic_snapshot.recommendation");

  const timeHorizon = firstDefined(rawIcSnapshot.time_horizon, rawIcSnapshot.horizon, appendix.time_horizon, "3-12 months");
  if (!hasValue(rawIcSnapshot.time_horizon) && !hasValue(rawIcSnapshot.horizon)) inferred.push("ic_snapshot.time_horizon");

  const expectedReturnRaw = firstDefined(
    rawIcSnapshot.expected_return_base_case,
    rawIcSnapshot.expected_return,
    appendix.expected_return_base_case,
    appendix.base_case_return,
  );
  const expectedReturn = expectedReturnRaw === null ? inferExpectedReturnBase(appendix) : asPctPoint(expectedReturnRaw);
  if (expectedReturnRaw === null) inferred.push("ic_snapshot.expected_return_base_case");

  const confidenceRaw = firstDefined(rawIcSnapshot.confidence, rawIcSnapshot.confidence_score, appendix.confidence);
  const parsedConfidence = toConfidence(confidenceRaw);
  const confidence = parsedConfidence.score === null ? inferConfidence(appendix, scoreHint) : parsedConfidence;
  if (parsedConfidence.score === null) inferred.push("ic_snapshot.confidence");

  const sizeRaw = firstDefined(rawIcSnapshot.position_size_hint, rawIcSnapshot.suggested_position_size, appendix.position_size_hint);
  let suggestedPositionSizeText = "Unavailable";
  let suggestedPositionSizePct = null;
  if (hasValue(sizeRaw)) {
    suggestedPositionSizeText = String(sizeRaw);
    suggestedPositionSizePct = asPctPoint(sizeRaw);
  } else {
    if (recommendation.value === "Pass") {
      suggestedPositionSizeText = "0% (Pass)";
      suggestedPositionSizePct = 0;
    } else {
      const inferredPct = confidence.score >= 70 ? 2.0 : confidence.score >= 50 ? 1.25 : 0.75;
      suggestedPositionSizePct = inferredPct;
      suggestedPositionSizeText = `${inferredPct.toFixed(2)}% (${Math.round(inferredPct * 100)} bps)`;
    }
    inferred.push("ic_snapshot.suggested_position_size");
  }

  const topThesisPoints = normalizeList(
    firstDefined(rawIcSnapshot.top_thesis_points, rawIcSnapshot.thesis_points, appendix.thesis_points),
    inferTopThesisPoints(appendix),
    3,
  );
  if (!hasValue(rawIcSnapshot.top_thesis_points) && !hasValue(rawIcSnapshot.thesis_points)) inferred.push("ic_snapshot.top_thesis_points");

  const topRisks = normalizeList(
    firstDefined(rawIcSnapshot.top_risks, rawIcSnapshot.risks, appendix.risks),
    inferRisks(appendix),
    3,
  );
  if (!hasValue(rawIcSnapshot.top_risks) && !hasValue(rawIcSnapshot.risks)) inferred.push("ic_snapshot.top_risks");

  const catalystsTimeline = normalizeList(
    firstDefined(rawIcSnapshot.catalysts_timeline, rawIcSnapshot.catalysts, appendix.catalyst_calendar),
    inferCatalysts(appendix),
    4,
  );
  if (!hasValue(rawIcSnapshot.catalysts_timeline) && !hasValue(rawIcSnapshot.catalysts)) inferred.push("ic_snapshot.catalysts_timeline");

  const invalidationCriteria = firstDefined(
    rawIcSnapshot.invalidation_criteria,
    rawIcSnapshot.falsifier,
    appendix.invalidation_criteria,
    inferInvalidation(recommendation.value, appendix),
  );
  if (!hasValue(rawIcSnapshot.invalidation_criteria) && !hasValue(rawIcSnapshot.falsifier)) inferred.push("ic_snapshot.invalidation_criteria");

  const icSnapshot = {
    recommendation: recommendation.value,
    time_horizon: hasValue(timeHorizon) ? String(timeHorizon) : "Unavailable",
    expected_return_base_case: Number.isFinite(Number(expectedReturn)) ? Number(expectedReturn) : null,
    confidence_score: confidence.score,
    confidence_label: confidence.label,
    suggested_position_size_text: suggestedPositionSizeText,
    suggested_position_size_pct: Number.isFinite(Number(suggestedPositionSizePct)) ? Number(suggestedPositionSizePct) : null,
    top_thesis_points: topThesisPoints.length ? topThesisPoints : ["Unavailable"],
    top_risks: topRisks.length ? topRisks : ["Unavailable"],
    catalysts_timeline: catalystsTimeline.length ? catalystsTimeline : ["Unavailable"],
    invalidation_criteria: hasValue(invalidationCriteria) ? String(invalidationCriteria) : "Unavailable",
  };

  const rawScenarios = firstDefined(
    appendix.scenarios,
    appendix.scenario_analysis,
    appendix.scenario,
    rawIcSnapshot.scenarios,
  );
  let scenarioRows = [];
  let scenariosInferred = false;
  if (isObject(rawScenarios)) {
    scenarioRows = normalizeScenarioRows(rawScenarios);
  } else if (Array.isArray(rawScenarios)) {
    scenarioRows = rawScenarios.slice(0, 3).map((row, idx) => {
      const node = asObject(row);
      const name = firstDefined(node.name, ["Base", "Bull", "Bear"][idx], `Case ${idx + 1}`);
      return {
        name,
        probability: asPctPoint(firstDefined(node.probability, node.prob, node.weight)),
        return_pct: asPctPoint(firstDefined(node.return_pct, node.target_return, node.expected_return)),
        price_target: firstDefined(node.price_target, node.target_price, node.target),
        rationale: firstDefined(node.rationale, node.note),
      };
    });
  }
  const scenarioHasSignal = scenarioRows.some((row) => hasValue(row.probability) || hasValue(row.return_pct) || hasValue(row.price_target));
  if (!scenarioHasSignal) {
    scenarioRows = inferScenarioRows(icSnapshot, appendix);
    scenariosInferred = true;
    inferred.push("scenarios");
    warnings.push("Scenario analysis is inferred from technical, valuation, and risk signals.");
  }
  const expectedValue = computeExpectedValue(scenarioRows);
  const upsideDownside = computeUpsideDownside(scenarioRows);
  const sensitivityBullets = normalizeList(
    firstDefined(appendix.sensitivity_bullets, appendix.sensitivity, asObject(rawScenarios).sensitivity_bullets),
    [
      "Valuation assumptions (growth and discount rate) can shift expected value materially.",
      "Trend breaks and volume deterioration increase bear-case probability.",
      "Filing or guidance surprises can re-weight scenario probabilities quickly.",
    ],
    4,
  );
  if (!hasValue(appendix.sensitivity_bullets) && !hasValue(appendix.sensitivity)) inferred.push("scenarios.sensitivity_bullets");

  const scenarios = {
    rows: scenarioRows.map((row) => ({
      name: hasValue(row.name) ? String(row.name) : "Unavailable",
      probability: Number.isFinite(Number(row.probability)) ? Number(row.probability) : null,
      return_pct: Number.isFinite(Number(row.return_pct)) ? Number(row.return_pct) : null,
      price_target: hasValue(row.price_target) ? row.price_target : null,
      rationale: hasValue(row.rationale) ? String(row.rationale) : null,
    })),
    expected_value_pct: expectedValue == null ? null : Number(expectedValue.toFixed(2)),
    upside_downside_ratio: upsideDownside == null ? null : Number(upsideDownside.toFixed(2)),
    sensitivity_bullets: sensitivityBullets,
    inferred: scenariosInferred,
    warning: scenariosInferred
      ? "Scenario probabilities and targets are inferred. Treat as directional until explicit scenario payload is available."
      : null,
  };

  const riskRegister = normalizeList(
    firstDefined(appendix.risk_register, appendix.risks, icSnapshot.top_risks),
    icSnapshot.top_risks,
    6,
  );

  const catalystCalendar = normalizeList(
    firstDefined(appendix.catalyst_calendar, appendix.catalysts, icSnapshot.catalysts_timeline),
    icSnapshot.catalysts_timeline,
    6,
  );

  const portfolioFit = normalizePortfolioFit(
    firstDefined(appendix.portfolio_fit, asObject(appendix.synthesis).portfolio_fit),
    context,
    icSnapshot,
    appendix,
  );

  const thesis = buildThesis(icSnapshot, topThesisPoints);
  const monitoringPlan = normalizeMonitoringPlan(appendix.monitoring_plan, icSnapshot, scenarios);

  return {
    ticker: hasValue(ticker) ? String(ticker) : "—",
    ic_snapshot: icSnapshot,
    scenarios,
    thesis,
    risk_register: riskRegister,
    catalyst_calendar: catalystCalendar,
    portfolio_fit: portfolioFit,
    monitoring_plan: monitoringPlan,
    appendix,
    meta: {
      inferred_fields: inferred,
      warnings,
      normalization_version: "v1",
    },
  };
}

export function runReportNormalizationSmokeChecks() {
  const legacyPayload = {
    ticker: "AAPL",
    technical: { signal_score: 72, stage_2: true, vcp: true, sector_etf: "XLK", sma_200: 182.2 },
    dcf: { margin_of_safety: 12.5, intrinsic_value: 238.4, current_price: 211.2 },
    health: { flags: [] },
    edgar: { risk_reasons: ["Supply chain volatility"] },
    mirofish: { conviction_score: 18, continuation_probability: 0.62, bull_trap_probability: 0.24 },
  };

  const partialPayload = { ticker: "MSFT", section: "technical", data: { signal_score: 44, stage_2: false, vcp: false } };
  const enrichedPayload = {
    ticker: "NVDA",
    ic_snapshot: {
      recommendation: "Long",
      time_horizon: "6-12 months",
      expected_return_base_case: 0.22,
      confidence: "High",
      suggested_position_size: "1.50% (150 bps)",
      top_thesis_points: ["AI capex cycle remains intact", "Gross margin expansion continues"],
      top_risks: ["China export restrictions"],
      catalysts_timeline: ["Earnings in 3 weeks"],
      invalidation_criteria: "Order book deceleration in two consecutive quarters.",
    },
    scenarios: {
      base: { probability: 0.55, return_pct: 0.2, price_target: 1150 },
      bull: { probability: 0.3, return_pct: 0.4, price_target: 1350 },
      bear: { probability: 0.15, return_pct: -0.22, price_target: 870 },
    },
  };

  const fixtures = [legacyPayload, partialPayload, enrichedPayload];
  const results = fixtures.map((fixture, idx) => {
    const first = normalizeReportPayload(fixture);
    const second = normalizeReportPayload(fixture);
    const deterministic = JSON.stringify(first) === JSON.stringify(second);
    return {
      fixture: idx + 1,
      ok: deterministic && isObject(first.ic_snapshot) && isObject(first.scenarios),
      deterministic,
    };
  });

  return {
    ok: results.every((row) => row.ok),
    results,
  };
}
