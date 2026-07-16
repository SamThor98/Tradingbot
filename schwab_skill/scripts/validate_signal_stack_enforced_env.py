#!/usr/bin/env python3
"""Validate .env matches the promoted live-entry + live-exit operating stack."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import signal_stack_enforced_file_readiness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(SKILL_DIR / ".env"))
    args = parser.parse_args()
    readiness = signal_stack_enforced_file_readiness(Path(args.env_path))
    if readiness.get("ready"):
        print("PASS: signal stack enforced env ready")
        print(f"- entry_mode={readiness.get('entry_timing_mode')} profile={readiness.get('profile')}")
        print(
            f"- exit_manager={readiness.get('exit_manager_mode')} "
            f"hold={readiness.get('exit_min_hold_days_before_trail')}/"
            f"{readiness.get('exit_max_hold_days')}"
        )
        print(
            f"- rank_filter_v2={readiness.get('rank_filter_v2_mode')} "
            f"p{readiness.get('rank_filter_v2_min_percentile')}"
        )
        return 0
    print("FAIL: signal stack enforced env not ready")
    for item in readiness.get("missing_env") or []:
        print(f"- {item}")
    print("Fix: python scripts/apply_signal_stack_enforced_env.py")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
