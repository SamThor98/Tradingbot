#!/usr/bin/env python3
"""Weekly P0 check while entry-timing live enforcement is active."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RUN_ID = "control_legacy_aug"

WEEKLY_SCRIPTS = (
    "entry_timing_experiment_status.py",
    "validate_entry_timing_live_active.py",
    "validate_signal_gate_stack.py",
    "validate_signal_gate_phase2_readiness.py",
)


def main() -> int:
    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN_ID
    print(f"=== Entry-timing live weekly check ({run_id}) ===")
    failed: list[str] = []
    for script in WEEKLY_SCRIPTS:
        cmd = [sys.executable, str(SKILL_DIR / "scripts" / script), run_id]
        proc = subprocess.run(cmd, cwd=SKILL_DIR, capture_output=True, text=True)
        status = "PASS" if proc.returncode == 0 else "FAIL"
        print(f"\n--- {script} [{status}] ---")
        if proc.stdout.strip():
            print(proc.stdout.strip())
        if proc.stderr.strip():
            print(proc.stderr.strip())
        if proc.returncode != 0:
            failed.append(script)

    print("\n=== Summary ===")
    if failed:
        print(f"Weekly check FAILED ({len(failed)} step(s)): {', '.join(failed)}")
        return 1
    print("Weekly check PASSED — continue monitoring throughput for one market week.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
