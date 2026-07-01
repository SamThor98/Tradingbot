"""Summarize on-disk calibration files from a skill directory (SaaS worker temp dir)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SELF_STUDY_KEYS = (
    "suggested_min_conviction",
    "round_trips_count",
    "win_rate",
    "avg_return_pct",
    "min_round_trips_met",
    "hypothesis_calibration",
    "last_run",
    "updated_at",
)


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_self_study(raw: dict[str, Any]) -> dict[str, Any]:
    out = {k: raw.get(k) for k in _SELF_STUDY_KEYS if k in raw}
    trips = raw.get("round_trips_count")
    if trips is None and raw.get("round_trips") is not None:
        trips = raw.get("round_trips")
    if trips is not None:
        out["round_trips_count"] = trips
        out["round_trips"] = trips
    return out


def build_calibration_snapshot(skill_dir: Path) -> dict[str, Any]:
    """
    Compact snapshot for AppState / API. Safe on missing or huge files.
    """
    out: dict[str, Any] = {"skill_dir_tag": skill_dir.name[:32]}
    ss = _read_json(skill_dir / ".self_study.json")
    if isinstance(ss, dict):
        out["self_study"] = _normalize_self_study(ss)
        hc = ss.get("hypothesis_calibration")
        if isinstance(hc, dict):
            out["hypothesis_calibration"] = hc
    else:
        out["self_study"] = None

    ledger_path = skill_dir / ".hypothesis_ledger.json"
    hl = _read_json(ledger_path)
    if isinstance(hl, list):
        n = len(hl)
        tail = hl[-50:] if n > 50 else hl
        sources: dict[str, int] = {}
        for row in tail:
            if not isinstance(row, dict):
                continue
            src = str(row.get("source") or "unknown")
            sources[src] = sources.get(src, 0) + 1
        out["hypothesis_ledger"] = {
            "row_count": n,
            "recent_source_counts": sources,
            "truncated": n > 50,
        }
    elif isinstance(hl, dict):
        out["hypothesis_ledger"] = {"row_count": 1, "shape": "object"}
    else:
        out["hypothesis_ledger"] = None

    if not out.get("self_study") and not out.get("hypothesis_ledger"):
        out["empty"] = True
        out["hint"] = (
            "No calibration snapshot yet. Run self-study or enable hypothesis ledger scoring."
        )

    return out
