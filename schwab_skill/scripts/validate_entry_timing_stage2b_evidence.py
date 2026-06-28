#!/usr/bin/env python3
"""Validate Stage 2b entry-timing shadow alignment evidence (P0)."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

DEFAULT_RUN_ID = "control_legacy_aug"


def main() -> int:
    from core.entry_timing_live_compare import (
        assess_stage2b_readiness,
        load_entry_timing_evidence_log,
        load_validation_artifact,
    )

    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN_ID
    errors: list[str] = []

    if load_validation_artifact(SKILL_DIR, f"entry_timing_shadow_counterfactual_{run_id}.json") is None:
        errors.append(f"missing entry_timing_shadow_counterfactual_{run_id}.json")

    compare = load_validation_artifact(SKILL_DIR, f"live_entry_shadow_compare_{run_id}.json")
    if compare is None:
        errors.append(f"missing live_entry_shadow_compare_{run_id}.json")
    elif (compare.get("comparison") or {}).get("verdict") != "pass":
        errors.append("latest live compare verdict is not pass")

    log = load_entry_timing_evidence_log(SKILL_DIR, run_id)
    stage2b = log.get("stage2b")
    if not isinstance(stage2b, dict):
        stage2b = assess_stage2b_readiness(log.get("records") or [])

    if not stage2b.get("ready"):
        errors.append(
            f"Stage 2b not ready: {stage2b.get('pass_scans')}/{stage2b.get('required_pass_scans')} pass scans"
        )

    if errors:
        print("entry timing Stage 2b evidence validation failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("entry timing Stage 2b evidence validation passed")
    print(f"- pass scans: {stage2b.get('pass_scans')}/{stage2b.get('required_pass_scans')}")
    print(f"- full-universe pass scans: {stage2b.get('full_universe_pass_scans')}")
    for msg in stage2b.get("messages") or []:
        print(f"- {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
