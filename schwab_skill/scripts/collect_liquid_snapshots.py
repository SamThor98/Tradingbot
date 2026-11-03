"""Collect one round of Liquid (xyz) funding/OI snapshots.

Shadow-first: snapshots are stored point-in-time in ``liquid_snapshots``
for later backtest joins. Nothing here influences rank, scoring, or
execution. One provider call covers every xyz market.

Usage:
    python scripts/collect_liquid_snapshots.py --force          # collect now
    python scripts/collect_liquid_snapshots.py                  # only if LIQUID_COLLECT_ENABLED
    python scripts/collect_liquid_snapshots.py --funding AAPL   # print funding-history span
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.liquid_positioning.provider import HyperliquidXyzProvider  # noqa: E402
from core.liquid_positioning.settings import build_liquid_config  # noqa: E402
from core.liquid_positioning.store import ensure_table, snapshot_coverage, store_snapshots  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Collect even if LIQUID_COLLECT_ENABLED is off")
    parser.add_argument("--funding", metavar="TICKER", help="Print funding-history span for one ticker and exit")
    args = parser.parse_args()

    config = build_liquid_config()
    if config.mode == "off":
        print(json.dumps({"skipped": "LIQUID_OVERLAY_MODE=off"}))
        return 0
    if not (config.collect_enabled or args.force):
        print(json.dumps({"skipped": "LIQUID_COLLECT_ENABLED off (use --force)"}))
        return 0

    provider = HyperliquidXyzProvider(config)

    if args.funding:
        rows = provider.get_funding_history(args.funding)
        if rows:
            first = datetime.fromtimestamp(rows[0]["time"] / 1000, tz=timezone.utc)
            last = datetime.fromtimestamp(rows[-1]["time"] / 1000, tz=timezone.utc)
            print(json.dumps({
                "ticker": args.funding.upper(), "rows": len(rows),
                "first": first.isoformat(), "last": last.isoformat(),
            }, indent=2))
        else:
            print(json.dumps({"ticker": args.funding.upper(), "rows": 0}))
        return 0

    ensure_table()
    snapshots, diag = provider.get_snapshots()
    diag.stored = store_snapshots(snapshots)
    payload = {
        "mode": config.mode,
        "dex": config.dex,
        "diagnostics": diag.to_dict(),
        "coverage": snapshot_coverage(),
        "top_oi": [s.to_dict() for s in sorted(
            snapshots, key=lambda x: -(x.open_interest_usd or 0))[:5]],
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0 if diag.provider_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
