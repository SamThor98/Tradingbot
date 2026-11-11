#!/usr/bin/env python3
"""Rebuild prompt-lab cases from saved backtest chunks. Does not place orders.

``--dry-run`` prints counts by era and week, unfilled field groups, and a
leakage audit. It does not call Schwab.

``--fetch`` downloads one date-ranged history per symbol and writes
``.tab_agent_rebuild.jsonl``. That hits the broker. Run it only after an
explicit OK. Bars are kept in memory and flushed to one cache file.

From ``schwab_skill/``::

    python scripts/tab_agent_rebuild.py --dry-run
    python scripts/tab_agent_rebuild.py --fetch
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.tab_agent_rebuild import (  # noqa: E402
    BAR_CACHE,
    BarCache,
    build_rebuild_cases,
    dry_run_report,
    iter_backtest_entries,
    schwab_history_fetch,
    write_rebuild_cases,
)

DEFAULT_ROOT = SKILL_DIR / "validation_artifacts" / "multi_era_chunks" / "control_legacy_aug_sp1500_full"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild lab cases from backtest chunks. No orders.")
    parser.add_argument("--skill-dir", type=Path, default=SKILL_DIR)
    parser.add_argument("--chunk-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="Print the audit. Does not fetch bars.")
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch date-ranged daily bars and write the rebuild file. Requires an explicit OK.",
    )
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--end-date", default="2026-11-11")
    args = parser.parse_args(argv)
    if args.fetch and args.dry_run:
        print("Pass either --dry-run or --fetch, not both.", file=sys.stderr)
        return 2
    if not args.fetch:
        report = dry_run_report(args.chunk_root)
        print(json.dumps(report, indent=2))
        return 0 if report.get("ok", True) else 1
    entries = list(iter_backtest_entries(args.chunk_root))
    cache = BarCache(args.skill_dir / BAR_CACHE)
    fetch = schwab_history_fetch(args.skill_dir)
    provider = cache.provider(fetch, start_date=args.start_date, end_date=args.end_date)
    cases = build_rebuild_cases(entries, provider, args.skill_dir)
    cache.flush()
    path = write_rebuild_cases(args.skill_dir, cases)
    print(json.dumps({"ok": True, "fetched": True, "cases": len(cases), "path": str(path), "orders_touched": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
