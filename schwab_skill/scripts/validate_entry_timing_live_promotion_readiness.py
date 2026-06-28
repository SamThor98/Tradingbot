#!/usr/bin/env python3
"""Validate entry-timing live promotion readiness (Stage 2b + stack + live compare)."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

DEFAULT_RUN_ID = "control_legacy_aug"


def main() -> int:
    from core.entry_timing_live_compare import assess_entry_timing_live_promotion_readiness

    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN_ID
    readiness = assess_entry_timing_live_promotion_readiness(SKILL_DIR, run_id=run_id)

    if not readiness.get("ready"):
        print("entry timing live promotion readiness failed:")
        for err in readiness.get("errors") or []:
            print(f"- {err}")
        for msg in readiness.get("messages") or []:
            print(f"NOTE: {msg}")
        return 1

    print("entry timing live promotion readiness passed")
    print(f"- mode: {readiness.get('mode')}")
    print(f"- compare verdict: {readiness.get('compare_verdict')}")
    print(f"- stack pf_mean: {readiness.get('stack_pf_mean')} worst: {readiness.get('stack_worst_era_pf')}")
    for msg in readiness.get("messages") or []:
        print(f"- {msg}")
    for note in readiness.get("notes") or []:
        print(f"NOTE: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
