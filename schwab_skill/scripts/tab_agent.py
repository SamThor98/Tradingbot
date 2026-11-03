#!/usr/bin/env python3
"""Operate a dashboard tab agent. Nothing here approves a prompt unless you say so.

From schwab_skill/:

    # Opt in (env or .env), then the Sunday 18:20 ET bot-loop job can propose.
    # TAB_AGENT_TODAY_ENABLED=true
    # TAB_AGENT_SHADOW_ENABLED=true   # only if a version is marked shadow

    python scripts/tab_agent.py status --tab today
    python scripts/tab_agent.py score --tab today
    python scripts/tab_agent.py propose --tab today
    python scripts/tab_agent.py approve --tab today --version-id <id>
    python scripts/tab_agent.py reject --tab today --version-id <id>
    python scripts/tab_agent.py shadow --tab today --version-id <id>
    python scripts/tab_agent.py rollback --tab today --version-id <id>

The same approve / reject / rollback actions are on the Today tab
(https://127.0.0.1:8182/?section=today-agent). Proposals stay pending until
one of those explicit actions. There is no auto-approval.
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
    parser.add_argument(
        "command",
        choices=("status", "score", "propose", "approve", "reject", "shadow", "rollback"),
    )
    parser.add_argument("--tab", default="today", help="Tab id. Registered: today, debate.")
    parser.add_argument("--version-id", default="", help="Required for approve, reject, shadow, and rollback.")
    parser.add_argument("--prompt-key", default="", help="Optional single persona key for propose.")
    parser.add_argument("--skill-dir", type=Path, default=SKILL_DIR)
    args = parser.parse_args(argv)

    from core.tab_agent import TabAgentError, get_tab_agent

    try:
        agent = get_tab_agent(args.tab)
    except TabAgentError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        if args.command == "status":
            payload = agent.status(args.skill_dir)
        elif args.command == "score":
            payload = agent.score(args.skill_dir)
        elif args.command == "propose":
            payload = agent.propose(args.skill_dir, prompt_key=args.prompt_key or None)
        else:
            version_id = args.version_id.strip()
            if not version_id:
                print("--version-id is required.", file=sys.stderr)
                return 1
            payload = getattr(agent, args.command)(args.skill_dir, version_id)
    except TabAgentError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
