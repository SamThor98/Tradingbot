"""Validation-driven rank v2 — weights components by offline 40d IC evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent

PTS_VOLUME_CAP = 20.0
PTS_MIROFISH_CAP = 15.0
PTS_52W_CAP = 40.0


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _signal_for_rank(
    *,
    signal_score: float,
    pts_52w: float,
    exclude_52w: bool,
) -> float:
    """Use signal minus harmful 52w component for rank (40d validation)."""
    base = float(signal_score)
    if exclude_52w:
        base = base - float(pts_52w)
    return _clamp(base)


def apply_rank_v2_risk_caps(
    rank_score: float,
    *,
    reliability_score: float | None,
    execution_score: float | None,
    sec_risk_tag: str | None,
    forensic_flags: list[str] | None,
) -> float:
    """Mirror v1 rank hard caps — safety layer on top of the v2 blend."""
    capped = float(rank_score)
    rel = float(reliability_score) if reliability_score is not None else 100.0
    exe = float(execution_score) if execution_score is not None else 100.0
    if rel < 40.0:
        capped = min(capped, 55.0)
    if exe < 45.0:
        capped = min(capped, 58.0)
    tag = str(sec_risk_tag or "unknown").lower()
    flags = list(forensic_flags or [])
    if tag == "high" or "beneish_manipulator" in flags or "altman_distress" in flags:
        capped = min(capped, 45.0)
    return round(_clamp(capped), 2)


def compute_rank_score_v2(
    *,
    signal_score: float,
    pts_volume: float,
    pts_mirofish: float,
    pts_52w: float = 0.0,
    exclude_52w: bool = True,
    signal_weight: float = 0.35,
    volume_weight: float = 0.50,
    mirofish_weight: float = 0.15,
    reliability_score: float | None = None,
    execution_score: float | None = None,
    sec_risk_tag: str | None = None,
    forensic_flags: list[str] | None = None,
) -> float:
    """Blend rank inputs using validation-tuned weights, then apply risk caps."""
    sig = _signal_for_rank(signal_score=signal_score, pts_52w=pts_52w, exclude_52w=exclude_52w)
    vol_norm = _clamp((float(pts_volume) / PTS_VOLUME_CAP) * 100.0)
    miro_norm = _clamp((float(pts_mirofish) / PTS_MIROFISH_CAP) * 100.0)
    blend = (
        (float(signal_weight) * sig)
        + (float(volume_weight) * vol_norm)
        + (float(mirofish_weight) * miro_norm)
    )
    return apply_rank_v2_risk_caps(
        _clamp(blend),
        reliability_score=reliability_score,
        execution_score=execution_score,
        sec_risk_tag=sec_risk_tag,
        forensic_flags=forensic_flags,
    )


def rank_v2_from_signal_row(signal_row: dict[str, Any], skill_dir: Path | None = None) -> float:
    from config import (
        get_rank_v2_exclude_52w,
        get_rank_v2_mirofish_weight,
        get_rank_v2_signal_weight,
        get_rank_v2_volume_weight,
    )
    from core.scoring_composite import resolve_rank_volume_points

    sd = skill_dir or SKILL_DIR
    comps = signal_row.get("score_components") if isinstance(signal_row.get("score_components"), dict) else {}
    flags = signal_row.get("forensic_flags")
    if not isinstance(flags, list):
        flags = []
    sec_tag = signal_row.get("sec_risk_tag")
    if not sec_tag and signal_row.get("sec_risk_score") is not None:
        try:
            risk = float(signal_row.get("sec_risk_score"))
            sec_tag = "high" if risk >= 0.67 else ("medium" if risk >= 0.33 else "low")
        except (TypeError, ValueError):
            sec_tag = "unknown"

    rel = signal_row.get("reliability_score")
    exe = signal_row.get("execution_score")
    try:
        rel_f = float(rel) if rel is not None else None
    except (TypeError, ValueError):
        rel_f = None
    try:
        exe_f = float(exe) if exe is not None else None
    except (TypeError, ValueError):
        exe_f = None

    return compute_rank_score_v2(
        signal_score=float(signal_row.get("signal_score") or 0.0),
        pts_volume=resolve_rank_volume_points(
            comps.get("pts_volume") or signal_row.get("pts_volume"),
            latest_volume=signal_row.get("latest_volume"),
            avg_vol_50=signal_row.get("avg_vol_50"),
        ),
        pts_mirofish=float(comps.get("pts_mirofish") or signal_row.get("pts_mirofish") or 0.0),
        pts_52w=float(comps.get("pts_52w") or signal_row.get("pts_52w") or 0.0),
        exclude_52w=bool(get_rank_v2_exclude_52w(sd)),
        signal_weight=float(get_rank_v2_signal_weight(sd)),
        volume_weight=float(get_rank_v2_volume_weight(sd)),
        mirofish_weight=float(get_rank_v2_mirofish_weight(sd)),
        reliability_score=rel_f,
        execution_score=exe_f,
        sec_risk_tag=str(sec_tag or "unknown"),
        forensic_flags=flags,
    )


def enrich_dataframe_rank_v2(df, skill_dir: Path | None = None):
    """Add ``rank_score_v2`` column for offline validation."""

    out = df.copy()
    rows: list[float] = []
    for _, row in out.iterrows():
        pack = row.to_dict()
        pack["score_components"] = {
            "pts_52w": row.get("pts_52w"),
            "pts_volume": row.get("pts_volume"),
            "pts_mirofish": row.get("pts_mirofish"),
        }
        rows.append(rank_v2_from_signal_row(pack, skill_dir=skill_dir))
    out["rank_score_v2"] = rows
    return out
