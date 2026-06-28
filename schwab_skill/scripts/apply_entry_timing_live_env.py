#!/usr/bin/env python3
"""Promote entry-timing from shadow to live (breakout buffer 1.0% only).

Requires validate_entry_timing_live_promotion_readiness to pass. Restart the
dashboard after applying so the process loads ENTRY_TIMING_SHADOW_MODE=live.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
sys.path.insert(0, str(SKILL_DIR))

from core.entry_timing_live_compare import assess_entry_timing_live_promotion_readiness  # noqa: E402
from core.env_local import (  # noqa: E402
    ENTRY_TIMING_LIVE_ENV,
    apply_entry_timing_live_env,
    reload_env_file_into_process,
    restore_process_env,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(ENV_PATH))
    parser.add_argument("--run-id", default="control_legacy_aug")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Skip readiness gate (not recommended)")
    args = parser.parse_args()
    env_path = Path(args.env_path)

    readiness = assess_entry_timing_live_promotion_readiness(SKILL_DIR, run_id=args.run_id)
    if not args.force and not readiness.get("ready"):
        print("FAIL: live promotion readiness gate not met")
        for err in readiness.get("errors") or []:
            print(f"- {err}")
        print("Run: python scripts/validate_entry_timing_live_promotion_readiness.py")
        return 1

    if args.dry_run:
        print(f"Would upsert in {env_path}:")
        for key, value in ENTRY_TIMING_LIVE_ENV.items():
            print(f"  {key}={value}")
        return 0

    changed = apply_entry_timing_live_env(env_path)
    print(f"Updated {env_path}: {', '.join(changed) if changed else 'already set'}")

    from config import get_entry_timing_breakout_buffer_readiness, get_entry_timing_shadow_mode

    saved = reload_env_file_into_process(env_path, keys=list(ENTRY_TIMING_LIVE_ENV.keys()))
    try:
        readiness = get_entry_timing_breakout_buffer_readiness(SKILL_DIR)
    finally:
        restore_process_env(saved)

    if readiness.get("ready") and get_entry_timing_shadow_mode(SKILL_DIR) == "live":
        print(f"PASS: mode=live profile={readiness.get('profile')}")
        print("Restart the dashboard (uvicorn) if it is already running.")
        return 0

    print("WARN: file updated but live readiness check failed.")
    for item in readiness.get("missing_env") or []:
        print(f"- {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
