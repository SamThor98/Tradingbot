#!/usr/bin/env python3
"""Apply multi-sleeve RTH shadow env (allocator shadow + hypothesis ledger).

Does NOT enable ALLOCATOR live or S1 live capital. Safe for RTH evidence weeks.

Usage (from schwab_skill):
  python scripts/apply_multi_sleeve_rth_shadow_env.py
  python scripts/apply_multi_sleeve_rth_shadow_env.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import (  # noqa: E402
    MULTI_SLEEVE_RTH_SHADOW_ENV,
    apply_multi_sleeve_rth_shadow_env,
    multi_sleeve_rth_shadow_file_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(ENV_PATH), help="Target .env file")
    parser.add_argument("--dry-run", action="store_true", help="Print planned updates only")
    args = parser.parse_args()
    env_path = Path(args.env_path)

    if args.dry_run:
        print(f"Would upsert in {env_path}:")
        for key, value in MULTI_SLEEVE_RTH_SHADOW_ENV.items():
            print(f"  {key}={value}")
        return 0

    if not env_path.exists():
        print(f"Creating {env_path} …")
        example = SKILL_DIR / "env.example"
        if example.exists():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    changed = apply_multi_sleeve_rth_shadow_env(env_path)
    if changed:
        print(f"Updated {env_path}: {', '.join(changed)}")
    else:
        print(f"No changes needed — {env_path} already has multi-sleeve RTH shadow vars.")

    readiness = multi_sleeve_rth_shadow_file_readiness(env_path)
    if readiness.get("ready"):
        print(
            "PASS: ALLOCATOR_MODE=shadow ledger=on S1_live=false "
            f"R7_min_n={readiness.get('r7_min_n')} horizon={readiness.get('r7_primary_horizon')}"
        )
        print("Restart the dashboard (uvicorn) if it is already running.")
        print("After RTH scans: python scripts/score_hypothesis_outcomes.py")
        return 0

    print("WARN: file updated but readiness check failed.")
    for item in readiness.get("missing_env") or []:
        print(f"- {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
