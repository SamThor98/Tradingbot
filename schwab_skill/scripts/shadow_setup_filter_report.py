#!/usr/bin/env python3
"""Report shadow setup-filter keep/drop counts and forward returns.

Reads ``data/shadow_setup_filter/observations.jsonl``. Does not change scan
output, ranking, alerts, or orders. ``--resolve`` fills 5/10/20-day returns
from the price cache, then from the existing daily-history path. It is not
scheduled.

Examples:
  python scripts/shadow_setup_filter_report.py
  python scripts/shadow_setup_filter_report.py --resolve
  python scripts/shadow_setup_filter_report.py --resolve --no-network
  python scripts/shadow_setup_filter_report.py --benchmark
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from core.shadow_setup_filter import (  # noqa: E402
    load_rows,
    measure_local_overhead,
    render_report,
    resolve_outcomes,
    summarize_rows,
    thresholds_from_config,
)
from logger_setup import get_logger, setup_logging  # noqa: E402

LOG = get_logger("shadow_setup_filter_report")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, default=SKILL_DIR)
    parser.add_argument(
        "--resolve",
        action="store_true",
        help="Fill forward returns from cached prices, then the existing daily-history path",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="With --resolve, do not call market data when the price cache misses",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Print local CPU milliseconds for the scan-time work and exit",
    )
    args = parser.parse_args(argv)
    setup_logging()
    skill_dir = Path(args.skill_dir)

    if args.benchmark:
        timing = measure_local_overhead()
        print(json.dumps(timing, sort_keys=True))
        LOG.info(
            "Shadow setup overhead probe_ms=%s record_ms=%s",
            timing.get("sma50_probe_ms"),
            timing.get("record_ms"),
        )
        return 0

    if args.resolve:
        result = resolve_outcomes(skill_dir, allow_network=not args.no_network)
        LOG.info(
            "Shadow setup resolve wrote=%s pending=%s skipped=%s",
            result.get("written"),
            result.get("pending"),
            result.get("skipped"),
        )

    limits = thresholds_from_config(skill_dir)
    summary = summarize_rows(load_rows(skill_dir), horizons=limits.horizons)
    print(render_report(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
