#!/usr/bin/env python3
"""Open the local dashboard Connect Schwab flow in the default browser.

Use when the dashboard is already running on port 8182 (recommended path).
Do not run fix_schwab_auth.py or run_dual_auth_browser.py while the dashboard
holds the callback port.

Usage (from schwab_skill/):
  python scripts/open_schwab_connect.py
  python scripts/open_schwab_connect.py --account-only
"""

from __future__ import annotations

import argparse
import os
import socket
import webbrowser
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _callback_port() -> int:
    raw = os.getenv("LOCAL_WEB_PORT", "8182")
    try:
        return int(str(raw).strip() or "8182")
    except ValueError:
        return 8182


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--account-only",
        action="store_true",
        help="Open account OAuth start URL instead of the Connect UI.",
    )
    args = parser.parse_args()

    port = _callback_port()
    if not _port_open(port):
        print(f"FAIL: nothing listening on https://127.0.0.1:{port}")
        print("Start the dashboard first:")
        print("  python scripts/start_local_dashboard.py")
        return 1

    if args.account_only:
        url = f"https://127.0.0.1:{port}/api/oauth/schwab/start"
    else:
        url = f"https://127.0.0.1:{port}/?section=connect"

    print(f"Opening {url}")
    print("Accept the self-signed certificate warning, then approve Schwab access.")
    print("Account OAuth runs first; market OAuth follows automatically when needed.")
    webbrowser.open(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
