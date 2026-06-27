#!/usr/bin/env python3
"""Build the offline scoring audit dataset (Stage2+VCP candidates with forward labels).

Writes ``validation_artifacts/scoring_audit_dataset.csv`` for use by
``validate_scoring_metrics.py``. This is cheaper than full backtests and
measures whether individual score components predict forward returns.

Example:
    python scripts/build_scoring_audit_dataset.py --max-tickers 80
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
DEFAULT_OUT = ARTIFACT_DIR / "scoring_audit_dataset.csv"

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build scoring audit dataset from Stage2+VCP history.")
    parser.add_argument("--start-date", default="", help="YYYY-MM-DD (default: 3y ago)")
    parser.add_argument("--end-date", default="", help="YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--full-history",
        action="store_true",
        help="Use ~10y history instead of the default 3y audit window.",
    )
    parser.add_argument("--max-tickers", type=int, default=0, help="Cap watchlist size (0 = all)")
    parser.add_argument(
        "--skip-live-stack",
        action="store_true",
        help="Skip advisory + score_stack enrichment (faster, less accurate).",
    )
    parser.add_argument(
        "--with-mirofish",
        action="store_true",
        help="Run MiroFish on audit rows (slow; capped by --mirofish-max-rows).",
    )
    parser.add_argument(
        "--mirofish-max-rows",
        type=int,
        default=200,
        help="Max audit rows to enrich with MiroFish when --with-mirofish (default 200).",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output CSV path")
    args = parser.parse_args()

    from advisory_model import build_advisory_dataset

    max_tickers = int(args.max_tickers) if args.max_tickers > 0 else None
    end = args.end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.start_date:
        start = args.start_date
    elif args.full_history:
        start = (datetime.now(timezone.utc) - timedelta(days=3652)).strftime("%Y-%m-%d")
    else:
        start = (datetime.now(timezone.utc) - timedelta(days=365 * 3)).strftime("%Y-%m-%d")

    print("Building scoring audit dataset (Stage2+VCP rows with forward labels)...")
    mirofish_max = int(args.mirofish_max_rows) if args.with_mirofish else None
    df = build_advisory_dataset(
        skill_dir=SKILL_DIR,
        start_date=start,
        end_date=end,
        max_tickers=max_tickers,
        include_mirofish=bool(args.with_mirofish),
        mirofish_max_rows=mirofish_max,
    )
    if df.empty:
        print("FAIL: dataset builder returned zero rows")
        return 1

    stack_source = "base_only"
    if not args.skip_live_stack:
        print("Enriching rows with live advisory + score stack (scanner parity)...")
        from core.scoring_audit_builder import enrich_with_live_score_stack

        df = enrich_with_live_score_stack(df, skill_dir=SKILL_DIR)
        stack_source = "live"

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    meta_path = out_path.with_suffix(".meta.json")
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(df)),
        "tickers": int(df["ticker"].nunique()),
        "date_min": str(df["entry_date"].min()),
        "date_max": str(df["entry_date"].max()),
        "path": str(out_path),
        "score_stack_source": stack_source,
        "window_start": start,
        "window_end": end,
        "mirofish_enabled": bool(args.with_mirofish),
        "mirofish_max_rows": mirofish_max,
        "mirofish_included_rows": int(df["mirofish_included"].sum()) if "mirofish_included" in df.columns else 0,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Window: {start} -> {end}")
    print(f"Wrote {out_path} ({len(df)} rows, {df['ticker'].nunique()} tickers)")
    print(f"Wrote {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
