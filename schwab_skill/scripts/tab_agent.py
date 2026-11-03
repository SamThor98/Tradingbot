#!/usr/bin/env python3
"""Manual override for the Today and Debate tab agents.

Analysis prompts only. Approve and reject write the review verdict file
(``reviewer`` is ``sam``). Promote and rollback change which prompt is live.
This command never places an order.

From ``schwab_skill/``::

    python scripts/tab_agent.py status --tab today
    python scripts/tab_agent.py approve --tab debate --version <id>
    python scripts/tab_agent.py reject --tab today
    python scripts/tab_agent.py promote --tab today --version <id>
    python scripts/tab_agent.py rollback --tab today --version <id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "propose", "approve", "reject", "rollback", "promote"):
        cmd = sub.add_parser(name, help=f"{name} one tab agent")
        cmd.add_argument("--tab", required=True, choices=("today", "debate"))
        cmd.add_argument("--version", default="", help="Prompt version id. Optional when only one candidate exists.")
        cmd.add_argument("--skill-dir", type=Path, default=SKILL_DIR)
        if name == "propose":
            cmd.add_argument("--prompt-key", default="", help="Revise one prompt key. Default: every key without a shadow.")
        if name in {"approve", "reject"}:
            cmd.add_argument("--notes", default="", help="Stored on the verdict file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from core.tab_agent import TabAgentError
    from core.tab_agent_override import (
        promote_version,
        propose_revision,
        record_verdict,
        rollback_version,
        tab_status,
    )

    try:
        if args.command == "status":
            payload = tab_status(args.skill_dir, args.tab)
            if args.version:
                payload["selected"] = args.version
        elif args.command == "propose":
            payload = propose_revision(args.skill_dir, args.tab, prompt_key=args.prompt_key or None)
        elif args.command == "approve":
            payload = record_verdict(args.skill_dir, args.tab, args.version, "approve", notes=args.notes)
        elif args.command == "reject":
            payload = record_verdict(args.skill_dir, args.tab, args.version, "reject", notes=args.notes)
        elif args.command == "rollback":
            payload = rollback_version(args.skill_dir, args.tab, args.version)
        elif args.command == "promote":
            payload = promote_version(args.skill_dir, args.tab, args.version)
        else:
            raise TabAgentError(f"Unknown command {args.command}")
    except TabAgentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
