"""Compact best/worst close ranks for the live backtest monitor.

The full trade ledger stays off the list payload. This keeps five winners
and five losers, and it drops overlap when a run has not closed enough
trades to fill both sides.
"""

from __future__ import annotations

from typing import Any

TAPE_LIMIT = 5
PROGRESS_MIN_INTERVAL_SEC = 8.0


def _net_pct(trade: dict[str, Any]) -> float | None:
    if trade.get("net_return_pct") is not None:
        try:
            pct = float(trade["net_return_pct"])
        except (TypeError, ValueError):
            pct = None
        else:
            if pct == pct:
                return pct
    raw = trade.get("net_return")
    try:
        frac = float(raw)
    except (TypeError, ValueError):
        return None
    if frac != frac:
        return None
    return frac * 100.0


def _day(value: Any) -> str:
    text = str(value or "").strip()
    return text[:10]


def compact_trade_row(trade: Any) -> dict[str, Any] | None:
    if not isinstance(trade, dict):
        return None
    pct = _net_pct(trade)
    if pct is None:
        return None
    ticker = str(trade.get("ticker") or trade.get("symbol") or "").strip()
    if not ticker:
        return None
    reason = trade.get("exit_reason")
    return {
        "ticker": ticker,
        "entry_date": _day(trade.get("entry_date") or trade.get("entry")),
        "exit_date": _day(trade.get("exit_date") or trade.get("exit")),
        "net_return_pct": round(pct, 2),
        "exit_reason": str(reason).strip() if reason else "",
    }


def rank_closed_trades(trades: Any, *, limit: int = TAPE_LIMIT) -> dict[str, Any]:
    """Rank closed trades. Worst omits any row already shown in best."""
    cap = max(1, int(limit))
    rows: list[dict[str, Any]] = []
    if isinstance(trades, list):
        for trade in trades:
            row = compact_trade_row(trade)
            if row is not None:
                rows.append(row)
    ranked = sorted(rows, key=lambda row: float(row["net_return_pct"]), reverse=True)
    best = ranked[:cap]
    shown = {id(row) for row in best}
    worst: list[dict[str, Any]] = []
    if len(ranked) > cap:
        for row in reversed(ranked[-cap:]):
            if id(row) in shown:
                continue
            worst.append(row)
            if len(worst) >= cap:
                break
    return {"closed": len(rows), "best": best, "worst": worst}
