#!/usr/bin/env python3
"""Check whether process env matches the P0 breakout-buffer-only experiment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    from config import get_entry_timing_breakout_buffer_readiness, get_entry_timing_experiment_readiness

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 1 when experiment env is not configured (default: exit 0 with status)",
    )
    parser.add_argument(
        "--check-file",
        action="store_true",
        help="Evaluate readiness from .env file only (ignore process env)",
    )
    args = parser.parse_args()

    if args.check_file:
        from core.env_local import entry_timing_experiment_file_readiness

        status = entry_timing_experiment_file_readiness(SKILL_DIR / ".env")
        scope = "file"
    else:
        status = get_entry_timing_experiment_readiness(SKILL_DIR)
        scope = "process"

    profile_status = get_entry_timing_breakout_buffer_readiness(SKILL_DIR)
    ready = bool(status.get("ready"))
    profile_ready = bool(profile_status.get("ready"))
    mode = profile_status.get("mode")
    profile = profile_status.get("profile")
    expected = profile_status.get("expected_profile")
    missing = profile_status.get("missing_env") or []

    if profile_ready and mode == "live":
        print(f"PASS: entry-timing live profile active ({scope}, profile={profile}, mode=live)")
        return 0
    if ready:
        print(f"PASS: entry-timing experiment env ready ({scope}, profile={profile})")
        return 0

    print(f"WARN: entry-timing profile not ready ({scope}, profile={profile}, expected={expected}, mode={mode})")
    for item in missing:
        print(f"- set {item}")
    rec = status.get("recommended_env") or {}
    if rec:
        print("Recommended block:")
        for key, val in rec.items():
            print(f"  {key}={val}")

    if args.require_ready:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
