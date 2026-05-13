"""JSON persistence for execution safety metrics and exit-manager state.

Extracted from ``execution`` to keep guardrail/order logic readable while preserving
exact file shapes and rollup behavior used by dashboards and validators.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from _io_utils import atomic_write_json

LOG = logging.getLogger(__name__)

_METRICS_FILE = "execution_safety_metrics.json"
_EXIT_MANAGER_STATE_FILE = ".exit_manager_state.json"

SKILL_DIR = Path(__file__).resolve().parent


def _metrics_path(skill_dir: Path) -> Path:
    return skill_dir / _METRICS_FILE


def _load_execution_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"days": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("days"), dict):
            return data
    except Exception as exc:
        LOG.warning("Ignoring unreadable execution metrics file %s: %s", path, exc)
    return {"days": {}}


def _save_execution_metrics(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data, indent=2)


def _record_execution_metric(
    skill_dir: Path,
    event: str,
    reason: str | None = None,
) -> None:
    today = date.today().isoformat()
    path = _metrics_path(skill_dir)
    data = _load_execution_metrics(path)
    days = data.setdefault("days", {})
    day_bucket = days.setdefault(today, {"events": {}, "reasons": {}})
    events = day_bucket.setdefault("events", {})
    events[event] = int(events.get(event, 0) or 0) + 1
    if reason:
        reasons = day_bucket.setdefault("reasons", {})
        key = reason.strip()[:120] or "unknown"
        reasons[key] = int(reasons.get(key, 0) or 0) + 1

    # Keep a rolling 45-day window so the metrics file stays compact.
    cutoff = date.today() - timedelta(days=45)
    stale = [k for k in days.keys() if k < cutoff.isoformat()]
    for k in stale:
        days.pop(k, None)
    _save_execution_metrics(path, data)


def get_execution_safety_summary(
    skill_dir: Path | str | None = None,
    days: int = 1,
) -> dict[str, Any]:
    skill_dir = Path(skill_dir or SKILL_DIR)
    path = _metrics_path(skill_dir)
    data = _load_execution_metrics(path)
    all_days = data.get("days", {})
    day_keys = sorted(all_days.keys())
    take = day_keys[-max(1, int(days)) :] if day_keys else []

    events: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for d in take:
        bucket = all_days.get(d, {})
        for ev, cnt in (bucket.get("events", {}) or {}).items():
            events[ev] = events.get(ev, 0) + int(cnt or 0)
        for rsn, cnt in (bucket.get("reasons", {}) or {}).items():
            reasons[rsn] = reasons.get(rsn, 0) + int(cnt or 0)

    top_reasons = sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "window_days": max(1, int(days)),
        "days_present": len(take),
        "events": events,
        "top_reasons": [{"reason": r, "count": c} for r, c in top_reasons],
    }


def _exit_manager_state_path(skill_dir: Path) -> Path:
    return skill_dir / _EXIT_MANAGER_STATE_FILE


def _load_exit_manager_state(skill_dir: Path) -> dict[str, Any]:
    path = _exit_manager_state_path(skill_dir)
    if not path.exists():
        return {"positions": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("positions"), dict):
            return data
    except Exception as exc:
        LOG.warning("Ignoring unreadable exit-manager state file %s: %s", path, exc)
    return {"positions": {}}


def _save_exit_manager_state(skill_dir: Path, state: dict[str, Any]) -> None:
    atomic_write_json(_exit_manager_state_path(skill_dir), state, indent=2)

