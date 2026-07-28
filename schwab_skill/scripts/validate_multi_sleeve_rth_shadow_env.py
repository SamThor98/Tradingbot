#!/usr/bin/env python3
"""Validate multi-sleeve RTH shadow env readiness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import multi_sleeve_rth_shadow_file_readiness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(ENV_PATH))
    args = parser.parse_args()
    readiness = multi_sleeve_rth_shadow_file_readiness(Path(args.env_path))
    if readiness.get("ready"):
        print(
            "PASS: multi-sleeve RTH shadow ready "
            f"(allocator={readiness.get('allocator_mode')} "
            f"ledger={readiness.get('hypothesis_ledger_enabled')} "
            f"s1_live={readiness.get('s1_live')})"
        )
        return 0
    print("FAIL: multi-sleeve RTH shadow not ready")
    for item in readiness.get("missing_env") or []:
        print(f"- {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
