"""
Historical large-cap universe membership (optional).

Drop a CSV under ``schwab_skill/data/sp1500_membership.csv`` to activate:

    ticker,start_date,end_date
    AAPL,2010-01-01,

Open ``end_date`` means still member as-of file publication. Dates are ISO.

Until the file exists, ``tickers_as_of`` returns ``None`` so callers keep using
the live watchlist without survivorship filtering.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import FrozenSet

SKILL_DIR = Path(__file__).resolve().parent
DEFAULT_MEMBERSHIP_PATH = SKILL_DIR / "data" / "sp1500_membership.csv"


def tickers_as_of(as_of: date, path: Path | None = None) -> FrozenSet[str] | None:
    csv_path = path or DEFAULT_MEMBERSHIP_PATH
    if not csv_path.exists():
        return None
    out: set[str] = set()
    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = str(row.get("ticker") or row.get("Ticker") or "").strip().upper()
                if not sym:
                    continue
                sd = str(row.get("start_date") or row.get("start") or "").strip()[:10]
                ed = str(row.get("end_date") or row.get("end") or "").strip()[:10]
                try:
                    start = date.fromisoformat(sd) if sd else date.min
                except ValueError:
                    continue
                if ed:
                    try:
                        end = date.fromisoformat(ed)
                    except ValueError:
                        continue
                else:
                    end = date.max
                if start <= as_of <= end:
                    out.add(sym)
    except OSError:
        return None
    return frozenset(out)
