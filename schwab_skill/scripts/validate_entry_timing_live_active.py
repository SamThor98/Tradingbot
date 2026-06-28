#!/usr/bin/env python3
"""Validate entry-timing live enforcement is active (post-promotion)."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

DEFAULT_RUN_ID = "control_legacy_aug"


def main() -> int:
    from config import get_entry_timing_breakout_buffer_readiness, get_entry_timing_shadow_mode
    from core.entry_timing_live_compare import build_live_entry_shadow_compare_report, load_last_scan_diagnostics

    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN_ID
    errors: list[str] = []

    profile = get_entry_timing_breakout_buffer_readiness(SKILL_DIR)
    mode = get_entry_timing_shadow_mode(SKILL_DIR)
    if not profile.get("ready"):
        errors.append("breakout-buffer profile not ready")
        for item in profile.get("missing_env") or []:
            errors.append(str(item))
    if mode != "live":
        errors.append(f"ENTRY_TIMING_SHADOW_MODE={mode} (expected live)")

    diagnostics, meta = load_last_scan_diagnostics(sqlite_path=SKILL_DIR / "webapp" / "webapp.db")
    if diagnostics is None:
        errors.append(f"no last_scan ({meta.get('error')})")
    else:
        if int(diagnostics.get("entry_timing_live_enforced") or 0) != 1:
            errors.append("last_scan entry_timing_live_enforced != 1")
        blocked = int(diagnostics.get("entry_timing_blocked") or 0)
        stage_a = int(diagnostics.get("stage_a_candidates") or 0)
        if blocked <= 0:
            errors.append("last_scan entry_timing_blocked is zero")
        if stage_a <= 0:
            errors.append("last_scan stage_a_candidates is zero")
        report = build_live_entry_shadow_compare_report(
            diagnostics,
            skill_dir=SKILL_DIR,
            run_id=run_id,
            live_meta=meta,
            experiment_env_ready=True,
        )
        if report is None:
            errors.append("could not build live compare report")
        elif (report.get("comparison") or {}).get("verdict") != "pass":
            errors.append(f"live compare verdict={(report.get('comparison') or {}).get('verdict')}")

    if errors:
        print("entry timing live active validation failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("entry timing live active validation passed")
    print(f"- mode: {mode}")
    print(f"- profile: {profile.get('profile')}")
    print(f"- stage_a: {diagnostics.get('stage_a_candidates') if diagnostics else 'n/a'}")
    print(f"- entry_timing_blocked: {diagnostics.get('entry_timing_blocked') if diagnostics else 'n/a'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
