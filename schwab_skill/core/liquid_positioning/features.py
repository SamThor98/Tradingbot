"""Funding-history aggregation and point-in-time (as-of) backtest features.

Lookahead rule: a daily funding aggregate describes a full UTC day, so a
trade entered on date D may only use funding days strictly before D.
``funding_features_asof`` enforces ``date < as_of`` — the unit tests pin
this so a future refactor cannot silently leak same-day funding.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timezone
from typing import Any, Iterable


def aggregate_funding_daily(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hourly funding rows -> one aggregate per UTC day, date ascending."""
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            ts = datetime.fromtimestamp(int(row["time"]) / 1000, tz=timezone.utc)
            rate = float(row["fundingRate"])
        except (KeyError, TypeError, ValueError):
            continue
        by_day.setdefault(ts.date().isoformat(), []).append({"time": int(row["time"]), "rate": rate})

    daily: list[dict[str, Any]] = []
    for day in sorted(by_day):
        entries = sorted(by_day[day], key=lambda e: e["time"])
        rates = [e["rate"] for e in entries]
        daily.append(
            {
                "date": day,
                "funding_mean": statistics.fmean(rates),
                "funding_min": min(rates),
                "funding_max": max(rates),
                "funding_last": entries[-1]["rate"],
                "n_hours": len(rates),
            }
        )
    return daily


def funding_features_asof(
    daily: list[dict[str, Any]], as_of: date, window: int = 90
) -> dict[str, Any]:
    """Funding features using only days strictly before ``as_of``.

    Returns None-valued features (never zeros) when history is missing or
    too short, so uncovered tickers stay distinguishable from neutral ones.
    """
    past = [r for r in daily if r.get("date") and r["date"] < as_of.isoformat()]
    empty: dict[str, Any] = {
        "liquid_funding_mean_30d": None,
        "liquid_funding_last": None,
        "liquid_funding_z90": None,
        "liquid_funding_pct_rank": None,
        "liquid_funding_days": 0,
    }
    if not past:
        return empty
    recent = past[-30:]
    means = [r["funding_mean"] for r in past if r.get("funding_mean") is not None]
    if not means:
        return empty
    last_mean = means[-1]
    hist = means[-window:]
    out = dict(empty)
    out["liquid_funding_mean_30d"] = statistics.fmean(
        [r["funding_mean"] for r in recent if r.get("funding_mean") is not None]
    )
    out["liquid_funding_last"] = last_mean
    out["liquid_funding_days"] = len(means)
    if len(hist) >= 20:
        sd = statistics.pstdev(hist)
        out["liquid_funding_z90"] = (last_mean - statistics.fmean(hist)) / sd if sd > 0 else 0.0
        out["liquid_funding_pct_rank"] = sum(1 for v in hist if v <= last_mean) / len(hist)
    return out
