"""Alpha Vantage helpers for PEAD historical EPS surprises."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from config import get_alpha_vantage_api_key

LOG = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parent
AV_BASE = "https://www.alphavantage.co/query"


def get_alphavantage_earnings_history(
    ticker: str,
    *,
    skill_dir: Path | None = None,
) -> dict[str, Any]:
    """Fetch quarterly EPS actual/estimate history via Alpha Vantage ``EARNINGS``.

    Free tier is rate-limited (~25 req/day); prefer Finnhub warm for bulk, use
    AV as a depth backfill when a key is configured.
    """
    sd = skill_dir or SKILL_DIR
    sym = str(ticker or "").strip().upper()
    api_key = get_alpha_vantage_api_key(sd)
    if not api_key:
        return {
            "ok": False,
            "ticker": sym,
            "rows": [],
            "errors": ["alpha_vantage_api_key_missing"],
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
    if not sym:
        return {
            "ok": False,
            "ticker": sym,
            "rows": [],
            "errors": ["ticker_empty"],
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    try:
        resp = requests.get(
            AV_BASE,
            params={"function": "EARNINGS", "symbol": sym, "apikey": api_key},
            timeout=30,
        )
        if resp.status_code != 200:
            errors.append(f"http_{resp.status_code}")
        else:
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("Note"):
                errors.append("rate_limited")
            elif isinstance(payload, dict) and payload.get("Information"):
                errors.append(str(payload.get("Information"))[:120])
            else:
                quarterly = payload.get("quarterlyEarnings") if isinstance(payload, dict) else None
                if isinstance(quarterly, list):
                    for row in quarterly:
                        if not isinstance(row, dict):
                            continue
                        # Prefer reportedDate (announcement) over fiscalDateEnding.
                        date_str = str(row.get("reportedDate") or row.get("fiscalDateEnding") or "").strip()
                        if not date_str:
                            continue
                        try:
                            actual = float(row["reportedEPS"]) if row.get("reportedEPS") not in (None, "None") else None
                        except (TypeError, ValueError):
                            actual = None
                        try:
                            estimate = (
                                float(row["estimatedEPS"]) if row.get("estimatedEPS") not in (None, "None") else None
                            )
                        except (TypeError, ValueError):
                            estimate = None
                        if actual is None and estimate is None:
                            continue
                        rows.append(
                            {
                                "date": date_str[:10],
                                "actual_eps": actual,
                                "estimate_eps": estimate,
                                "source": "alphavantage/EARNINGS",
                            }
                        )
    except Exception as exc:
        LOG.debug("Alpha Vantage earnings fetch failed for %s: %s", sym, exc)
        errors.append(f"exception:{type(exc).__name__}")
    rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return {
        "ok": bool(rows),
        "ticker": sym,
        "rows": rows,
        "errors": errors if not rows else [],
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
