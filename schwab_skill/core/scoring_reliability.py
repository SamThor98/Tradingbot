"""Reliability score computation for live scans and backtest parity."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def backtest_reliability_context(*, context: str | None = None, skill_dir: Path | None = None) -> bool:
    """True when reliability should use backtest-tolerant rules (no uniform cap stack)."""
    _ = skill_dir
    if context == "backtest":
        return True
    if context == "live":
        return False
    return os.environ.get("BACKTEST_SKIP_MIROFISH", "").strip().lower() in ("1", "true", "yes", "on")


def _resolve_confidence_bucket(signal_row: dict[str, Any], skill_dir: Path | None = None) -> str:
    advisory = signal_row.get("advisory") if isinstance(signal_row.get("advisory"), dict) else {}
    bucket = str(advisory.get("confidence_bucket") or signal_row.get("advisory_confidence_bucket") or "").lower()
    if bucket in {"high", "medium", "low"}:
        return bucket

    p_raw = advisory.get("p_up_10d")
    if p_raw is None:
        p_raw = signal_row.get("p_up_calibrated")
    if p_raw is None:
        return "unknown"
    try:
        p_up = float(p_raw)
    except (TypeError, ValueError):
        return "unknown"
    from advisory_model import _confidence_bucket

    sd = skill_dir or SKILL_DIR
    return str(_confidence_bucket(p_up, sd)).lower()


def compute_reliability_score(
    signal_row: dict[str, Any],
    *,
    context: str | None = None,
    skill_dir: Path | None = None,
) -> tuple[float, list[str]]:
    """
    Compute reliability (0-100) with advisory-driven dispersion.

    In backtest context, skip penalties that apply uniformly to every row
    (missing MiroFish, yfinance fallback, non-Schwab provider). Those flat
    deductions pushed most trades below the composite cap threshold (40) and
    pinned ``composite_score`` at 55.
    """
    backtest_mode = backtest_reliability_context(context=context, skill_dir=skill_dir)
    advisory = signal_row.get("advisory") if isinstance(signal_row.get("advisory"), dict) else {}
    confidence_bucket = _resolve_confidence_bucket(signal_row, skill_dir=skill_dir)
    feature_coverage_raw = advisory.get("feature_coverage")
    if feature_coverage_raw is None:
        feature_coverage_raw = signal_row.get("advisory_feature_coverage")
    try:
        feature_coverage = float(feature_coverage_raw) if feature_coverage_raw is not None else None
    except (TypeError, ValueError):
        feature_coverage = None

    reliability = 82.0
    reasons: list[str] = []

    if confidence_bucket == "high":
        reliability += 12.0
    elif confidence_bucket == "medium":
        reliability += 4.0
    elif confidence_bucket == "low":
        reliability -= 12.0
        reasons.append("advisory_low_confidence")
    else:
        unknown_penalty = 8.0 if backtest_mode else 18.0
        reliability -= unknown_penalty
        reasons.append("advisory_missing_or_unknown")

    if not advisory:
        unavailable_penalty = 0.0 if backtest_mode else 8.0
        if unavailable_penalty:
            reliability -= unavailable_penalty
            reasons.append("advisory_unavailable")

    if feature_coverage is not None:
        coverage_nudge = _clamp((feature_coverage - 0.55) * 24.0, -12.0, 12.0)
        reliability += coverage_nudge
        if coverage_nudge >= 4.0:
            reasons.append("advisory_high_feature_coverage")
        elif coverage_nudge <= -4.0:
            reasons.append("advisory_low_feature_coverage")
    elif backtest_mode:
        p_up_raw = advisory.get("p_up_10d", signal_row.get("p_up_calibrated"))
        try:
            p_up = float(p_up_raw) if p_up_raw is not None else None
        except (TypeError, ValueError):
            p_up = None
        if p_up is not None:
            reliability += _clamp((p_up - 0.5) * 20.0, -8.0, 8.0)
            reasons.append("advisory_p_up_dispersion")
        elif advisory:
            reasons.append("advisory_coverage_unavailable")

    conviction_raw = signal_row.get("mirofish_conviction")
    conviction = None
    try:
        conviction = float(conviction_raw) if conviction_raw is not None else None
    except (TypeError, ValueError):
        conviction = None

    if conviction is None and not backtest_mode:
        reliability -= 10.0
        reasons.append("mirofish_conviction_missing")

    if not backtest_mode:
        disagreement = _safe_float(signal_row.get("mirofish_disagreement"), 0.0)
        if disagreement >= 55:
            reliability -= 14.0
            reasons.append("mirofish_high_disagreement")
        elif disagreement >= 35:
            reliability -= 8.0
            reasons.append("mirofish_medium_disagreement")

    used_fallback = bool(signal_row.get("used_fallback_data"))
    if used_fallback and not backtest_mode:
        reliability -= 15.0
        reasons.append("fallback_data_used")
    elif used_fallback and backtest_mode:
        reasons.append("backtest_fallback_data_ignored")

    if not bool(signal_row.get("data_provider_primary")) and not backtest_mode:
        reliability -= 6.0
        reasons.append("non_primary_provider")
    elif not bool(signal_row.get("data_provider_primary")) and backtest_mode:
        reasons.append("backtest_non_primary_provider_ignored")

    sec_risk_tag = str(signal_row.get("sec_risk_tag") or "unknown").lower()
    forensic_flags = list(signal_row.get("forensic_flags") or [])
    if sec_risk_tag == "high":
        reliability -= 8.0
        reasons.append("high_sec_risk_tag")
    if "beneish_manipulator" in forensic_flags:
        reliability -= 10.0
        reasons.append("forensic_beneish_manipulator")
    if "altman_distress" in forensic_flags:
        reliability -= 10.0
        reasons.append("forensic_altman_distress")

    return _clamp(reliability, 0.0, 100.0), sorted(set(reasons))


def reliability_series_from_frame(
    df: "pd.DataFrame",
    *,
    context: str = "backtest",
) -> "pd.Series":
    """Row-wise reliability for offline trade/candidate frames."""
    import pandas as pd

    scores: list[float] = []
    for row in df.to_dict(orient="records"):
        rel, _ = compute_reliability_score(row, context=context)
        scores.append(rel)
    return pd.Series(scores, index=df.index, dtype=float)
