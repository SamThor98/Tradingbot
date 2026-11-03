"""Operator trust score: 0–100, higher means a better expected realized outcome.

The only feature that earned this job on the Stage 2 book is inverted
52-week extension. ``pts_52w`` runs 0–40, closer to the high scoring higher.
The trust score maps that span onto 0–100 so 100 is the stage-2 floor
(least extended) and 0 is at the high. Safety caps apply after the map.
Fundamentals did not earn a weight: free cash flow was the only stable
group, and it was too weak to add on top of extension.

This score does not write ``sort_score`` and does not change
``SCAN_LIVE_SORT_KEY``. The day's top 5 by inverted extension misses the
1.20 profit-factor mean, so the number is a readout, not the live order.
"""

from __future__ import annotations

from typing import Any

from core.scoring_rank_v2 import apply_rank_v2_risk_caps

PTS_52W_SCALE = 40.0

# Published on control_legacy_aug. Do not edit these figures without a new book.
TRUST_BOOK = "control_legacy_aug"
TRUST_LABEL = "net_return"
TRUST_BASIS = "inv_pts_52w"
TRUST_EVIDENCE = (
    "Higher means less extended. On the Stage 2 control book, a p76 cut on "
    "inverted 52-week extension is PF 1.4212 / worst era 1.0881. "
    "The day's top 5 by this score is PF 1.1911 / worst 1.0227, so it does not "
    "reorder the live book. Fundamentals did not earn a weight."
)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def trust_score_from_pts(pts_52w: float) -> float:
    """Map extension points onto 0–100. Higher is less extended."""
    return round(_clamp((1.0 - (float(pts_52w) / PTS_52W_SCALE)) * 100.0), 2)


def _sec_tag(signal_row: dict[str, Any]) -> str:
    tag = signal_row.get("sec_risk_tag")
    if tag:
        return str(tag)
    risk = _optional_float(signal_row.get("sec_risk_score"))
    if risk is None:
        return "unknown"
    from core.sec_risk_score import sec_risk_tag_from_score

    return sec_risk_tag_from_score(risk)


def _forensic_flags(signal_row: dict[str, Any]) -> list[str]:
    flags = signal_row.get("forensic_flags")
    if not isinstance(flags, list):
        return []
    return [str(flag) for flag in flags]


def trust_cap_reasons(signal_row: dict[str, Any]) -> list[str]:
    """Name the safety caps that can lower the score. Missing inputs do not cap."""
    reasons: list[str] = []
    reliability = _optional_float(signal_row.get("reliability_score"))
    execution = _optional_float(signal_row.get("execution_score"))
    if reliability is not None and reliability < 40.0:
        reasons.append("reliability")
    if execution is not None and execution < 45.0:
        reasons.append("execution")
    tag = _sec_tag(signal_row).lower()
    flags = _forensic_flags(signal_row)
    if tag == "high" or "beneish_manipulator" in flags or "altman_distress" in flags:
        reasons.append("forensic")
    return reasons


def trust_score_block(signal_row: dict[str, Any], *, pts_52w: float) -> dict[str, Any]:
    """Stamp fields for one row that already has extension points."""
    raw = trust_score_from_pts(pts_52w)
    reasons = trust_cap_reasons(signal_row)
    capped = apply_rank_v2_risk_caps(
        raw,
        reliability_score=_optional_float(signal_row.get("reliability_score")),
        execution_score=_optional_float(signal_row.get("execution_score")),
        sec_risk_tag=_sec_tag(signal_row),
        forensic_flags=_forensic_flags(signal_row),
    )
    return {
        "trust_score": capped,
        "trust_score_raw": raw,
        "trust_score_basis": TRUST_BASIS,
        "trust_score_label": TRUST_LABEL,
        "trust_score_book": TRUST_BOOK,
        "trust_score_evidence": TRUST_EVIDENCE,
        "trust_score_caps": reasons,
        "trust_score_mode": "shadow",
    }
