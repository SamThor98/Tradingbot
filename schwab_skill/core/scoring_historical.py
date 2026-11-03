"""Historical blend ranker — weights fitted on realized 40d forward returns.

The blend is deliberately small: every feature must be computable *both* offline
from the frozen research panels and at scan time from a live signal row, so the
score the operator sees is the score the weights were fitted on. Raw extraction
and normalization both live here so ``scripts/fit_historical_rank_blend.py`` and
the scanner cannot drift.

Normalization uses **fitted** per-feature bounds stored in the artifact rather
than the fixed caps used by ``composite_score``. Those caps are tuned for a
score, not a ranker: ``TREND_PCT_CAP`` (0.20) sits near the median trend of the
candidate population, so reusing it ties roughly half the book at 100 and
destroys resolution in exactly the range where selection happens.

Weights come from ``research_store/models/historical_rank_blend.json`` (written
by the fitter). Risk caps are reused from rank v2 — safety never adds score.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.scoring_composite import PTS_VOLUME_CAP, breakout_volume_points
from core.scoring_rank_v2 import apply_rank_v2_risk_caps

LOG = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent

DEFAULT_ARTIFACT_PATH = "research_store/models/historical_rank_blend.json"

FEATURE_NAMES: tuple[str, ...] = (
    "trend_norm",
    "volume_norm",
    "prox_52w_norm",
    "ml_expected_return_norm",
)

# Raw-value key each normalized feature is derived from.
RAW_KEYS: dict[str, str] = {
    "trend_norm": "close_vs_sma200_pct",
    "volume_norm": "volume_points",
    "prox_52w_norm": "pct_from_52w_high",
    "ml_expected_return_norm": "expected_return_40d",
}

# Fallback bounds for artifacts that predate fitted bounds (or are hand written).
FALLBACK_BOUNDS: dict[str, tuple[float, float]] = {
    "close_vs_sma200_pct": (0.0, 0.20),
    "volume_points": (0.0, PTS_VOLUME_CAP),
    "pct_from_52w_high": (0.70, 1.00),
    "expected_return_40d": (-0.05, 0.11),
}


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def volume_points(
    *,
    avg_vcp_volume_ratio: Any = None,
    pts_volume: Any = None,
    volume_ratio: Any = None,
    latest_volume: Any = None,
    avg_vol_50: Any = None,
) -> float | None:
    """VCP dry-up or breakout expansion, whichever is stronger, on the 0-20 scale.

    Mirrors ``resolve_rank_volume_points`` but also accepts the raw VCP ratio,
    which is what the research panel carries.
    """
    dry_up = _safe_float(pts_volume)
    if dry_up is None:
        ratio = _safe_float(avg_vcp_volume_ratio)
        if ratio is not None:
            # Same mapping as compute_signal_components: lower ratio = higher.
            dry_up = max(0.0, PTS_VOLUME_CAP - ratio * PTS_VOLUME_CAP)

    expansion_ratio = _safe_float(volume_ratio)
    if expansion_ratio is not None:
        breakout_pts = breakout_volume_points(expansion_ratio, 1.0)
    else:
        breakout_pts = breakout_volume_points(latest_volume, avg_vol_50)

    if dry_up is None and breakout_pts <= 0.0:
        return None
    return max(_clamp(dry_up or 0.0, 0.0, PTS_VOLUME_CAP), breakout_pts)


def raw_features_from_signal_row(signal_row: dict[str, Any]) -> dict[str, float | None]:
    """Un-normalized blend inputs for one live signal row."""
    row = signal_row or {}
    comps = row.get("score_components") if isinstance(row.get("score_components"), dict) else {}
    prob_rank = row.get("prob_rank") if isinstance(row.get("prob_rank"), dict) else {}

    trend_pct = _safe_float(row.get("close_vs_sma200_pct"))
    if trend_pct is None:
        price = _safe_float(row.get("price")) or 0.0
        sma_200 = _safe_float(row.get("sma_200")) or 0.0
        trend_pct = (price / sma_200) - 1.0 if price > 0 and sma_200 > 0 else None

    return {
        "close_vs_sma200_pct": trend_pct,
        "volume_points": volume_points(
            pts_volume=_first_present(comps.get("pts_volume"), row.get("pts_volume")),
            avg_vcp_volume_ratio=_first_present(
                comps.get("avg_vcp_volume_ratio"), row.get("avg_vcp_volume_ratio")
            ),
            volume_ratio=row.get("volume_ratio"),
            latest_volume=row.get("latest_volume"),
            avg_vol_50=row.get("avg_vol_50"),
        ),
        "pct_from_52w_high": _safe_float(
            _first_present(comps.get("pct_from_52w_high"), row.get("pct_from_52w_high"))
        ),
        "expected_return_40d": _safe_float(
            _first_present(row.get("expected_return_40d"), prob_rank.get("expected_return_40d"))
        ),
    }


def normalize_feature(name: str, raw: Any, bounds: dict[str, Any] | None) -> float | None:
    """Linear clip of one raw value onto 0-100 using fitted (or fallback) bounds."""
    value = _safe_float(raw)
    if value is None:
        return None
    raw_key = RAW_KEYS.get(name, name)
    span = (bounds or {}).get(raw_key)
    lo = hi = None
    if isinstance(span, dict):
        lo, hi = _safe_float(span.get("lo")), _safe_float(span.get("hi"))
    if lo is None or hi is None or hi <= lo:
        fallback = FALLBACK_BOUNDS.get(raw_key)
        if fallback is None:
            return None
        lo, hi = fallback
    return _clamp(((value - lo) / (hi - lo)) * 100.0)


def features_from_signal_row(
    signal_row: dict[str, Any],
    *,
    bounds: dict[str, Any] | None = None,
) -> dict[str, float | None]:
    """Normalized blend features for one live signal row."""
    raw = raw_features_from_signal_row(signal_row)
    return {name: normalize_feature(name, raw.get(RAW_KEYS[name]), bounds) for name in FEATURE_NAMES}


def cohort_percentile(sorted_values: list[float], value: float) -> float:
    """Mid-rank percentile (0-100) of ``value`` within an ascending cohort.

    Mirrors ``percentileRank`` in webapp/static/modules/signalScores.js so the
    backend and the table agree on the number.
    """
    if not sorted_values:
        return 0.0
    below = sum(1 for item in sorted_values if item < value)
    equal = sum(1 for item in sorted_values if item == value)
    return ((below + equal / 2.0) / len(sorted_values)) * 100.0


def load_blend_artifact(
    skill_dir: Path | None = None,
    override: str | None = None,
) -> dict[str, Any] | None:
    """Load the fitted blend artifact, or None when it is missing/invalid."""
    sd = skill_dir or SKILL_DIR
    raw = str(override or "").strip() or DEFAULT_ARTIFACT_PATH
    path = Path(raw)
    if not path.is_absolute():
        path = Path(sd) / path
    if not path.is_file():
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        LOG.warning("Historical rank blend unreadable at %s: %s", path, exc)
        return None
    weights = artifact.get("weights")
    if not isinstance(weights, dict) or not weights:
        LOG.warning("Historical rank blend has no weights: %s", path)
        return None
    artifact["_path"] = str(path)
    return artifact


def compute_historical_rank(
    signal_row: dict[str, Any],
    artifact: dict[str, Any],
    *,
    skill_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Blend fitted weights into a 0-100 score, then apply rank-v2 risk caps.

    Returns None when no weighted feature is available for the row (never a
    zero score, so unscored rows stay distinguishable from genuinely weak ones).
    """
    weights_raw = artifact.get("weights") or {}
    weights = {k: float(v) for k, v in weights_raw.items() if _safe_float(v) is not None}
    if not weights:
        return None

    bounds = artifact.get("feature_bounds") if isinstance(artifact.get("feature_bounds"), dict) else {}
    feats = features_from_signal_row(signal_row, bounds=bounds)
    used: dict[str, float] = {}
    missing: list[str] = []
    weighted_sum = 0.0
    weight_total = 0.0
    for name, weight in weights.items():
        value = feats.get(name)
        if value is None:
            missing.append(name)
            continue
        used[name] = round(float(value), 2)
        weighted_sum += weight * float(value)
        weight_total += weight

    if weight_total <= 0.0:
        return None

    # Renormalize over available weights so a missing feature shifts the score
    # toward the remaining evidence instead of dragging it to zero.
    blend = _clamp(weighted_sum / weight_total)
    capped = apply_rank_v2_risk_caps(
        blend,
        reliability_score=_safe_float(signal_row.get("reliability_score")),
        execution_score=_safe_float(signal_row.get("execution_score")),
        sec_risk_tag=str(signal_row.get("sec_risk_tag") or "unknown"),
        forensic_flags=list(signal_row.get("forensic_flags") or []),
    )

    reasons = [f"{name} {value:.1f} @ w{weights[name]:.2f}" for name, value in used.items()]
    if missing:
        reasons.append(f"missing: {', '.join(sorted(missing))}")
    if capped < blend - 0.005:
        reasons.append(f"risk cap {blend:.1f} -> {capped:.1f}")

    return {
        "score": round(float(capped), 2),
        "blend_raw": round(float(blend), 2),
        "features": used,
        "missing_features": sorted(missing),
        "reasons": reasons,
        "model_id": artifact.get("model_id"),
        "coverage": round(weight_total / sum(weights.values()), 4) if sum(weights.values()) else None,
    }
