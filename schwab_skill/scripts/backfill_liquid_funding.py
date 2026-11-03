"""Backfill Liquid (xyz) funding history into daily aggregates.

Default universe: tickers from the last 90 days of scan_results that are
actually listed on xyz — the only names a backtest can ever join.
    python scripts/backfill_liquid_funding.py
    python scripts/backfill_liquid_funding.py --tickers AAPL,NVDA,DELL
    python scripts/backfill_liquid_funding.py --all
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.liquid_positioning.backfill import backfill_funding_for_tickers  # noqa: E402
from core.liquid_positioning.provider import HyperliquidXyzProvider  # noqa: E402
from core.liquid_positioning.settings import build_liquid_config  # noqa: E402
from core.liquid_positioning.store import ensure_table  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent


def _scan_tickers(days: int = 90) -> set[str]:
    db = SKILL_DIR / "webapp" / "webapp.db"
    if not db.exists():
        return set()
    con = sqlite3.connect(str(db))
    try:
        rows = con.execute(
            "SELECT DISTINCT ticker FROM scan_results WHERE created_at >= datetime('now', ?)",
            (f"-{int(days)} days",),
        ).fetchall()
    finally:
        con.close()
    return {str(r[0]).upper() for r in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", help="Comma-separated tickers (default: covered scan tickers)")
    parser.add_argument("--all", action="store_true", help="Every xyz market")
    parser.add_argument("--days", type=int, default=90, help="Scan-results lookback for the default universe")
    args = parser.parse_args()

    provider = HyperliquidXyzProvider(build_liquid_config())
    snapshots, diag = provider.get_snapshots()
    if diag.provider_errors:
        print(json.dumps({"error": "provider_failed", "diagnostics": diag.to_dict()}))
        return 1
    universe = [s.ticker for s in snapshots]

    if args.tickers:
        wanted = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.all:
        wanted = universe
    else:
        wanted = sorted(_scan_tickers(args.days) & set(universe))

    ensure_table()
    result = backfill_funding_for_tickers(provider, wanted)
    result["universe_size"] = len(universe)
    result["requested"] = wanted
    print(json.dumps(result, indent=2))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
