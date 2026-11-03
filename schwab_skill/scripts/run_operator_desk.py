"""Run the operator desk against the local dashboard and optionally post to Discord.

Examples:
  python scripts/run_operator_desk.py
  python scripts/run_operator_desk.py --ticker AAPL --queue-backtest --discord
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from operator_desk import DashboardTools, run_operator_desk  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the operator desk against the dashboard.")
    parser.add_argument("--ticker", default="", help="Optional equity for a quick check.")
    parser.add_argument("--no-scan", action="store_true", help="Grade the last scan instead of starting one.")
    parser.add_argument(
        "--wait-for-running",
        action="store_true",
        help="If a scan is already running, wait for it instead of grading an empty snapshot.",
    )
    parser.add_argument("--scan-timeout", type=int, default=0, help="Seconds to wait for the scan. 0 uses the config getter.")
    parser.add_argument("--queue-backtest", action="store_true", help="Queue a trailing replay if the worker is idle.")
    parser.add_argument("--discord", action="store_true", help="Post the brief to Discord.")
    parser.add_argument("--base-url", default="", help="Dashboard origin. Default https://127.0.0.1:8182.")
    args = parser.parse_args()
    from config import get_operator_desk_base_url, get_operator_desk_scan_timeout_sec

    base_url = args.base_url.strip() or get_operator_desk_base_url(SKILL_DIR)
    tools = DashboardTools(base_url, api_key=os.environ.get("WEB_API_KEY", "").strip())
    brief = run_operator_desk(
        tools,
        skill_dir=SKILL_DIR,
        run_scan=not args.no_scan,
        queue_backtest=bool(args.queue_backtest),
        notify_discord=bool(args.discord),
        ticker=args.ticker.strip() or None,
        wait_if_running=bool(args.wait_for_running),
        scan_timeout_s=float(args.scan_timeout or get_operator_desk_scan_timeout_sec(SKILL_DIR)),
    )
    print(brief.get("text") or "")
    return 0 if brief.get("scan", {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
