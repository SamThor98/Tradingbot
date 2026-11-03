#!/usr/bin/env python3
"""Upsert SCAN_STAGE_* workers from this host's hardware profile into .env.

Does not create .env, copy secrets, or change plugin modes.

Usage (from schwab_skill):
  python scripts/apply_local_ops_box_env.py
  python scripts/apply_local_ops_box_env.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_DIR if (SKILL_DIR / ".git").exists() else SKILL_DIR.parent
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import upsert_env_file  # noqa: E402
from core.hardware_profile import assess_ops_box, collect_host_facts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(SKILL_DIR / ".env"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    env_path = Path(args.env_path)

    facts = collect_host_facts(REPO_ROOT, skill_dir=SKILL_DIR)
    readiness = assess_ops_box(facts, skill_dir=SKILL_DIR)
    updates = readiness.profile.env_updates()

    if args.dry_run:
        print(f"Would upsert in {env_path}:")
        for key, value in updates.items():
            print(f"  {key}={value}")
        return 0

    if not env_path.is_file():
        print(f"Refusing to create {env_path}. Copy .env from the old PC, then re-run.")
        return 1

    changed = upsert_env_file(env_path, updates)
    if changed:
        print(f"Updated {env_path}: {', '.join(changed)}")
    else:
        print(f"No changes needed — {env_path} already has ops-box worker vars.")
    print("Restart the dashboard if it is already running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
