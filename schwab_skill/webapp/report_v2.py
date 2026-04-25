"""Canonical hedge-fund style report assembler (backward-compatible add-on).

This module derives deterministic `report_v2` payloads from existing report sections.
No legacy keys are modified; callers append `report_v2` alongside existing response data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

ConfidenceLevel = Literal["low", "medium", "high"]
RiskLevel = Literal["low", "medium", "high"]
Recommendation = Literal["long", "short", "pass"]


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _bound(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clean_items(items: list[Any], *, limit: int = 3) -> list[str]:
    out: list[str] = []
    for raw in items:
        text = str(raw or "").strip()
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _confidence_bucket(score: float) -> ConfidenceLevel:
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _evidence_mode(edgar: dict[str, Any]) -> Literal["full_text", "metadata_fallback", "mixed"]:
    filing_analysis = edgar.get("filing_analysis")
    if not isinstance(filing_analysis, dict) or not filing_analysis:
        return "metadata_fallback"
    has_llm = bool(str(filing_analysis.get("llm_summary") or "").strip())
    has_evidence = bool(filing_analysis.get("evidence"))
    has_takeaway = bool(str(filing_analysis.get("high_level_takeaway") or "").strip())
    if has_llm and has_evidence:
        return "full_text"
    if has_llm or has_takeaway:
        return "mixed"
    return "metadata_fallback"


def _normalize_probs(base: float, bull: float, bear: float) -> tuple[float, float, float]:
    raw = [_bound(base, 0.0, 1.0), _bound(bull, 0.0, 1.0), _bound(bear, 0.0, 1.0)]
    total = sum(raw)
    if total <= 0:
        return 0.5, 0.25, 0.25
    normalized = [x / total for x in raw]
    rounded = [round(x, 4) for x in normalized]
    # Force exact sum=1.0 after rounding for deterministic API behavior.
    rounded[0] = round(1.0 - rounded[1] - rounded[2], 4)
    if rounded[0] < 0:
        rounded[0] = 0.0
        total_tail = rounded[1] + rounded[2]
        if total_tail <= 0:
            return 0.5, 0.25, 0.25
        rounded[1] = round(rounded[1] / total_tail, 4)
        rounded[2] = round(1.0 - rounded[1], 4)
    return rounded[0], rounded[1], rounded[2]


def _pick_thesis_items(report: dict[str, Any]) -> tuple[list[str], list[str], list[str], list[str]]:
    technical = report.get("technical") or {}
    dcf = report.get("dcf") or {}
    health = report.get("health") or {}
    edgar = report.get("edgar") or {}
    miro = report.get("mirofish") or {}

    thesis: list[str] = []
    risks: list[str] = []
    catalysts: list[str] = []
    invalidation: list[str] = []

    signal = _safe_float(technical.get("signal_score"))
    if bool(technical.get("stage_2")) and bool(technical.get("vcp")):
        thesis.append("Stage 2 trend and VCP setup are aligned for trend continuation.")
        catalysts.append("Trend confirmation via sustained breakout volume.")
    elif bool(technical.get("stage_2")):
        thesis.append("Primary trend remains constructive, but setup quality is incomplete.")
        risks.append("VCP confirmation is missing, raising false-breakout risk.")
    else:
        risks.append("Technical regime is not Stage 2, reducing breakout reliability.")
        invalidation.append("Price structure remains below Stage 2 conditions.")
    if signal is not None:
        if signal >= 65:
            thesis.append(f"Technical signal score {signal:.1f}/100 supports directional conviction.")
        elif signal <= 45:
            risks.append(f"Technical signal score {signal:.1f}/100 indicates weak setup quality.")

    mos = _safe_float(dcf.get("margin_of_safety"))
    if mos is not None:
        if mos >= 10:
            thesis.append(f"DCF margin of safety ({mos:.1f}%) implies valuation upside.")
        elif mos <= -10:
            risks.append(f"DCF margin of safety ({mos:.1f}%) indicates overvaluation risk.")
            invalidation.append("Valuation premium remains elevated versus intrinsic estimate.")

    flags = _clean_items(list(health.get("flags") or []), limit=4)
    if flags:
        risks.extend(flags[:2])
        invalidation.append("Financial health flags persist without improvement.")
    else:
        thesis.append("Balance sheet and operating health flags remain contained.")

    risk_reasons = _clean_items(list(edgar.get("risk_reasons") or []), limit=4)
    risks.extend(risk_reasons[:2])
    if bool(edgar.get("recent_8k")):
        catalysts.append("Recent 8-K introduces a potential near-term information catalyst.")
    if risk_reasons:
        invalidation.append("Material SEC-disclosed risk factors worsen.")

    conviction = _safe_float(miro.get("conviction_score"))
    if conviction is not None:
        if conviction >= 30:
            thesis.append(f"Cross-agent sentiment conviction ({conviction:.0f}) is supportive.")
        elif conviction <= -30:
            risks.append(f"Cross-agent sentiment conviction ({conviction:.0f}) is adverse.")

    if not thesis:
        thesis = ["Signal set is mixed; no decisive edge has been established."]
    if not risks:
        risks = ["No dominant risk surfaced from current section data."]
    if not catalysts:
        catalysts = ["Earnings update or material filing could reset expectations."]
    if not invalidation:
        invalidation = ["Risk/reward deteriorates materially versus current base case."]
    return thesis[:3], risks[:3], catalysts[:3], invalidation[:3]


def _recommendation_bundle(report: dict[str, Any]) -> tuple[Recommendation, float, float, list[str]]:
    """Deterministic recommendation and confidence.

    Scoring model (fully deterministic):
    - technical signal contributes +/-25 around a neutral score of 50
    - Stage 2 and VCP structure contribute fixed bonuses/penalties
    - valuation (margin of safety) contributes up to +/-25
    - health flags and EDGAR risk tag reduce score
    - Mirofish conviction contributes up to +/-12.5
    """

    technical = report.get("technical") or {}
    dcf = report.get("dcf") or {}
    health = report.get("health") or {}
    edgar = report.get("edgar") or {}
    miro = report.get("mirofish") or {}

    inferred_fields: list[str] = []
    signal_score = _safe_float(technical.get("signal_score"))
    if signal_score is None:
        signal_score = 50.0
        inferred_fields.append("technical.signal_score")
    score = _bound((signal_score - 50.0) * 0.5, -25.0, 25.0)
    score += 6.0 if bool(technical.get("stage_2")) else -6.0
    score += 4.0 if bool(technical.get("vcp")) else -2.0

    mos = _safe_float(dcf.get("margin_of_safety"))
    if mos is None:
        inferred_fields.append("dcf.margin_of_safety")
    else:
        score += _bound(mos, -25.0, 25.0)

    flags = list(health.get("flags") or [])
    score -= min(12.0, float(len(flags) * 4))

    risk_tag = str(edgar.get("risk_tag") or "").strip().lower()
    if risk_tag in {"high", "elevated", "red"}:
        score -= 8.0
    elif risk_tag in {"low", "green"}:
        score += 4.0

    conviction = _safe_float(miro.get("conviction_score"))
    if conviction is None:
        inferred_fields.append("mirofish.conviction_score")
    else:
        score += _bound(conviction * 0.125, -12.5, 12.5)

    if score >= 12.0:
        rec: Recommendation = "long"
    elif score <= -12.0:
        rec = "short"
    else:
        rec = "pass"
    confidence = _bound(50.0 + abs(score) * 1.3, 5.0, 95.0)
    return rec, round(score, 2), round(confidence, 1), inferred_fields


def _build_risk_register(
    health_flags: list[str],
    edgar_risks: list[str],
    recommendation: Recommendation,
) -> list[dict[str, Any]]:
    register: list[dict[str, Any]] = []
    for flag in _clean_items(health_flags, limit=3):
        register.append(
            {
                "risk": flag,
                "likelihood": "medium",
                "impact": "high",
                "mitigation": "Tighten size and require sequential metric improvement before adding risk.",
            }
        )
    for risk in _clean_items(edgar_risks, limit=3):
        register.append(
            {
                "risk": risk,
                "likelihood": "medium",
                "impact": "medium",
                "mitigation": "Track filing updates and cut exposure on adverse disclosure drift.",
            }
        )
    if recommendation == "pass":
        register.append(
            {
                "risk": "Edge quality is currently ambiguous.",
                "likelihood": "high",
                "impact": "medium",
                "mitigation": "Wait for setup confirmation rather than forcing directional exposure.",
            }
        )
    if not register:
        register.append(
            {
                "risk": "No explicit high-salience risk found in current sections.",
                "likelihood": "low",
                "impact": "low",
                "mitigation": "Maintain routine risk review cadence.",
            }
        )
    return register[:5]


def _build_catalyst_calendar(report: dict[str, Any]) -> list[dict[str, Any]]:
    technical = report.get("technical") or {}
    edgar = report.get("edgar") or {}
    output: list[dict[str, Any]] = []
    for filing in (edgar.get("recent_filings") or [])[:3]:
        if not isinstance(filing, dict):
            continue
        form = str(filing.get("form") or "SEC filing").strip()
        dt = str(filing.get("date") or "").strip() or None
        desc = str(filing.get("description") or "").strip() or None
        output.append(
            {
                "name": form,
                "date": dt,
                "expected_impact": desc,
                "confidence": "medium" if dt else "low",
            }
        )
    if bool(technical.get("stage_2")):
        output.append(
            {
                "name": "Trend persistence confirmation",
                "date": None,
                "expected_impact": "Continuation above key moving-average structure.",
                "confidence": "medium",
            }
        )
    if not output:
        output.append(
            {
                "name": "Next earnings / major filing",
                "date": None,
                "expected_impact": "Potential reset of growth and risk assumptions.",
                "confidence": None,
            }
        )
    return output[:6]


def _portfolio_fit_from_summary(
    *,
    ticker: str,
    technical: dict[str, Any],
    portfolio_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(portfolio_summary, dict):
        return {
            "available": False,
            "sector_overlap_pct": None,
            "concentration_impact": None,
            "correlation_proxy": None,
            "risk_budget_impact": None,
            "notes": ["Portfolio context unavailable; account snapshot not provided."],
        }

    positions = list(portfolio_summary.get("positions") or [])
    total_mv = _safe_float(portfolio_summary.get("total_market_value")) or 0.0
    if total_mv <= 0 or not positions:
        return {
            "available": True,
            "sector_overlap_pct": 0.0,
            "concentration_impact": "low",
            "correlation_proxy": 0.0,
            "risk_budget_impact": "low",
            "notes": ["No active positions detected; new position impact is minimal."],
        }

    target_etf = str(technical.get("sector_etf") or "").strip()
    overlap_mv = 0.0
    top_weight = 0.0
    weight_sq_sum = 0.0
    for row in positions:
        if not isinstance(row, dict):
            continue
        mv = _safe_float(row.get("market_value")) or 0.0
        if mv <= 0:
            continue
        wt = (mv / total_mv) * 100.0
        top_weight = max(top_weight, wt)
        weight_sq_sum += wt**2
        if target_etf:
            row_sector = str(row.get("sector_etf") or "").strip()
            if row_sector == target_etf:
                overlap_mv += mv

    overlap_pct = round((overlap_mv / total_mv) * 100.0, 2) if total_mv > 0 else 0.0
    concentration_impact: RiskLevel = "low"
    if top_weight >= 20:
        concentration_impact = "high"
    elif top_weight >= 12:
        concentration_impact = "medium"

    hhi = weight_sq_sum
    risk_budget_impact: RiskLevel = "low"
    if hhi >= 2000:
        risk_budget_impact = "high"
    elif hhi >= 1400:
        risk_budget_impact = "medium"

    corr_proxy = round(_bound((overlap_pct / 100.0) * 0.9 + (top_weight / 100.0) * 0.4, 0.0, 1.0), 3)
    notes = [
        f"Ticker {ticker.upper()} mapped to sector proxy {target_etf or 'unknown'}.",
        f"Top position weight: {top_weight:.2f}%.",
    ]
    if overlap_pct > 0:
        notes.append(f"Existing sector overlap is {overlap_pct:.2f}% of portfolio value.")
    else:
        notes.append("No direct sector overlap detected from available holdings metadata.")
    return {
        "available": True,
        "sector_overlap_pct": overlap_pct,
        "concentration_impact": concentration_impact,
        "correlation_proxy": corr_proxy,
        "risk_budget_impact": risk_budget_impact,
        "notes": notes[:4],
    }


def build_report_v2(
    report: dict[str, Any],
    *,
    portfolio_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble canonical `report_v2` payload from existing report sections."""

    ticker = str(report.get("ticker") or "").strip().upper()
    generated_at = str(report.get("generated_at") or datetime.now(timezone.utc).isoformat())
    technical = dict(report.get("technical") or {})
    dcf = dict(report.get("dcf") or {})
    health = dict(report.get("health") or {})
    edgar = dict(report.get("edgar") or {})
    miro = dict(report.get("mirofish") or {})
    synthesis = str(report.get("synthesis") or "").strip()

    recommendation, rec_score, confidence_score, inferred = _recommendation_bundle(report)
    thesis_top3, risks_top3, catalysts_top3, invalidation = _pick_thesis_items(report)
    mos = _safe_float(dcf.get("margin_of_safety"))
    expected_return_base = mos if mos is not None else _bound(rec_score * 0.8, -20.0, 20.0)
    if mos is None:
        inferred.append("ic_snapshot.expected_return_base_pct")

    confidence_bucket = _confidence_bucket(confidence_score)
    position_size_hint = round(_bound((confidence_score / 100.0) * 8.0, 0.0, 8.0), 2)

    bull_return = round(expected_return_base + max(6.0, abs(expected_return_base) * 0.45), 2)
    bear_return = round(expected_return_base - max(6.0, abs(expected_return_base) * 0.65), 2)
    if bear_return > expected_return_base:
        bear_return = round(expected_return_base - 6.0, 2)

    bull_p_raw = 0.22 + max(rec_score, 0.0) / 160.0
    bear_p_raw = 0.22 + max(-rec_score, 0.0) / 160.0 + min(0.15, len(health.get("flags") or []) * 0.03)
    base_p_raw = 1.0 - bull_p_raw - bear_p_raw
    base_p, bull_p, bear_p = _normalize_probs(base_p_raw, bull_p_raw, bear_p_raw)
    ev = round(base_p * expected_return_base + bull_p * bull_return + bear_p * bear_return, 2)
    payoff_ratio = round(abs(bull_return) / max(abs(bear_return), 0.01), 3)

    catalyst_calendar = _build_catalyst_calendar(report)
    risk_register = _build_risk_register(list(health.get("flags") or []), list(edgar.get("risk_reasons") or []), recommendation)
    portfolio_fit = _portfolio_fit_from_summary(
        ticker=ticker,
        technical=technical,
        portfolio_summary=portfolio_summary,
    )

    sec_takeaway = ""
    filing_analysis = edgar.get("filing_analysis")
    if isinstance(filing_analysis, dict):
        sec_takeaway = str(
            filing_analysis.get("high_level_takeaway")
            or filing_analysis.get("summary_headline")
            or ""
        ).strip()
    claim = synthesis.split("\n")[0].strip("- ").strip() if synthesis else ""
    if not claim:
        direction = {"long": "upside", "short": "downside", "pass": "unclear edge"}[recommendation]
        claim = f"{ticker or 'This ticker'} currently presents a {direction} profile over the selected horizon."

    evidence: list[str] = []
    signal_score = _safe_float(technical.get("signal_score"))
    if signal_score is not None:
        evidence.append(f"Technical signal score: {signal_score:.1f}/100")
    if mos is not None:
        evidence.append(f"DCF margin of safety: {mos:.1f}%")
    if sec_takeaway:
        evidence.append(sec_takeaway[:220])
    conviction = _safe_float(miro.get("conviction_score"))
    if conviction is not None:
        evidence.append(f"Mirofish conviction: {conviction:.0f}")
    evidence = evidence[:5] or ["Evidence is primarily inferred from available section summaries."]

    catalysts = [
        {
            "name": name,
            "date": None,
            "impact": "medium",
        }
        for name in catalysts_top3
    ]
    for i, cat in enumerate(catalyst_calendar[: len(catalysts)]):
        if cat.get("date"):
            catalysts[i]["date"] = cat.get("date")
        if cat.get("expected_impact"):
            catalysts[i]["impact"] = str(cat.get("expected_impact"))[:120]

    inferred_unique = sorted({item for item in inferred if item})
    return {
        "ic_snapshot": {
            "recommendation": recommendation,
            "horizon": "3-12 months",
            "expected_return_base_pct": round(expected_return_base, 2),
            "confidence_score": confidence_score,
            "position_size_hint_pct": position_size_hint,
            "thesis_top3": thesis_top3,
            "risks_top3": risks_top3,
            "catalysts": catalysts,
            "invalidation": invalidation,
            "inferred_fields": inferred_unique,
        },
        "scenarios": {
            "base": {
                "probability": base_p,
                "return_pct": round(expected_return_base, 2),
                "rationale": "Blend of valuation, technical setup, and sentiment baseline.",
            },
            "bull": {
                "probability": bull_p,
                "return_pct": bull_return,
                "rationale": "Multiple bullish signals reinforce with favorable catalyst follow-through.",
            },
            "bear": {
                "probability": bear_p,
                "return_pct": bear_return,
                "rationale": "Setup fails or risk factors intensify beyond current assumptions.",
            },
            "expected_value_pct": ev,
            "payoff_ratio_up_down": payoff_ratio,
            "inferred": bool(inferred_unique),
        },
        "thesis": {
            "claim": claim,
            "evidence": evidence,
            "confidence": confidence_bucket,
            "falsifiers": invalidation[:3],
        },
        "risk_register": risk_register,
        "catalyst_calendar": catalyst_calendar,
        "portfolio_fit": portfolio_fit,
        "monitoring_plan": {
            "weekly_checks": [
                "Recompute signal score and trend state.",
                "Review new SEC disclosures and management commentary.",
                "Track relative performance versus sector benchmark.",
            ],
            "monthly_checks": [
                "Re-underwrite valuation assumptions and margin-of-safety drift.",
                "Refresh position sizing against current risk budget.",
            ],
            "kill_switches": [
                "Primary thesis invalidation triggered.",
                "Aggregate risk register shifts to high-high profile.",
                "Expected value turns materially negative after scenario refresh.",
            ],
        },
        "metadata": {
            "generated_at": generated_at,
            "evidence_mode": _evidence_mode(edgar),
            "model_version": None,
        },
    }
