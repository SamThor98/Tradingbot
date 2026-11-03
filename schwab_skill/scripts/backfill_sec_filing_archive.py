#!/usr/bin/env python3
"""Fill the SEC filing archive for the scoring-sample tickers.

The live six-filing cache is left alone. This walks EDGAR submission shards
until each ticker has a 10-K, 10-Q, or 8-K on or before 2016-01-01.

Example:
    python scripts/backfill_sec_filing_archive.py
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from config import get_edgar_user_agent, is_real_edgar_user_agent  # noqa: E402
from scripts.run_scoring_trades_backtest import SAMPLE_TICKERS  # noqa: E402
from sec_enrichment import backfill_filing_archive  # noqa: E402


def main() -> int:
    agent = get_edgar_user_agent(SKILL_DIR)
    if not is_real_edgar_user_agent(agent):
        print("SEC filing archive was not filled. Set EDGAR_USER_AGENT to a real contact address.")
        return 2
    result = backfill_filing_archive(
        SAMPLE_TICKERS,
        skill_dir=SKILL_DIR,
        earliest="2016-01-01",
        user_agent=agent,
        sleep_s=0.15,
    )
    print(
        f"ok={result.get('ok')} tickers={result.get('tickers')} "
        f"requests={result.get('requests')} earliest={result.get('earliest', '')}"
    )
    if result.get("error"):
        print(result["error"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
