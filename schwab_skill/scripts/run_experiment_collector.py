#!/usr/bin/env python3
"""Dry-run or notify the experiment collector harvest / Discord brief.

Usage (from schwab_skill/):
  python scripts/run_experiment_collector.py --summarize-only
  python scripts/run_experiment_collector.py --notify
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summarize-only",
        action="store_true",
        help="Print the 16:15 brief packet without posting Discord",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Post daily (and campaign-complete if due) Discord briefs now",
    )
    args = parser.parse_args()

    from core.experiment_collector import build_brief_packet, deliver_experiment_briefs

    if args.notify:
        result = deliver_experiment_briefs(SKILL_DIR, notify=True)
        print(json.dumps(result, indent=2, default=str))
        return 0 if not result.get("skipped") else 0

    packet = build_brief_packet(SKILL_DIR)
    print(json.dumps(packet, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
