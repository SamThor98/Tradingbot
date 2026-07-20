#!/usr/bin/env python3
"""Materialize Stage-2 candidate research features into research_store/ Parquet.

Examples:
  python scripts/materialize_research_features.py --ticker AAPL --start 2020-01-01 --end 2020-12-31
  python scripts/materialize_research_features.py --tickers-file path/to/tickers.txt --start 2019-01-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.materialize import materialize_ticker  # noqa: E402
from research.paths import ensure_research_store_layout  # noqa: E402
from research.registry import FEATURE_SCHEMA_VERSION, load_feature_registry  # noqa: E402

LOG = get_logger("materialize_research_features")


def _load_tickers(args: argparse.Namespace) -> list[str]:
    tickers: list[str] = []
    if args.ticker:
        tickers.extend([t.strip().upper() for t in args.ticker if t.strip()])
    if args.tickers_file:
        text = Path(args.tickers_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tickers.append(line.split(",")[0].strip().upper())
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _fetch_bars(ticker: str, start: str | None, end: str | None):
    """Fetch daily bars; ``days`` derived from start when provided (cap 4000)."""
    from datetime import datetime, timezone

    import pandas as pd

    from market_data import get_daily_history

    days = 800
    if start:
        try:
            start_ts = datetime.strptime(start, "%Y-%m-%d")
            end_ts = (
                datetime.strptime(end, "%Y-%m-%d")
                if end
                else datetime.now(timezone.utc).replace(tzinfo=None)
            )
            days = max(300, min(4000, (end_ts - start_ts).days + 400))
        except ValueError:
            days = 800
    df = get_daily_history(ticker, days=days, skill_dir=SKILL_DIR)
    if df is None or getattr(df, "empty", True):
        return df
    if start:
        df = df.loc[df.index >= pd.Timestamp(start)]
    if end:
        df = df.loc[df.index <= pd.Timestamp(end)]
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", action="append", default=[], help="Ticker (repeatable)")
    parser.add_argument("--tickers-file", type=str, default=None, help="Text file of tickers")
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not write Parquet")
    parser.add_argument(
        "--allow-non-stage2",
        action="store_true",
        help="Materialize every asof in range (debug); default is Stage-2 candidates only",
    )
    args = parser.parse_args(argv)

    setup_logging()
    tickers = _load_tickers(args)
    if not tickers:
        LOG.error("Provide --ticker and/or --tickers-file")
        return 2

    reg = load_feature_registry()
    ensure_research_store_layout(SKILL_DIR, schema_version=FEATURE_SCHEMA_VERSION)
    LOG.info(
        "Registry schema_version=%s path=%s enabled_ohlcv=%s",
        reg.get("schema_version"),
        reg.get("_path"),
        sum(1 for f in reg["features"] if f.get("enabled") and f.get("data_source") == "ohlcv"),
    )

    total_rows = 0
    for ticker in tickers:
        try:
            bars = _fetch_bars(ticker, args.start, args.end)
        except Exception as exc:
            LOG.warning("History fetch failed for %s: %s", ticker, exc)
            continue
        if bars is None or getattr(bars, "empty", True):
            LOG.warning("No bars for %s", ticker)
            continue
        frame = materialize_ticker(
            ticker=ticker,
            bars=bars,
            start=args.start,
            end=args.end,
            skill_dir=SKILL_DIR,
            bar_provider="market_data",
            require_stage2=not args.allow_non_stage2,
            write=not args.dry_run,
        )
        n = len(frame)
        total_rows += n
        LOG.info("%s: materialized %s candidate rows", ticker, n)

    LOG.info("Done. total_rows=%s write=%s", total_rows, not args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
