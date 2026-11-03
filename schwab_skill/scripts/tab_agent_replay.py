#!/usr/bin/env python3
"""Replay matured scans through a tab agent's live prompt (and its shadow).

Point-in-time inputs are the fields stored on the scan date. The label is the
existing 20-day scale-out on stored prices, and bars after that horizon are
not used. The model may still know later events. Samples are tagged
``source=replay``, weighted below live samples, and cannot by themselves
authorize a promotion.

This command never places an order.

From ``schwab_skill/``::

    python scripts/tab_agent_replay.py --tab research --since 2026-01-01 --dry-run
    python scripts/tab_agent_replay.py --tab today --since 2026-01-01 --limit 20 --max-llm-calls 25

A second run skips cases already stored in ``.tab_agent_replay.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tab", required=True, choices=["today", "debate", "research"])
    parser.add_argument("--since", default="", help="Inclusive UTC date, YYYY-MM-DD. Required unless --rescore.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum matured cases.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the call and cost estimate. Do not call the model."
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=25,
        help="Stop before this many model calls. Default 25.",
    )
    parser.add_argument("--skill-dir", type=Path, default=SKILL_DIR)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite scan_results file. Default: <skill-dir>/webapp/webapp.db.",
    )
    parser.add_argument("--user-id", default="local")
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="Relabel stored replay rows. Does not call the model.",
    )
    parser.add_argument(
        "--prompt-key",
        action="append",
        default=[],
        help="Score only this decision prompt. Repeatable. Debate still runs the full committee.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=10,
        help="Print calls, cost, and parse failures every N cases. 0 silences the ticker.",
    )
    args = parser.parse_args(argv)
    if not args.rescore and not args.since:
        parser.error("--since is required")

    if args.rescore:
        from core.tab_agent_replay import rescore_replay

        report = rescore_replay(args.skill_dir, tab=args.tab)
        print(json.dumps(report, indent=2))
        if report.get("needs_llm"):
            print("Minimum rerun still needs a model for: " + ", ".join(report["needs_llm"]))
            print(
                "python scripts/tab_agent_replay.py --tab debate --since "
                f"{args.since or 'YYYY-MM-DD'} --max-llm-calls 50 --progress-every 10"
            )
            if "today:mean_reversion" in report["needs_llm"]:
                print(
                    "python scripts/tab_agent_replay.py --tab today --since "
                    f"{args.since or 'YYYY-MM-DD'} --prompt-key mean_reversion --max-llm-calls 50"
                )
        return 0 if report.get("ok") else 1

    from core.tab_agent_replay import run_replay

    report = run_replay(
        args.skill_dir,
        tab=args.tab,
        since=args.since,
        limit=args.limit,
        dry_run=args.dry_run,
        max_llm_calls=args.max_llm_calls,
        db_path=args.db,
        user_id=args.user_id,
        progress_every=args.progress_every,
        prompt_keys=set(args.prompt_key) or None,
    )
    print(
        f"estimated_llm_calls={report['estimated_llm_calls']} "
        f"estimated_cost_usd={report['estimated_cost_usd']} "
        f"calls_under_cap={report['calls_under_cap']} "
        f"max_llm_calls={report['max_llm_calls']}"
    )
    print(report["caveat"])
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
