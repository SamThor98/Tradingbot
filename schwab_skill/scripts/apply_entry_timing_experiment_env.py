#!/usr/bin/env python3
"""Apply P0 entry-timing experiment vars to schwab_skill/.env (idempotent).

Does not remove unrelated keys. After running, restart the dashboard process
so config getters pick up the new values.

Usage (from schwab_skill):
  python scripts/apply_entry_timing_experiment_env.py
  python scripts/apply_entry_timing_experiment_env.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
sys.path.insert(0, str(SKILL_DIR))

from config import get_entry_timing_experiment_readiness  # noqa: E402
from core.env_local import ENTRY_TIMING_EXPERIMENT_ENV, apply_entry_timing_experiment_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(ENV_PATH), help="Target .env file (default: schwab_skill/.env)")
    parser.add_argument("--dry-run", action="store_true", help="Print planned updates without writing")
    args = parser.parse_args()
    env_path = Path(args.env_path)

    if args.dry_run:
        print(f"Would upsert in {env_path}:")
        for key, value in ENTRY_TIMING_EXPERIMENT_ENV.items():
            print(f"  {key}={value}")
        return 0

    if not env_path.exists():
        print(f"Creating {env_path} from env.example defaults plus experiment block…")
        example = SKILL_DIR / "env.example"
        if example.exists():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    changed = apply_entry_timing_experiment_env(env_path)
    if changed:
        print(f"Updated {env_path}: {', '.join(changed)}")
    else:
        print(f"No changes needed — {env_path} already has experiment vars.")

    # Verify readiness using file values without permanently polluting os.environ.
    saved = {key: os.environ.get(key) for key in ENTRY_TIMING_EXPERIMENT_ENV}
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, val = stripped.split("=", 1)
            os.environ[key.strip()] = val.strip()
        readiness = get_entry_timing_experiment_readiness(SKILL_DIR)
    finally:
        for key, prior in saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior

    if readiness.get("ready"):
        print(f"PASS: profile={readiness.get('profile')}")
        print("Restart the dashboard (uvicorn) if it is already running.")
        return 0

    print("WARN: file updated but readiness check failed — verify .env and restart dashboard.")
    for item in readiness.get("missing_env") or []:
        print(f"- {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
