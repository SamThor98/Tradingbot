#!/usr/bin/env python3
"""Backward-compatible wrapper — prefer ``validate_scoring_metrics.py``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    cmd = [sys.executable, str(SKILL_DIR / "scripts" / "validate_scoring_metrics.py"), *sys.argv[1:]]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
