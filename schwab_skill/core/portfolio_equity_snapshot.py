"""Persistence helpers for daily portfolio equity snapshots."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from core.contracts.portfolio import PortfolioRiskState


def _snapshot_date(value: date | None = None) -> date:
    return value or datetime.now(timezone.utc).date()


def _positions_payload(state: PortfolioRiskState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pos in state.positions:
        rows.append(
            {
                "ticker": pos.ticker,
                "qty": pos.qty,
                "market_value": pos.market_value,
                "weight_pct": pos.weight_pct,
                "sector_etf": pos.sector_etf,
            }
        )
    return rows


def record_equity_snapshot(
    db: Any,
    state: PortfolioRiskState,
    *,
    user_id: str = "local",
    source: str = "schwab",
    snapshot_date: date | None = None,
) -> bool:
    """Insert or update one portfolio equity snapshot per user/day."""
    if db is None or state.equity is None:
        return False
    try:
        from webapp.models import PortfolioEquitySnapshot

        day = _snapshot_date(snapshot_date)
        row = (
            db.query(PortfolioEquitySnapshot)
            .filter(
                PortfolioEquitySnapshot.user_id == user_id,
                PortfolioEquitySnapshot.snapshot_date == day,
            )
            .one_or_none()
        )
        positions_json = json.dumps(_positions_payload(state), sort_keys=True)
        if row is None:
            row = PortfolioEquitySnapshot(
                user_id=user_id,
                snapshot_date=day,
                equity=float(state.equity),
                cash=state.cash,
                positions_json=positions_json,
                source=source,
            )
            db.add(row)
        else:
            row.equity = float(state.equity)
            row.cash = state.cash
            row.positions_json = positions_json
            row.source = source
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False


def load_equity_curve(db: Any, *, user_id: str = "local", limit: int = 252) -> list[dict[str, Any]]:
    """Return stored snapshots as an equity curve sorted oldest to newest."""
    if db is None:
        return []
    try:
        from webapp.models import PortfolioEquitySnapshot

        rows = (
            db.query(PortfolioEquitySnapshot)
            .filter(PortfolioEquitySnapshot.user_id == user_id)
            .order_by(PortfolioEquitySnapshot.snapshot_date.desc())
            .limit(max(1, int(limit)))
            .all()
        )
    except Exception:
        return []
    out = []
    for row in reversed(rows):
        out.append(
            {
                "date": row.snapshot_date.isoformat(),
                "equity": float(row.equity),
                "cash": row.cash,
                "source": row.source,
            }
        )
    return out
