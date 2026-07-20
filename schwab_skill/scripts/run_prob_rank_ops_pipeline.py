#!/usr/bin/env python3
"""Orchestrate prob-rank ops: materialize → train → dual-run → promotion.

Examples:
  # Offline smoke (synthetic bars + trades; no chunks required)
  python scripts/run_prob_rank_ops_pipeline.py --smoke

  # Real bars → train; CF/portfolio when --run-id chunks exist
  python scripts/run_prob_rank_ops_pipeline.py \\
      --ticker AAPL --ticker MSFT --ticker SPY \\
      --start 2019-01-01 --end 2024-06-01 \\
      --run-id control_legacy_aug
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402
from research.ops_pipeline import run_ops_pipeline  # noqa: E402

LOG = get_logger("run_prob_rank_ops_pipeline")


def _fetch_bars(ticker: str, start: str | None, end: str | None):
    from datetime import datetime, timezone

    import pandas as pd

    from market_data import get_daily_history

    # Trailing window from today must reach ``start`` (+ warmup), not start→end span.
    days = 800
    if start:
        try:
            start_ts = datetime.strptime(start, "%Y-%m-%d")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            days = max(300, min(5000, (now - start_ts).days + 400))
        except ValueError:
            days = 800
    df = get_daily_history(ticker, days=days, skill_dir=SKILL_DIR)
    if df is None or getattr(df, "empty", True):
        return df
    if end:
        df = df.loc[df.index <= pd.Timestamp(end)]
    return df


def _maybe_load_trades(run_id: str | None):
    if not run_id:
        return None
    try:
        from scripts.validate_scoring_metrics import _load_trade_frame

        trades = _load_trade_frame(run_id)
        if trades is None or getattr(trades, "empty", True):
            LOG.warning("No trades loaded for run_id=%s", run_id)
            return None
        LOG.info("Loaded %s trades for run_id=%s", len(trades), run_id)
        return trades
    except Exception as exc:
        LOG.warning("Could not load trades for %s: %s", run_id, exc)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="Synthetic end-to-end (no market/chunks)")
    parser.add_argument("--ticker", action="append", default=[], help="Ticker (repeatable)")
    parser.add_argument(
        "--tickers-file",
        type=str,
        default=None,
        help="Text file of tickers (one per line; # comments ok)",
    )
    parser.add_argument("--start", type=str, default="2019-01-01")
    parser.add_argument("--end", type=str, default="2024-06-01")
    parser.add_argument("--run-id", type=str, default=None, help="multi_era_chunks run id for CF/portfolio")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--sizing", type=str, default="equal", choices=["equal", "edge_vol"])
    parser.add_argument("--num-boost-round", type=int, default=80)
    parser.add_argument("--requested", type=str, default="shadow", choices=["shadow", "live"])
    parser.add_argument(
        "--artifact-dir",
        type=str,
        default=None,
        help="Output dir (default validation_artifacts/prob_rank_ops)",
    )
    parser.add_argument(
        "--skill-dir",
        type=str,
        default=None,
        help="Override skill root (smoke tests often use a temp dir)",
    )
    parser.add_argument("--apply", action="store_true", help="Append promotion decision to registry")
    args = parser.parse_args(argv)
    setup_logging()

    skill = Path(args.skill_dir) if args.skill_dir else SKILL_DIR
    art = Path(args.artifact_dir) if args.artifact_dir else None

    if args.smoke:
        result = run_ops_pipeline(
            skill_dir=skill,
            mode="smoke",
            date_start="2016-01-01",
            date_end="2024-06-01",
            top_n=args.top_n,
            sizing_mode=args.sizing,
            num_boost_round=args.num_boost_round,
            requested_promotion=args.requested,
            artifact_dir=art,
            apply_registry=bool(args.apply),
        )
    else:
        tickers = [t.strip().upper() for t in args.ticker if t.strip()]
        if args.tickers_file:
            text = Path(args.tickers_file).read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                tickers.append(line.split(",")[0].strip().upper())
        seen: set[str] = set()
        unique: list[str] = []
        for t in tickers:
            if t and t not in seen:
                seen.add(t)
                unique.append(t)
        tickers = unique
        if not tickers:
            LOG.error("Provide --smoke, --ticker, and/or --tickers-file")
            return 2
        ticker_bars = {}
        for t in tickers:
            bars = _fetch_bars(t, args.start, args.end)
            if bars is None or getattr(bars, "empty", True):
                LOG.warning("No bars for %s", t)
                continue
            ticker_bars[t] = bars
        if not ticker_bars:
            LOG.error("No bars fetched")
            return 2
        trades = _maybe_load_trades(args.run_id)
        result = run_ops_pipeline(
            skill_dir=skill,
            mode="bars",
            ticker_bars=ticker_bars,
            trades=trades,
            date_start=args.start,
            date_end=args.end,
            top_n=args.top_n,
            sizing_mode=args.sizing,
            num_boost_round=args.num_boost_round,
            requested_promotion=args.requested,
            artifact_dir=art,
            apply_registry=bool(args.apply),
        )

    print(json.dumps(result.to_dict(), indent=2, default=str))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
