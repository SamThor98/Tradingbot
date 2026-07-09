#!/usr/bin/env python3
"""Pre-warm Finnhub earnings cache for PEAD under Schwab-only backtests.

Usage (from schwab_skill/):
  python scripts/warm_earnings_cache.py
  python scripts/warm_earnings_cache.py --smoke
  python scripts/warm_earnings_cache.py --limit 50 --force
  python scripts/warm_earnings_cache.py --tickers AAPL,MSFT,NVDA
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from logger_setup import get_logger, setup_logging  # noqa: E402

LOG = get_logger(__name__)


def _load_universe_tickers() -> list[str]:
    from watchlist_loader import _fallback_watchlist, _load_cached, load_full_watchlist

    cached = _load_cached()
    if cached and cached[0]:
        return [str(t).strip().upper() for t in cached[0] if str(t).strip()]
    try:
        wl = load_full_watchlist(force_refresh=False)
        if wl:
            return [str(t).strip().upper() for t in wl if str(t).strip()]
    except Exception:
        pass
    return _fallback_watchlist()


def _parse_tickers(raw: str) -> list[str]:
    out: list[str] = []
    for part in str(raw or "").split(","):
        sym = part.strip().upper()
        if sym:
            out.append(sym)
    return out


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Warm Finnhub earnings cache for PEAD enrichment.")
    parser.add_argument(
        "--tickers",
        default="",
        help="Comma-separated tickers (default: full SP1500 watchlist).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Cap universe to first N tickers after dedupe (0 = no cap).",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Smoke mode: warm first 5 universe tickers.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore fresh cache entries and refetch all tickers.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore warm progress checkpoint and process full list.",
    )
    args = parser.parse_args()

    from earnings_signal import (
        _resolve_pead_provider,
        earnings_cache_summary,
        warm_earnings_for_tickers,
    )

    provider = _resolve_pead_provider(SKILL_DIR)
    if provider == "off":
        print("[warm-earnings] PEAD provider is off; set FINNHUB_API_KEY and PEAD_DATA_PROVIDER=finnhub")
        return 2
    if provider != "finnhub":
        print(f"[warm-earnings] provider={provider}; warm script supports finnhub only")
        return 2

    if args.tickers.strip():
        tickers = _parse_tickers(args.tickers)
    else:
        tickers = _load_universe_tickers()
    if args.smoke:
        tickers = tickers[:5]
    elif args.limit and args.limit > 0:
        tickers = tickers[: args.limit]

    if not tickers:
        print("[warm-earnings] no tickers to warm")
        return 1

    before = earnings_cache_summary(tickers, skill_dir=SKILL_DIR)
    print(
        f"[warm-earnings] provider={provider} universe={before['total']} "
        f"fresh={before['fresh']} missing={before['missing']}"
    )
    summary = warm_earnings_for_tickers(
        tickers,
        skill_dir=SKILL_DIR,
        force=bool(args.force),
        resume=not args.no_resume,
    )
    after = earnings_cache_summary(tickers, skill_dir=SKILL_DIR)
    print(
        "[warm-earnings] done "
        f"fetched={summary.get('fetched')} skipped={summary.get('skipped')} "
        f"errors={summary.get('errors')} fresh_after={after.get('fresh')}/{after.get('total')}"
    )
    if summary.get("failed_tickers"):
        sample = ", ".join(list(summary["failed_tickers"])[:8])
        print(f"[warm-earnings] failed sample: {sample}")
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
