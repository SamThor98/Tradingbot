#!/usr/bin/env python3
"""Apply promoted P0 signal stack to schwab_skill/.env (idempotent).

Live 1% breakout-buffer entry timing + live exit grace (15d / 40d hold)
with backtest parity. Does not demote EVENT_RISK or EXEC_QUALITY.

Usage (from schwab_skill):
  python scripts/apply_signal_stack_enforced_env.py
  python scripts/apply_signal_stack_enforced_env.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import (  # noqa: E402
    SIGNAL_STACK_ENFORCED_ENV,
    apply_signal_stack_enforced_env,
    signal_stack_enforced_file_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(ENV_PATH), help="Target .env file (default: schwab_skill/.env)")
    parser.add_argument("--dry-run", action="store_true", help="Print planned updates without writing")
    args = parser.parse_args()
    env_path = Path(args.env_path)

    if args.dry_run:
        print(f"Would upsert in {env_path}:")
        for key, value in SIGNAL_STACK_ENFORCED_ENV.items():
            print(f"  {key}={value}")
        return 0

    if not env_path.exists():
        print(f"Creating {env_path} from env.example defaults plus enforced stack block…")
        example = SKILL_DIR / "env.example"
        if example.exists():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    changed = apply_signal_stack_enforced_env(env_path)
    if changed:
        print(f"Updated {env_path}: {', '.join(changed)}")
    else:
        print(f"No changes needed — {env_path} already has enforced stack vars.")

    readiness = signal_stack_enforced_file_readiness(env_path)
    if readiness.get("ready"):
        print(
            "PASS: entry=live profile={profile} exit_manager=live "
            "rank_filter_v2={mode}@p{pct} hold=15/40".format(
                profile=readiness.get("profile"),
                mode=readiness.get("rank_filter_v2_mode"),
                pct=readiness.get("rank_filter_v2_min_percentile"),
            )
        )
        print("Restart the dashboard (uvicorn) if it is already running.")
        return 0

    print("WARN: file updated but enforced stack readiness check failed.")
    for item in readiness.get("missing_env") or []:
        print(f"- {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
