#!/usr/bin/env python3
"""Write point-in-time setup features next to a trade pool. No network.

Reads ``.tab_agent_rebuild/bars.pkl`` and the chunk directory. Writes
``validation_artifacts/setup_rule_features/<pool>.jsonl``. Does not rewrite
chunks, place orders, or change the live scan.

From ``schwab_skill/``::

    python scripts/enrich_setup_rule_features.py
    python scripts/enrich_setup_rule_features.py \\
        --pool validation_artifacts/multi_era_chunks/control_legacy_aug_sp1500_full \\
        --bars .tab_agent_rebuild/bars.pkl

Then score the enriched pool without waiting for the night window::

    python scripts/tab_agent_lab.py --setup-rules
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.setup_rule_features import (  # noqa: E402
    BarsFormatError,
    bars_path,
    default_sidecar,
    enrich_pool,
)
from core.setup_rule_search import chunks_dir  # noqa: E402
from logger_setup import get_logger, setup_logging  # noqa: E402

LOG = get_logger("enrich_setup_rule_features")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, default=SKILL_DIR)
    parser.add_argument("--pool", type=Path, default=None, help="Chunk directory. Default is the configured run id.")
    parser.add_argument(
        "--bars", type=Path, default=None, help="Pickled daily bars. Default is .tab_agent_rebuild/bars.pkl."
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="Sidecar JSONL. Default sits beside validation_artifacts."
    )
    args = parser.parse_args(argv)
    setup_logging()
    skill_dir = Path(args.skill_dir)
    from config import get_setup_rule_search_run_id

    pool = Path(args.pool) if args.pool else chunks_dir(skill_dir) / get_setup_rule_search_run_id(skill_dir)
    if not pool.is_absolute():
        pool = skill_dir / pool
    bars = Path(args.bars) if args.bars else bars_path(skill_dir)
    if not bars.is_absolute():
        bars = skill_dir / bars
    sidecar = Path(args.out) if args.out else default_sidecar(pool)
    if not sidecar.is_absolute():
        sidecar = skill_dir / sidecar
    try:
        result = enrich_pool(pool, bars, sidecar)
    except (OSError, BarsFormatError, FileNotFoundError, ValueError) as exc:
        LOG.warning("Setup-rule enricher failed: %s", exc)
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
