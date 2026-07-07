#!/usr/bin/env python3
"""Apply P0 signal-stack shadow vars to schwab_skill/.env (idempotent).

Sets exit grace (15d defer, 40d max hold) in EXIT_MANAGER shadow mode plus
entry-timing breakout-buffer shadow experiment. Does not enable entry-timing LIVE.

Usage (from schwab_skill):
  python scripts/apply_signal_stack_shadow_env.py
  python scripts/apply_signal_stack_shadow_env.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import (  # noqa: E402
    SIGNAL_STACK_SHADOW_ENV,
    apply_signal_stack_shadow_env,
    signal_stack_shadow_file_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(ENV_PATH), help="Target .env file (default: schwab_skill/.env)")
    parser.add_argument("--dry-run", action="store_true", help="Print planned updates without writing")
    args = parser.parse_args()
    env_path = Path(args.env_path)

    if args.dry_run:
        print(f"Would upsert in {env_path}:")
        for key, value in SIGNAL_STACK_SHADOW_ENV.items():
            print(f"  {key}={value}")
        return 0

    if not env_path.exists():
        print(f"Creating {env_path} from env.example defaults plus stack shadow block…")
        example = SKILL_DIR / "env.example"
        if example.exists():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    changed = apply_signal_stack_shadow_env(env_path)
    if changed:
        print(f"Updated {env_path}: {', '.join(changed)}")
    else:
        print(f"No changes needed — {env_path} already has stack shadow vars.")

    readiness = signal_stack_shadow_file_readiness(env_path)
    if readiness.get("ready"):
        entry = readiness.get("entry_timing") or {}
        print(f"PASS: exit_manager=shadow hold=15/40 entry_profile={entry.get('profile')}")
        print("Restart the dashboard (uvicorn) if it is already running.")
        return 0

    print("WARN: file updated but stack shadow readiness check failed.")
    for item in readiness.get("missing_env") or []:
        print(f"- {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
