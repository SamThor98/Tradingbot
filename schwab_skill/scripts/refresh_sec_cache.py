#!/usr/bin/env python3
"""Force-refresh SEC cache entries and print session data_quality."""

from __future__ import annotations

import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

REFRESH_TICKERS = [
    "SPY",
    "QQQ",
    "IWM",
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "JPM",
    "XOM",
    "UNH",
    "JNJ",
    "V",
    "PG",
]


def main() -> int:
    from config import get_edgar_user_agent
    from data_health import assess_scan_session_data_health
    from sec_enrichment import fetch_sec_snapshot
    from schwab_auth import DualSchwabAuth

    ua = get_edgar_user_agent(SKILL_DIR)
    ok = fail = 0
    for ticker in REFRESH_TICKERS:
        snap = fetch_sec_snapshot(
            ticker,
            skill_dir=SKILL_DIR,
            user_agent=ua,
            cache_hours=0.0,
            enabled=True,
        )
        if snap.get("ok"):
            ok += 1
            print(
                f"OK {ticker} from_cache={snap.get('from_cache')} risk={snap.get('risk_tag')}",
                flush=True,
            )
        else:
            fail += 1
            print(f"FAIL {ticker}: {snap.get('error')}", flush=True)
        time.sleep(0.25)

    print(f"refresh_done ok={ok} fail={fail}", flush=True)
    auth = DualSchwabAuth(skill_dir=SKILL_DIR)
    dq = assess_scan_session_data_health(auth, skill_dir=SKILL_DIR)
    print(f"session_dq={dq.get('data_quality')} reasons={dq.get('reasons')}", flush=True)
    details = dq.get("details") or {}
    print(f"sec_cache_latest_ts={details.get('sec_cache_latest_ts')}", flush=True)
    reasons = list(dq.get("reasons") or [])
    if any(str(r).startswith("sec_cache_stale") or r == "sec_cache_empty_or_missing" for r in reasons):
        print("FAIL: SEC cache still stale/missing after refresh")
        return 1
    print("PASS: SEC cache freshness ok for DQ gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
