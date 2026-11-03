#!/usr/bin/env python3
"""Point MiroFish at xAI Grok with MIROFISH_WEIGHTING_MODE=shadow.

Does not write API keys. Set XAI_API_KEY (or MIROFISH_API_KEY) yourself.
Does not promote weighting to live.

Usage (from schwab_skill):
  python scripts/apply_mirofish_grok_shadow_env.py
  python scripts/apply_mirofish_grok_shadow_env.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SKILL_DIR / ".env"
sys.path.insert(0, str(SKILL_DIR))

from core.env_local import (  # noqa: E402
    MIROFISH_GROK_SHADOW_ENV,
    apply_mirofish_grok_shadow_env,
    mirofish_grok_shadow_file_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", default=str(ENV_PATH), help="Target .env file")
    parser.add_argument("--dry-run", action="store_true", help="Print planned updates without writing")
    args = parser.parse_args()
    env_path = Path(args.env_path)

    if args.dry_run:
        print(f"Would upsert in {env_path}:")
        for key, value in MIROFISH_GROK_SHADOW_ENV.items():
            print(f"  {key}={value}")
        return 0

    if not env_path.exists():
        example = SKILL_DIR / "env.example"
        if example.exists():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"Created {env_path} from env.example")

    changed = apply_mirofish_grok_shadow_env(env_path)
    if changed:
        print(f"Updated {env_path}: {', '.join(changed)}")
    else:
        print(f"No changes needed — {env_path} already has Grok shadow vars.")

    readiness = mirofish_grok_shadow_file_readiness(env_path)
    if readiness.get("ready"):
        print(
            "PASS: weighting={mode} model={model} base_url={url}".format(
                mode=readiness.get("weighting_mode"),
                model=readiness.get("llm_model"),
                url=readiness.get("llm_base_url"),
            )
        )
        for warning in readiness.get("warnings") or []:
            print(f"WARN: {warning}")
        print("Restart the dashboard if it is already running.")
        print("Compare later: python scripts/compare_mirofish_models.py --write-artifact")
        return 0

    print("WARN: file updated but Grok shadow readiness check failed.")
    for item in readiness.get("missing_env") or []:
        print(f"- {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
