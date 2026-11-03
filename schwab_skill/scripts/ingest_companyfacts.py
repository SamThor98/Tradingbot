#!/usr/bin/env python3
"""Ingest SEC companyfacts for a ticker list or the live watchlist.

One XBRL document per CIK, paced so we stay under EDGAR's fair-access limit.
Does not call Yahoo. Recompute in the Research pricing panel is local.

    python scripts/ingest_companyfacts.py --tickers AAPL,MSFT
    python scripts/ingest_companyfacts.py --watchlist --limit 40 --delay 0.25
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402

LOG = get_logger("ingest_companyfacts")


def _tickers_from_args(args: argparse.Namespace) -> list[str]:
    out: list[str] = []
    raw = str(args.tickers or "").strip()
    if raw:
        out.extend(part.strip().upper() for part in raw.replace(";", ",").split(",") if part.strip())
    if args.watchlist:
        from watchlist_loader import load_full_watchlist

        out.extend(str(t).strip().upper() for t in load_full_watchlist() if str(t).strip())
    # Preserve order, drop dupes.
    seen: set[str] = set()
    unique: list[str] = []
    for tkr in out:
        if tkr in seen:
            continue
        seen.add(tkr)
        unique.append(tkr)
    if args.limit and args.limit > 0:
        unique = unique[: int(args.limit)]
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", default="", help="Comma-separated symbols")
    parser.add_argument("--watchlist", action="store_true", help="Include the live watchlist")
    parser.add_argument("--limit", type=int, default=0, help="Cap after de-dupe (0 = no cap)")
    parser.add_argument("--delay", type=float, default=0.25, help="Seconds between EDGAR requests")
    parser.add_argument("--force", action="store_true", help="Bypass a warm companyfacts cache")
    args = parser.parse_args(argv)
    setup_logging()

    tickers = _tickers_from_args(args)
    if not tickers:
        LOG.error("No tickers. Pass --tickers AAPL,MSFT and/or --watchlist.")
        return 2

    from core.companyfacts.service import ingest_ticker

    ok = 0
    failed = 0
    for i, ticker in enumerate(tickers):
        payload = ingest_ticker(ticker, skill_dir=SKILL_DIR, force=bool(args.force))
        status = str(payload.get("status") or "")
        if status in {"ok", "degraded"}:
            ok += 1
            LOG.info("%s %s", ticker, status)
        else:
            failed += 1
            LOG.warning("%s %s %s", ticker, status, payload.get("reason"))
        if i + 1 < len(tickers) and args.delay > 0:
            time.sleep(float(args.delay))
    LOG.info("ingest complete: ok=%s failed=%s of %s", ok, failed, len(tickers))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
