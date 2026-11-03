#!/usr/bin/env python3
"""Diagnose this machine as a TradingBot ops box and optionally apply worker env.

Does not place orders, copy secrets, or start the dashboard.

Usage (from schwab_skill):
  python scripts/setup_local_ops_box.py
  python scripts/setup_local_ops_box.py --apply-env
  python scripts/setup_local_ops_box.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_DIR if (SKILL_DIR / ".git").exists() else SKILL_DIR.parent
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import upsert_env_file  # noqa: E402
from core.hardware_profile import (  # noqa: E402
    assess_ops_box,
    collect_host_facts,
    write_profile_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a text report")
    parser.add_argument(
        "--apply-env",
        action="store_true",
        help="Upsert SCAN_STAGE_*_MAX_WORKERS into schwab_skill/.env (no-op if .env is missing)",
    )
    parser.add_argument(
        "--env-path",
        default=str(SKILL_DIR / ".env"),
        help="Target .env (default: schwab_skill/.env)",
    )
    parser.add_argument(
        "--artifact",
        default=str(SKILL_DIR / "artifacts" / "hardware_profile.json"),
        help="Write diagnosis JSON here",
    )
    parser.add_argument("--no-artifact", action="store_true", help="Skip writing the artifact file")
    args = parser.parse_args()

    facts = collect_host_facts(REPO_ROOT, skill_dir=SKILL_DIR)
    readiness = assess_ops_box(facts, skill_dir=SKILL_DIR)
    payload = readiness.as_dict()

    env_path = Path(args.env_path)
    applied: list[str] = []
    if args.apply_env:
        if not env_path.is_file():
            payload["apply_env"] = f"skipped — {env_path} does not exist"
        else:
            applied = upsert_env_file(env_path, readiness.profile.env_updates())
            payload["apply_env"] = applied or "already current"
            payload["env_path"] = str(env_path)

    if not args.no_artifact:
        write_profile_artifact(readiness, Path(args.artifact))
        payload["artifact"] = str(Path(args.artifact))

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0 if not readiness.blockers else 1

    profile = readiness.profile
    print("Local ops box profile")
    print("=====================")
    if facts.cpu_name:
        print(f"CPU: {facts.cpu_name}")
    print(f"Cores: {facts.physical_cores} physical / {facts.logical_cpus} logical")
    print(f"RAM: {facts.ram_gb:.1f} GB")
    print(f"Repo: {facts.repo_root} ({facts.repo_bus_type})")
    print(f"Ethernet up: {facts.ethernet_up}  Wi-Fi up: {facts.wifi_up}")
    print()
    print("Recommended workers")
    print(f"  SCAN_STAGE_A_MAX_WORKERS={profile.scan_stage_a_workers}")
    print(f"  SCAN_STAGE_B_MAX_WORKERS={profile.scan_stage_b_workers}")
    print(f"  research --max-workers {profile.research_max_workers}")
    if args.apply_env:
        print()
        if applied:
            print(f"Applied to {env_path}: {', '.join(applied)}")
        elif env_path.is_file():
            print(f"No .env changes needed ({env_path})")
        else:
            print(f"Did not write workers — {env_path} is missing. Copy .env from the old PC first.")
    if profile.warnings:
        print()
        print("Warnings")
        for item in profile.warnings:
            print(f"  - {item}")
    if readiness.blockers:
        print()
        print("Blockers (dashboard will not be a live ops box yet)")
        for item in readiness.blockers:
            print(f"  - {item}")
    if readiness.next_steps:
        print()
        print("Next steps")
        for item in readiness.next_steps:
            print(f"  - {item}")
    for note in profile.notes:
        print()
        print(note)
        break
    return 0 if not readiness.blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
