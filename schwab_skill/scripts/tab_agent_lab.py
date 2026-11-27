#!/usr/bin/env python3
"""Overnight prompt lab. Analysis prompts only. This command never places an order.

From ``schwab_skill/``::

    python scripts/tab_agent_lab.py --plan
    python scripts/tab_agent_lab.py --dry-run
    python scripts/tab_agent_lab.py --dry-run --tab debate --candidates 3 --generations 1
    python scripts/enrich_setup_rule_features.py
    python scripts/tab_agent_lab.py --setup-rules
    python scripts/tab_agent_lab.py --setup-rules --trades validation_artifacts/multi_era_chunks/control_legacy_aug_sp1500_full

``--dry-run`` revises prompts offline, scores one short generation on a
synthetic walk-forward book, and does not install a shadow. ``--setup-rules``
is an offline inspection of entry filters. It refreshes the feature sidecar
when ``.tab_agent_rebuild/bars.pkl`` is on disk, then scores the pool. It does
not start the night and does not trade. The night process is ``--until-window-end``, which the
scheduler launches when ``TAB_AGENT_LAB_ENABLED=1``, and only between
6:00 PM and 7:00 AM America/Chicago. That night runs the setup-rule search
first (no model call unless ``SETUP_RULE_SEARCH_LLM=true``).
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
