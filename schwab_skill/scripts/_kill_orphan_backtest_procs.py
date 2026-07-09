#!/usr/bin/env python3
"""Kill orphaned multi-era / single-chunk backtest worker processes."""
from __future__ import annotations

import subprocess

KEYWORDS = ("single-chunk", "run_multi_era_backtest", "phase1_overlay_sweep")


def main() -> int:
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "name like '%python%'", "get", "ProcessId,CommandLine"],
            text=True,
            errors="replace",
        )
    except Exception as exc:
        print(f"wmic failed: {exc}")
        return 1
    killed = 0
    for line in out.splitlines():
        low = line.lower()
        if not any(k in low for k in KEYWORDS):
            continue
        parts = line.strip().rsplit(None, 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        pid = parts[1]
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
        killed += 1
        print(f"killed {pid}: {line[:120]}")
    print(f"total killed: {killed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
