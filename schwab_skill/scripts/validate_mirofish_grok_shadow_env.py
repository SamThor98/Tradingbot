#!/usr/bin/env python3
"""Validate MiroFish Grok shadow routing in the local .env (no secrets required)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import mirofish_grok_shadow_file_readiness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(SKILL_DIR / ".env"))
    args = parser.parse_args()
    env_path = Path(args.env_path)
    if not env_path.exists():
        print(f"FAIL: missing {env_path}")
        print("Fix: python scripts/apply_mirofish_grok_shadow_env.py")
        return 1

    readiness = mirofish_grok_shadow_file_readiness(env_path)
    if not readiness.get("ready"):
        print("FAIL: MiroFish Grok shadow env is incomplete")
        for item in readiness.get("missing_env") or []:
            print(f"- {item}")
        print("Fix: python scripts/apply_mirofish_grok_shadow_env.py")
        return 1

    print(
        "PASS: MiroFish Grok shadow routing "
        f"mode={readiness.get('weighting_mode')} model={readiness.get('llm_model')}"
    )
    for warning in readiness.get("warnings") or []:
        print(f"WARN: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
