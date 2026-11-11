#!/usr/bin/env python3
"""Overnight prompt lab. Analysis prompts only. This command never places an order.

From ``schwab_skill/``::

    python scripts/tab_agent_lab.py --plan
    python scripts/tab_agent_lab.py --dry-run
    python scripts/tab_agent_lab.py --dry-run --tab debate --candidates 3 --generations 1

``--dry-run`` revises prompts offline, scores one short generation on a
synthetic walk-forward book, and does not install a shadow. The night
process is ``--until-window-end``, which the scheduler launches when
``TAB_AGENT_LAB_ENABLED=1``.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.tab_agent_lab import main

if __name__ == "__main__":
    raise SystemExit(main())
