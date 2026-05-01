#!/usr/bin/env python3
"""Validate experiment registry schema and basic record integrity."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from experiment_registry import load_registry_events

REQUIRED_KEYS = {
    "schema_version",
    "recorded_at",
    "event_type",
    "target",
    "decision",
    "rationale",
    "gates",
    "metadata",
}


def _record_ok(rec: dict[str, Any]) -> bool:
    if not REQUIRED_KEYS.issubset(set(rec.keys())):
        return False
    if int(rec.get("schema_version", 0) or 0) != 1:
        return False
    if not isinstance(rec.get("rationale"), list):
        return False
    if not isinstance(rec.get("gates"), dict):
        return False
    if not isinstance(rec.get("metadata"), dict):
        return False
    return True


def main() -> int:
    rows = load_registry_events()
    bad = [idx for idx, rec in enumerate(rows) if not _record_ok(rec)]
    if bad:
        print(f"FAIL: malformed experiment registry records at indices: {bad}")
        return 1
    print(f"PASS: experiment registry schema valid ({len(rows)} records)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
