"""Backfill hourly funding history into daily aggregates for backtests."""

from __future__ import annotations

import logging
from typing import Any

from core.liquid_positioning.features import aggregate_funding_daily
from core.liquid_positioning.provider import HyperliquidXyzProvider
from core.liquid_positioning.store import store_funding_daily

LOG = logging.getLogger(__name__)


def backfill_funding_for_tickers(
    provider: HyperliquidXyzProvider, tickers: list[str]
) -> dict[str, Any]:
    """Fetch + aggregate + store funding history per ticker. Never raises."""
    diag: dict[str, Any] = {"tickers": 0, "stored_days": 0, "errors": [], "per_ticker": {}}
    for raw in tickers:
        ticker = str(raw or "").upper().strip()
        if not ticker:
            continue
        diag["tickers"] += 1
        try:
            rows = provider.get_funding_history(ticker)
            daily = aggregate_funding_daily(rows)
            written = store_funding_daily(ticker, daily)
            diag["stored_days"] += written
            diag["per_ticker"][ticker] = {
                "hourly_rows": len(rows),
                "days": len(daily),
                "first": daily[0]["date"] if daily else None,
                "last": daily[-1]["date"] if daily else None,
            }
        except Exception as exc:
            diag["errors"].append(f"{ticker}:{type(exc).__name__}")
            LOG.warning("Liquid funding backfill failed for %s: %s", ticker, exc)
    return diag
