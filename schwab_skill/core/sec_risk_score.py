"""SEC risk tag ↔ numeric score.

The advisory model stores a low tag as +1 and a high tag as -1. Zero is both
a measured medium tag and a missing tag, so readers treat zero as unknown
instead of inventing a medium penalty.
"""

from __future__ import annotations


def risk_tag_to_score(tag: str | None) -> float:
    t = (tag or "").strip().lower()
    if t == "low":
        return 1.0
    if t == "medium":
        return 0.0
    if t == "high":
        return -1.0
    return 0.0


def sec_risk_tag_from_score(score: float | None) -> str:
    """Invert :func:`risk_tag_to_score`.

    ``+1`` is low risk and ``-1`` is high risk. Values near zero stay
    ``unknown`` because the encoder uses ``0`` for both medium and missing.
    """
    if score is None:
        return "unknown"
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if value != value:  # NaN
        return "unknown"
    if value >= 0.5:
        return "low"
    if value <= -0.5:
        return "high"
    return "unknown"
