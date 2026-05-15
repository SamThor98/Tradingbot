"""
Heuristic composite-weight hints from realized ``.trade_outcomes.json``.

This is not a full calibration engine — it summarizes average return by signal
score bucket so operators can spot gross misalignment. Use outputs as priors
when tuning ``signal_scanner`` / advisory weights manually.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent


def summarize_outcomes_by_score_band(
    skill_dir: Path | None = None,
    *,
    bands: tuple[tuple[float, float], ...] = ((0, 50), (50, 65), (65, 80), (80, 101)),
) -> dict[str, Any]:
    path = (skill_dir or SKILL_DIR) / ".trade_outcomes.json"
    if not path.exists():
        return {"ok": False, "reason": "no_trade_outcomes_file"}

    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": f"parse_error:{type(exc).__name__}"}

    if not isinstance(rows, list):
        return {"ok": False, "reason": "invalid_json_shape"}

    buckets: dict[str, list[float]] = {f"{lo}-{hi}": [] for lo, hi in bands}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            score = float(row.get("signal_score") or row.get("score") or 0)
            ret = float(row.get("return_pct") or row.get("pnl_pct") or 0)
        except (TypeError, ValueError):
            continue
        for lo, hi in bands:
            if lo <= score < hi:
                buckets[f"{lo}-{hi}"].append(ret)
                break

    summary: dict[str, Any] = {"ok": True, "bands": {}}
    for key, vals in buckets.items():
        if not vals:
            summary["bands"][key] = {"count": 0, "avg_return_pct": None}
        else:
            summary["bands"][key] = {
                "count": len(vals),
                "avg_return_pct": round(sum(vals) / len(vals), 6),
            }
    return summary


def suggest_composite_tilt(summary: dict[str, Any]) -> dict[str, float]:
    """Map band summary to coarse multiplicative tilts (centered at 1.0)."""
    if not summary.get("ok"):
        return {"edge_tilt": 1.0, "reliability_tilt": 1.0}
    bands = summary.get("bands") or {}
    hi = bands.get("65-80") or {}
    mid = bands.get("50-65") or {}
    hi_avg = hi.get("avg_return_pct")
    mid_avg = mid.get("avg_return_pct")
    tilt = 1.0
    if isinstance(hi_avg, (int, float)) and isinstance(mid_avg, (int, float)) and mid_avg not in (0, None):
        edge = float(hi_avg) - float(mid_avg)
        tilt = max(0.85, min(1.15, 1.0 + edge * 5.0))
    return {"edge_tilt": round(tilt, 4), "reliability_tilt": 1.0}
