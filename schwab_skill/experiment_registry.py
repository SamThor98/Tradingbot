"""Canonical experiment/promotion decision registry (JSONL)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = SKILL_DIR / "validation_artifacts" / "experiment_registry.jsonl"
SCHEMA_VERSION = 1


def append_registry_event(
    *,
    event_type: str,
    target: str,
    decision: str,
    rationale: list[str] | None = None,
    gates: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    skill_dir: Path | str | None = None,
) -> dict[str, Any]:
    root = Path(skill_dir or SKILL_DIR)
    path = root / "validation_artifacts" / "experiment_registry.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "event_type": str(event_type),
        "target": str(target),
        "decision": str(decision),
        "rationale": list(rationale or []),
        "gates": dict(gates or {}),
        "metadata": dict(metadata or {}),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")
    return event


def load_registry_events(skill_dir: Path | str | None = None) -> list[dict[str, Any]]:
    root = Path(skill_dir or SKILL_DIR)
    path = root / "validation_artifacts" / "experiment_registry.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows
