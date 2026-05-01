#!/usr/bin/env python3
"""Fail when mypy errors exceed the ratchet baseline."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
BASELINE = SKILL_DIR / ".quality" / "mypy_baseline.json"


def _load_max_errors() -> int:
    if not BASELINE.exists():
        return 0
    try:
        payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    try:
        return max(0, int(payload.get("max_errors", 0)))
    except Exception:
        return 0


def main() -> int:
    max_errors = _load_max_errors()
    cmd = [sys.executable, "-m", "mypy"]
    proc = subprocess.run(cmd, cwd=str(SKILL_DIR), capture_output=True, text=True)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if stdout:
        print(stdout)
    if stderr:
        print(stderr)
    error_count = sum(1 for line in stdout.splitlines() if ": error:" in line)
    print(f"typecheck_ratchet: current_errors={error_count} baseline_max={max_errors}")
    if error_count > max_errors:
        print("FAIL: mypy error count exceeded ratchet baseline")
        return 1
    print("PASS: mypy error count within ratchet baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
