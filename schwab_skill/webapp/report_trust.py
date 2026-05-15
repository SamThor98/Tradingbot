from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _make_fact(fact_id: str, statement: str, source: str) -> dict[str, Any]:
    return {
        "id": fact_id,
        "statement": statement,
        "source": source,
    }


def _build_verified_facts(report: dict[str, Any]) -> list[dict[str, Any]]:
    technical = report.get("technical") or {}
    dcf = report.get("dcf") or {}
    health = report.get("health") or {}
    edgar = report.get("edgar") or {}
    miro = report.get("mirofish") or {}

    facts: list[dict[str, Any]] = []
    facts.append(
        _make_fact(
            "f_technical_signal",
            f"Technical signal score is {technical.get('signal_score', 'n/a')}.",
            "technical",
        )
    )
    facts.append(
        _make_fact(
            "f_stage2_vcp",
            f"Stage 2={bool(technical.get('stage_2', False))}, VCP={bool(technical.get('vcp', False))}.",
            "technical",
        )
    )
    facts.append(
        _make_fact(
            "f_dcf_mos",
            f"DCF margin of safety is {dcf.get('margin_of_safety', 'n/a')}%.",
            "dcf",
        )
    )
    facts.append(
        _make_fact(
            "f_health_flags",
            f"Health flags count is {len(_as_list(health.get('flags')))}.",
            "health",
        )
    )
    facts.append(
        _make_fact(
            "f_edgar_risk_tag",
            f"EDGAR risk tag is {edgar.get('risk_tag', 'unknown')}.",
            "edgar",
        )
    )
    facts.append(
        _make_fact(
            "f_mirofish_conviction",
            f"MiroFish conviction score is {miro.get('conviction_score', 'n/a')}.",
            "mirofish",
        )
    )
    return facts


def _build_analyst_take(report_v2: dict[str, Any] | None, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ic = (report_v2 or {}).get("ic_snapshot") or {}
    thesis = (report_v2 or {}).get("thesis") or {}
    blocks: list[dict[str, Any]] = []
    top_points = _as_list(ic.get("top_thesis_points"))
    recommendation = str(ic.get("recommendation") or "pass").upper()
    confidence = str(ic.get("confidence_label") or "UNKNOWN")
    blocks.append(
        {
            "id": "a_recommendation",
            "text": f"Recommendation is {recommendation} with {confidence} confidence.",
            "citation_ids": ["f_technical_signal", "f_dcf_mos", "f_mirofish_conviction"],
        }
    )
    if top_points:
        blocks.append(
            {
                "id": "a_top_thesis",
                "text": str(top_points[0]),
                "citation_ids": ["f_stage2_vcp", "f_dcf_mos"],
            }
        )
    if thesis.get("claim"):
        blocks.append(
            {
                "id": "a_claim",
                "text": str(thesis.get("claim")),
                "citation_ids": ["f_technical_signal", "f_edgar_risk_tag"],
            }
        )
    # Remove empty text blocks.
    return [b for b in blocks if str(b.get("text") or "").strip()]


def _build_hypotheses(report_v2: dict[str, Any] | None) -> list[dict[str, Any]]:
    ic = (report_v2 or {}).get("ic_snapshot") or {}
    risks = _as_list(ic.get("top_risks"))
    out: list[dict[str, Any]] = []
    for idx, risk in enumerate(risks[:3], start=1):
        out.append(
            {
                "id": f"h_{idx}",
                "text": str(risk),
                "promotion_rule": "requires_new_supporting_facts",
                "status": "tentative",
            }
        )
    return out


def _source_freshness_status(generated_at: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    dt = _parse_iso_dt(generated_at)
    if dt is None:
        return {
            "ok": False,
            "policy": "source_specific",
            "detail": "missing_generated_at",
        }
    age_hours = max(0.0, (now - dt.astimezone(UTC)).total_seconds() / 3600.0)
    # v1 simplified source-specific freshness proxy.
    ok = age_hours <= 48.0
    return {
        "ok": ok,
        "policy": "source_specific",
        "age_hours": round(age_hours, 2),
    }


def build_report_trust_payload(report: dict[str, Any]) -> dict[str, Any]:
    report_v2 = report.get("report_v2") if isinstance(report, dict) else None
    verified_facts = _build_verified_facts(report)
    analyst_take = _build_analyst_take(report_v2, verified_facts)
    hypotheses = _build_hypotheses(report_v2)

    cited_blocks = 0
    for block in analyst_take:
        if _as_list(block.get("citation_ids")):
            cited_blocks += 1
    citation_completeness = (
        cited_blocks / max(1, len(analyst_take))
    )

    health_flags = _as_list((report.get("health") or {}).get("flags"))
    edgar_risk = str((report.get("edgar") or {}).get("risk_tag") or "").lower()
    unresolved_conflicts = len(health_flags)
    if edgar_risk in {"high", "elevated"}:
        unresolved_conflicts += 1
    unresolved_conflict_ratio = unresolved_conflicts / max(1, len(verified_facts))

    conviction = _safe_float((report.get("mirofish") or {}).get("conviction_score"))
    signal_score = _safe_float((report.get("technical") or {}).get("signal_score"))
    confidence_components: list[float] = []
    if conviction is not None:
        confidence_components.append(max(0.0, min(100.0, (conviction + 100.0) / 2.0)))
    if signal_score is not None:
        confidence_components.append(max(0.0, min(100.0, signal_score)))
    if confidence_components:
        data_confidence = sum(confidence_components) / len(confidence_components) / 100.0
    else:
        data_confidence = 0.0

    freshness = _source_freshness_status(report.get("generated_at"))
    trust_gates = {
        "data_confidence_floor": 0.80,
        "citation_completeness_floor": 0.95,
        "max_unresolved_conflict_ratio": 0.05,
        "allow_documented_overrides": True,
    }
    overrides = {
        "count": 0,
        "documented": True,
    }
    trusted = (
        data_confidence >= trust_gates["data_confidence_floor"]
        and citation_completeness >= trust_gates["citation_completeness_floor"]
        and unresolved_conflict_ratio <= trust_gates["max_unresolved_conflict_ratio"]
        and bool(freshness.get("ok"))
        and overrides["documented"]
    )

    return {
        "trust_status": "trusted" if trusted else "needs_review",
        "trusted": trusted,
        "data_confidence": round(data_confidence, 4),
        "citation_completeness": round(citation_completeness, 4),
        "unresolved_conflict_count": int(unresolved_conflicts),
        "unresolved_conflict_ratio": round(unresolved_conflict_ratio, 4),
        "freshness_status": freshness,
        "override_summary": overrides,
        "trust_gates": trust_gates,
        "verified_facts": verified_facts,
        "analyst_take": analyst_take,
        "hypotheses": hypotheses,
    }

