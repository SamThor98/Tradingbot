#!/usr/bin/env python3
"""Build a frozen probabilistic-ranking dataset (features + labels).

Example:
  python scripts/build_rank_dataset.py --ticker AAPL --ticker MSFT --start 2019-01-01 --end 2024-06-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.dataset import build_rank_dataset, load_feature_panels  # noqa: E402
from research.registry import FEATURE_SCHEMA_VERSION  # noqa: E402

LOG = get_logger("build_rank_dataset")


def _fetch_bars(ticker: str, start: str | None, end: str | None):
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
    # Keep warm-up history before start for indicators, but materializer filters asofs
    if end:
        df = df.loc[df.index <= pd.Timestamp(end)]
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", action="append", default=[], help="Ticker (repeatable)")
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--label-set", type=str, default="fwd40+strategy")
    parser.add_argument("--candidate-set", type=str, default="stage2_pass_v1")
    parser.add_argument("--schema-version", type=int, default=FEATURE_SCHEMA_VERSION)
    parser.add_argument(
        "--from-panels",
        action="store_true",
        help="Load features from research_store panels (still requires --ticker bars for fwd labels)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    setup_logging()

    tickers = [t.strip().upper() for t in args.ticker if t.strip()]
    if not tickers and not args.from_panels:
        LOG.error("Provide --ticker (and/or --from-panels with prior materialization)")
        return 2

    ticker_bars: dict = {}
    for t in tickers:
        bars = _fetch_bars(t, args.start, args.end)
        if bars is None or getattr(bars, "empty", True):
            LOG.warning("No bars for %s", t)
            continue
        ticker_bars[t] = bars

    features = None
    if args.from_panels:
        features = load_feature_panels(
            skill_dir=SKILL_DIR,
            schema_version=args.schema_version,
            tickers=tickers or None,
            date_start=args.start,
            date_end=args.end,
        )
        LOG.info("Loaded %s feature rows from panels", len(features))

    try:
        ds, path, manifest = build_rank_dataset(
            candidate_set=args.candidate_set,
            schema_version=args.schema_version,
            date_start=args.start,
            date_end=args.end,
            label_set=args.label_set,
            skill_dir=SKILL_DIR,
            ticker_bars=ticker_bars or None,
            features=features,
            write=not args.dry_run,
        )
    except Exception as exc:
        LOG.exception("Dataset build failed: %s", exc)
        return 1

    LOG.info(
        "dataset_id=%s rows=%s features=%s path=%s",
        manifest.get("dataset_id"),
        len(ds),
        len(manifest.get("feature_columns") or []),
        path,
    )
    if manifest.get("leakage", {}).get("warnings"):
        for w in manifest["leakage"]["warnings"]:
            LOG.warning("leakage warning: %s", w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
