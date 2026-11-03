"""Liquid (xyz perp) positioning API — read-only, shadow layer.

``GET /api/liquid/overview``   collector status + latest market snapshots
``GET /api/liquid/{ticker}``   one market: snapshot, funding history, features

Market-wide public data (Hyperliquid xyz), so rows are global rather than
tenant-scoped, same as the event-probability catalog. This layer never
touches scan scores, ranking, sizing, or execution.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from ..recovery_map import map_failure as _map_failure
from ..route_helpers import require_api_key_if_set as _require_api_key_if_set
from ..schemas import ApiResponse

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["liquid"], dependencies=[Depends(_require_api_key_if_set)])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent

_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err(endpoint: str, exc: Exception) -> ApiResponse:
    mapped = _map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    return ApiResponse(ok=False, error=headline, data={"recovery": mapped})


@router.get("/api/liquid/overview", response_model=ApiResponse)
def liquid_overview() -> ApiResponse:
    """Collector health plus the latest snapshot board (OI leaders, funding extremes)."""
    try:
        from config import get_liquid_min_oi_usd, get_liquid_overlay_mode
        from core.liquid_positioning.store import get_latest_snapshots, snapshot_coverage

        snapshots = get_latest_snapshots(SKILL_DIR)
        liquid_enough = [s for s in snapshots if not s["is_thin"]]
        by_funding = sorted(
            (s for s in liquid_enough if s["funding_rate"] is not None),
            key=lambda s: -abs(s["funding_rate"] or 0),
        )
        return _ok(
            {
                "mode": get_liquid_overlay_mode(SKILL_DIR),
                "min_oi_usd": get_liquid_min_oi_usd(SKILL_DIR),
                "coverage": snapshot_coverage(SKILL_DIR),
                "thin_count": sum(1 for s in snapshots if s["is_thin"]),
                "top_open_interest": snapshots[:25],
                "funding_extremes": by_funding[:10],
                "note": "Perp positioning from Liquid's xyz markets (Hyperliquid). Not Schwab order flow. Thin markets (< min OI) are no-signal.",
            }
        )
    except Exception as exc:
        LOG.exception("liquid_overview failed")
        return _err("liquid_overview", exc)


@router.get("/api/liquid/{ticker}", response_model=ApiResponse)
def liquid_for_ticker(ticker: str) -> ApiResponse:
    """Latest snapshot + daily funding history + as-of funding features for one symbol."""
    symbol = str(ticker or "").strip().upper()
    if not symbol or not _TICKER_RE.match(symbol):
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL).")
    try:
        from datetime import datetime, timezone

        from core.liquid_positioning.features import funding_features_asof
        from core.liquid_positioning.store import get_funding_daily, get_latest_snapshots

        snapshots = get_latest_snapshots(SKILL_DIR)
        snap = next((s for s in snapshots if s["ticker"] == symbol), None)
        if snap is None:
            return _ok({"ticker": symbol, "status": "not_on_liquid", "snapshot": None})
        daily = get_funding_daily(symbol, days=365, skill_dir=SKILL_DIR)
        today = datetime.now(timezone.utc).date()
        return _ok(
            {
                "ticker": symbol,
                "status": "thin_no_signal" if snap["is_thin"] else "ok",
                "snapshot": snap,
                "funding_daily": daily[-180:],
                "features": funding_features_asof(daily, date(today.year, today.month, today.day)),
                "history_days": len(daily),
            }
        )
    except Exception as exc:
        LOG.exception("liquid_for_ticker failed for %s", symbol)
        return _err("liquid_for_ticker", exc)
