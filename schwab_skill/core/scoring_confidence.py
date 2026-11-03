"""Setup confidence — shadow survival readout for the scan table.

Answers a narrower question than the other scores: of the setups that look like
this one at entry, how many survived to the 21-40 day window instead of being
trailed out inside 20 days? That cohort split is where the realized book's P&L
comes from.

Every feature enters with a fitted sign. On the `control_legacy_aug` book those
signs are all negative -- steeper trend and greater extension at entry are
associated with *worse* realized outcomes, not better. The artifact carries the
signs so this module never hardcodes that direction.

Shadow only. `CONFIDENCE_RANK_MODE=live` is refused: walk-forward showed the
blend clears the promotion floors but does not beat a single already-known
variable (`pts_52w`) by a meaningful margin. See
`validation_artifacts/scan_confidence/report.md`.

The same normalization runs offline (research screen) and at scan time, so the
number on the row is the number the counterfactual scored.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.scoring_rank_v2 import apply_rank_v2_risk_caps

LOG = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ARTIFACT_PATH = "research_store/models/scan_confidence.json"
ARTIFACT_SCHEMA = "scan_confidence_v1"

# Never inputs: outcomes and identifiers.
FORBIDDEN_INPUTS: frozenset[str] = frozenset(
    {
        "net_return",
        "hold_days",
        "exit_reason",
        "ret_40d_fwd",
        "ret_20d_fwd",
        "ret_10d_fwd",
        "ret_5d_fwd",
        "expected_return_40d",
        "ticker",
        "y_win",
    }
)

# Live signal rows spell a few registry names differently.
LIVE_ALIASES: dict[str, tuple[str, ...]] = {
    "close_vs_sma200_pct": ("close_vs_sma200_pct", "dist_sma200_pct"),
    "dist_sma200_pct": ("dist_sma200_pct", "close_vs_sma200_pct"),
}


def load_confidence_artifact(
    skill_dir: Path | None = None,
    override: str | None = None,
) -> dict[str, Any] | None:
    """Load the fitted artifact. Missing or malformed returns None (fail-closed)."""
    base = Path(skill_dir) if skill_dir is not None else SKILL_DIR
    path = Path(override).expanduser() if override and str(override).strip() else Path(DEFAULT_ARTIFACT_PATH)
    if not path.is_absolute():
        path = base / path
    if not path.is_file():
        return None
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOG.warning("Scan confidence artifact unreadable at %s: %s", path, exc)
        return None
    if not isinstance(artifact, dict) or artifact.get("schema") != ARTIFACT_SCHEMA:
        LOG.warning("Scan confidence artifact has unexpected schema: %s", path)
        return None
    features = artifact.get("features")
    if not isinstance(features, list) or not features:
        LOG.warning("Scan confidence artifact has no features: %s", path)
        return None
    for feat in features:
        name = str((feat or {}).get("name") or "")
        if name in FORBIDDEN_INPUTS:
            LOG.error("Scan confidence artifact references a label column: %s", name)
            return None
    return artifact


def _lookup(name: str, *sources: dict[str, Any] | None) -> float | None:
    """First finite value for ``name`` (or a known alias) across the sources."""
    keys = LIVE_ALIASES.get(name, (name,))
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            if key not in source:
                continue
            raw = source.get(key)
            if raw is None or isinstance(raw, bool):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value == value and value not in (float("inf"), float("-inf")):
                return value
    return None


def compute_setup_confidence(
    artifact: dict[str, Any],
    *sources: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Score one row from a registry feature row and/or a live signal row.

    Returns ``None`` when no feature resolved, so an unscored row reads as
    "unknown" rather than "zero confidence".
    """
    features = artifact.get("features") or []
    contributions: list[float] = []
    used: list[str] = []
    missing: list[str] = []
    for feat in features:
        name = str((feat or {}).get("name") or "")
        bounds = (feat or {}).get("bounds") or []
        if not name or len(bounds) != 2:
            continue
        value = _lookup(name, *sources)
        if value is None:
            missing.append(name)
            continue
        lo, hi = float(bounds[0]), float(bounds[1])
        if hi <= lo:
            missing.append(name)
            continue
        norm = (value - lo) / (hi - lo)
        norm = max(0.0, min(1.0, norm))
        sign = float((feat or {}).get("sign") or 1.0)
        contributions.append(norm if sign > 0 else (1.0 - norm))
        used.append(name)

    if not contributions:
        return None

    score = 100.0 * sum(contributions) / len(contributions)
    coverage = len(used) / max(1, len(features))
    reasons: list[str] = []
    if missing:
        reasons.append(f"missing_features:{','.join(sorted(missing))}")
    if coverage < 1.0:
        reasons.append(f"partial_coverage_{coverage:.2f}")
    return {
        "setup_confidence": round(score, 2),
        "setup_confidence_features": used,
        "setup_confidence_coverage": round(coverage, 4),
        "setup_confidence_model_id": artifact.get("model_id"),
        "setup_confidence_reasons": reasons,
    }


def score_setup_confidence(
    signal: dict[str, Any],
    artifact: dict[str, Any],
    *,
    feature_row: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Score a live signal row, applying the shared rank-v2 safety caps.

    Safety is caps only, never additive points -- same contract as
    ``core/scoring_rank_v2.py`` and the analog overlay.
    """
    block = compute_setup_confidence(artifact, feature_row, signal)
    if block is None:
        return None
    raw = float(block["setup_confidence"])
    capped = apply_rank_v2_risk_caps(
        raw,
        reliability_score=_lookup("reliability_score", signal),
        execution_score=_lookup("execution_score", signal),
        sec_risk_tag=str(signal.get("sec_risk_tag") or "unknown"),
        forensic_flags=list(signal.get("forensic_flags") or []),
    )
    if capped < raw:
        block["setup_confidence"] = round(float(capped), 2)
        block["setup_confidence_reasons"] = [
            *block.get("setup_confidence_reasons", []),
            "risk_capped",
        ]
    return block
