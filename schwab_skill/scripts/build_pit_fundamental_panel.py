#!/usr/bin/env python3
"""Fetch PIT fundamentals + insider Form 4 history for a frozen trade book.

Does not run a backtest and does not touch live scanning. Reads the trade keys
from existing multi-era chunks, pulls Sharadar as-of filings for those tickers,
and caches two parquet files so the confidence screen can re-run offline.

Fail-closed: if the Sharadar key is missing or the API errors, the cache is not
written and the exit code is non-zero. Thin coverage is reported, never filled.

Usage (from schwab_skill/):
  python scripts/build_pit_fundamental_panel.py
  python scripts/build_pit_fundamental_panel.py --run-id control_legacy_aug
  python scripts/build_pit_fundamental_panel.py --skip-insiders
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.pit_fundamentals import (  # noqa: E402
    fetch_fundamentals_frame,
    fetch_insider_events,
)
from research.providers.sharadar_pit import SharadarPITAdapter  # noqa: E402
from scripts.validate_scoring_metrics import _load_trade_frame  # noqa: E402

PANEL_DIR = SKILL_DIR / "research_store" / "panels"
DEFAULT_RUN_ID = "control_legacy_aug"
# Fundamentals are TTM, so reach back before the first entry to cover staleness.
FUNDAMENTAL_LOOKBACK_YEARS = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    # The API rejects oversized ticker lists; 20 is the largest that holds up.
    parser.add_argument("--fundamentals-batch", type=int, default=20)
    parser.add_argument("--insiders-batch", type=int, default=10)
    parser.add_argument("--skip-insiders", action="store_true")
    parser.add_argument("--limit-tickers", type=int, default=0, help="debug: cap ticker count")
    args = parser.parse_args(argv)

    try:
        trades = _load_trade_frame(args.run_id)
    except Exception as exc:
        print(f"FAIL: could not load trades for {args.run_id}: {exc}")
        return 2

    trades["entry_date"] = pd.to_datetime(trades["entry_date"], errors="coerce")
    tickers = sorted({str(t).upper() for t in trades["ticker"].dropna() if str(t).strip()})
    if args.limit_tickers:
        tickers = tickers[: args.limit_tickers]
    first_entry = trades["entry_date"].min()
    start = (first_entry - pd.DateOffset(years=FUNDAMENTAL_LOOKBACK_YEARS)).strftime("%Y-%m-%d")
    print(
        f"Run {args.run_id}: {len(trades)} trades, {len(tickers)} tickers, "
        f"entries {first_entry.date()} .. {trades['entry_date'].max().date()}"
    )
    print(f"Fetching Sharadar history from {start}")

    adapter = SharadarPITAdapter(skill_dir=SKILL_DIR)
    if not adapter._key():
        print("FAIL: no Sharadar API key; cannot build a PIT fundamental panel.")
        return 2

    PANEL_DIR.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "run_id": args.run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "n_trades": int(len(trades)),
        "n_tickers": len(tickers),
        "history_start": start,
        "provider": "sharadar",
        "pit_key": "filing date (Sharadar `date`), strictly before entry",
    }

    fundamentals, fund_errors = fetch_fundamentals_frame(
        adapter, tickers, start=start, batch_size=args.fundamentals_batch
    )
    print(f"Fundamentals: {len(fundamentals)} filings, {fundamentals['ticker'].nunique() if not fundamentals.empty else 0} tickers")
    if fund_errors:
        for err in fund_errors[:10]:
            print(f"  fundamentals error: {err}")
    if fundamentals.empty:
        print("FAIL: no fundamental rows returned; refusing to write an empty panel.")
        print(f"  provider data_quality: {adapter.data_quality()}")
        return 2
    fund_path = PANEL_DIR / f"pit_fundamentals_{args.run_id}.parquet"
    fundamentals.to_parquet(fund_path, index=False)
    meta["fundamentals"] = {
        "path": str(fund_path.relative_to(SKILL_DIR)),
        "rows": int(len(fundamentals)),
        "tickers": int(fundamentals["ticker"].nunique()),
        "ticker_coverage": round(fundamentals["ticker"].nunique() / max(1, len(tickers)), 4),
        "errors": fund_errors,
    }
    print(f"Wrote {fund_path.relative_to(SKILL_DIR)}")

    if not args.skip_insiders:
        insiders, ins_errors = fetch_insider_events(
            adapter, tickers, start=start, batch_size=args.insiders_batch
        )
        print(
            f"Insiders: {len(insiders)} ticker-days, "
            f"{insiders['ticker'].nunique() if not insiders.empty else 0} tickers"
        )
        if ins_errors:
            for err in ins_errors[:10]:
                print(f"  insiders error: {err}")
        if insiders.empty:
            print("WARN: no insider rows returned; insider features will be absent.")
            meta["insiders"] = {"rows": 0, "errors": ins_errors}
        else:
            ins_path = PANEL_DIR / f"pit_insiders_{args.run_id}.parquet"
            insiders.to_parquet(ins_path, index=False)
            meta["insiders"] = {
                "path": str(ins_path.relative_to(SKILL_DIR)),
                "rows": int(len(insiders)),
                "tickers": int(insiders["ticker"].nunique()),
                "ticker_coverage": round(insiders["ticker"].nunique() / max(1, len(tickers)), 4),
                "errors": ins_errors,
            }
            print(f"Wrote {ins_path.relative_to(SKILL_DIR)}")
    else:
        # Skipping the fetch does not mean skipping the data: report the cache.
        ins_path = PANEL_DIR / f"pit_insiders_{args.run_id}.parquet"
        if ins_path.is_file():
            cached = pd.read_parquet(ins_path)
            meta["insiders"] = {
                "path": str(ins_path.relative_to(SKILL_DIR)),
                "rows": int(len(cached)),
                "tickers": int(cached["ticker"].nunique()),
                "ticker_coverage": round(cached["ticker"].nunique() / max(1, len(tickers)), 4),
                "fetch_skipped_reused_cache": True,
            }
            print(f"Reusing cached insiders: {len(cached)} ticker-days")
        else:
            meta["insiders"] = {"skipped": True, "cache_present": False}

    meta_path = PANEL_DIR / f"pit_panel_meta_{args.run_id}.json"
    meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {meta_path.relative_to(SKILL_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
