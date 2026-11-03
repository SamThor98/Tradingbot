#!/usr/bin/env python3
"""Apply PEAD S1 paper canary env (shadow, top-5, ALLOW_LIVE=false).

Also upserts multi-sleeve RTH shadow so R7 can score S1 names.
Does NOT enable PEAD live, allocator live, or S1 live capital.

Usage (from schwab_skill):
  python scripts/apply_pead_canary_paper_env.py
  python scripts/apply_pead_canary_paper_env.py --dry-run
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
    PEAD_CANARY_PAPER_ENV,
    apply_multi_sleeve_rth_shadow_env,
    apply_pead_canary_paper_env,
    multi_sleeve_rth_shadow_file_readiness,
    pead_canary_paper_file_readiness,
    signal_stack_enforced_file_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(ENV_PATH), help="Target .env file")
    parser.add_argument("--dry-run", action="store_true", help="Print planned updates only")
    args = parser.parse_args()
    env_path = Path(args.env_path)

    planned = dict(MULTI_SLEEVE_RTH_SHADOW_ENV)
    planned.update(PEAD_CANARY_PAPER_ENV)
    if args.dry_run:
        print(f"Would upsert in {env_path}:")
        for key, value in planned.items():
            print(f"  {key}={value}")
        return 0

    if not env_path.exists():
        print(f"Creating {env_path} …")
        example = SKILL_DIR / "env.example"
        if example.exists():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    changed = []
    changed.extend(apply_pead_canary_paper_env(env_path))
    changed.extend(apply_multi_sleeve_rth_shadow_env(env_path))
    # Dedup while preserving order
    seen: set[str] = set()
    unique = []
    for key in changed:
        if key not in seen:
            seen.add(key)
            unique.append(key)
    if unique:
        print(f"Updated {env_path}: {', '.join(unique)}")
    else:
        print(f"No changes needed — {env_path} already has PEAD paper + R7 vars.")

    pead = pead_canary_paper_file_readiness(env_path)
    sleeve = multi_sleeve_rth_shadow_file_readiness(env_path)
    stack = signal_stack_enforced_file_readiness(env_path)
    if pead.get("allow_live"):
        print("FAIL: STRATEGY_PEAD_PRIMARY_ALLOW_LIVE is true after apply")
        return 1
    if pead.get("ready") and sleeve.get("ready"):
        print(
            "PASS: PEAD paper shadow top5 allow_live=false "
            f"R7_min_n={sleeve.get('r7_min_n')} horizon={sleeve.get('r7_primary_horizon')}"
        )
        if stack.get("ready"):
            print(
                "PASS: Stage2 stack still enforced "
                f"(exit {stack.get('exit_min_hold_days_before_trail')}/"
                f"{stack.get('exit_max_hold_days')})"
            )
        else:
            print("WARN: Stage2 enforced stack not fully ready (PEAD paper still ok).")
            for item in stack.get("missing_env") or []:
                print(f"- stack: {item}")
        print("Restart the dashboard if it is already running.")
        print("RTH session: python scripts/run_pead_canary_paper_session.py")
        return 0

    print("WARN: file updated but readiness check failed.")
    for item in (pead.get("missing_env") or []) + (sleeve.get("missing_env") or []):
        print(f"- {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
