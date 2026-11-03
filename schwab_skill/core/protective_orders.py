"""Schwab order shapes that keep a long equity position protected.

A buy is one TRIGGER parent with a single stop child. Schwab holds that child
after the parent fills, including when this process is down. Later stop changes
go out as a replace of the working stop. A rejected replace leaves the working
stop where it is.

Field names for trailing stops follow the Trader API order schema
(``stopPriceLinkBasis``, ``stopPriceLinkType``, ``stopPriceOffset``).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

PROTECTION_FULL = "FULL"
PROTECTION_PARTIAL = "PARTIAL"
PROTECTION_NONE = "NO STOP SEEN"
PROTECTION_LOCKED = "LOCKED"
PROTECTION_NA = "NOT_APPLICABLE"

_WORST_FIRST = {
    PROTECTION_LOCKED: 0,
    PROTECTION_NONE: 1,
    PROTECTION_PARTIAL: 2,
    PROTECTION_FULL: 3,
    PROTECTION_NA: 4,
}


def round_price(price: float) -> float:
    """Schwab equity ticks: cents at or above $1, and tenths of a cent below."""
    px = float(price)
    if px >= 1:
        return round(px, 2)
    return round(px, 4)


def trailing_stop_order(
    ticker: str,
    qty: int,
    *,
    offset: float,
    link_type: str,
    basis: str = "LAST",
    duration: str = "GOOD_TILL_CANCEL",
) -> dict[str, Any]:
    """A single trailing-stop order using the Trader API link fields."""
    return {
        "orderStrategyType": "SINGLE",
        "session": "NORMAL",
        "duration": duration,
        "orderType": "TRAILING_STOP",
        "orderLegCollection": [
            {
                "instruction": "SELL",
                "quantity": int(qty),
                "instrument": {"symbol": ticker.upper(), "assetType": "EQUITY"},
            }
        ],
        "stopPriceLinkBasis": basis,
        "stopPriceLinkType": link_type,
        "stopPriceOffset": float(offset),
    }


def build_entry_bracket(entry: dict[str, Any], stop_child: dict[str, Any]) -> dict[str, Any]:
    """Return a TRIGGER order: the entry parent, then one protective stop child.

    The child is a copy. The caller's dicts are left unchanged.
    """
    if str(entry.get("orderStrategyType") or "SINGLE") != "SINGLE":
        raise ValueError("entry must be a single order")
    if str(stop_child.get("orderType") or "") not in {"STOP", "TRAILING_STOP"}:
        raise ValueError("protective child must be a STOP or TRAILING_STOP")
    entry_legs = entry.get("orderLegCollection") or []
    stop_legs = stop_child.get("orderLegCollection") or []
    if not entry_legs or not stop_legs:
        raise ValueError("entry and stop each need an order leg")
    if int(entry_legs[0].get("quantity") or 0) != int(stop_legs[0].get("quantity") or 0):
        raise ValueError("stop quantity must match the entry")
    bracket = copy.deepcopy(entry)
    bracket["orderStrategyType"] = "TRIGGER"
    bracket["childOrderStrategies"] = [copy.deepcopy(stop_child)]
    return bracket


def shift_trigger_limit(payload: dict[str, Any], new_limit: float) -> dict[str, Any] | None:
    """Move a working entry limit and keep the same stop distance.

    Returns None when the shifted hard stop would sit on the wrong side of the
    new limit. Trailing children keep their offset. A non-trigger order only
    gets the new limit price.
    """
    updated = copy.deepcopy(payload)
    new_px = round_price(float(new_limit))
    old_raw = updated.get("price")
    updated["price"] = new_px
    if str(updated.get("orderStrategyType") or "") != "TRIGGER":
        return updated
    if old_raw is None:
        return updated
    delta = new_px - round_price(float(old_raw))
    if abs(delta) < 0.01:
        return updated
    for child in updated.get("childOrderStrategies") or []:
        if not isinstance(child, dict) or str(child.get("orderType") or "") != "STOP":
            continue
        if child.get("stopPrice") is None:
            continue
        new_stop = round_price(float(child["stopPrice"]) + delta)
        leg = (child.get("orderLegCollection") or [{}])[0]
        instruction = str((leg or {}).get("instruction") or "")
        floor = 0.01 if new_px >= 1 else 0.0001
        if instruction == "SELL" and new_stop >= new_px - floor:
            return None
        if instruction == "BUY_TO_COVER" and new_stop <= new_px + floor:
            return None
        if new_stop <= 0:
            return None
        child["stopPrice"] = new_stop
    return updated


def child_stop_order_id(order: dict[str, Any]) -> str | None:
    """First STOP or TRAILING_STOP order id in a Schwab order tree."""
    for child in order.get("childOrderStrategies") or []:
        if not isinstance(child, dict):
            continue
        order_type = str(child.get("orderType") or "")
        if order_type in {"STOP", "TRAILING_STOP", "STOP_LIMIT"} and child.get("orderId"):
            return str(child["orderId"])
        found = child_stop_order_id(child)
        if found:
            return found
    return None


def _protection_rank(status: str) -> int:
    return _WORST_FIRST.get(status, 1)


def describe_protection(
    *,
    qty: float,
    asset_type: str | None,
    managed: dict[str, Any] | None,
    lock: dict[str, Any] | None,
    ambiguous_order_id: str,
) -> dict[str, Any]:
    """Local protection label for one portfolio row.

    ``FULL`` means a stop is registered here. It is not a live read of Schwab's
    working orders. ``NO STOP SEEN`` means this book has no stop id for the symbol.
    """
    if lock:
        reason = str(lock.get("reason") or "the last order did not return a clear result")
        return {
            "status": PROTECTION_LOCKED,
            "label": PROTECTION_LOCKED,
            "detail": (f"ORDER OUTCOME UNKNOWN: {reason}. Confirm the order at Schwab, then choose Landed or Absent."),
            "source": "order_ambiguity",
            "action": lock.get("action"),
        }

    asset = str(asset_type or "EQUITY").upper()
    if asset != "EQUITY" or float(qty or 0) <= 0:
        return {
            "status": PROTECTION_NA,
            "label": "—",
            "detail": "Protective stops are tracked for long equity.",
            "source": "n/a",
        }

    book = managed or {}
    stop_id = str(book.get("stop_order_id") or "")
    partial_id = str(book.get("partial_tp_order_id") or "")
    time_id = str(book.get("time_stop_order_id") or "")
    ambiguous = ambiguous_order_id

    if time_id == ambiguous:
        return {
            "status": PROTECTION_PARTIAL,
            "label": PROTECTION_PARTIAL,
            "detail": (
                "A time stop may be working at Schwab. This bot will not send another until you mark it Absent."
            ),
            "source": "exit_manager",
        }
    if partial_id == ambiguous:
        return {
            "status": PROTECTION_PARTIAL,
            "label": PROTECTION_PARTIAL,
            "detail": (
                "A partial sale may be working at Schwab. "
                "Share count updates when the fill is matched. "
                "This bot will not send another partial until you mark it Absent."
            ),
            "source": "exit_manager",
        }
    if stop_id == ambiguous and (book.get("trailing_stop_armed") or book.get("breakeven_done")):
        return {
            "status": PROTECTION_FULL,
            "label": PROTECTION_FULL,
            "detail": (
                "The stop change was confirmed at Schwab. "
                "The new order id was not captured, so this bot will not move that stop again."
            ),
            "source": "exit_manager",
        }
    if stop_id == ambiguous:
        return {
            "status": PROTECTION_LOCKED,
            "label": PROTECTION_LOCKED,
            "detail": "The protective stop id was not captured. This bot will not send a second stop.",
            "source": "exit_manager",
        }
    if book.get("pending_breakeven_move") and not book.get("breakeven_done"):
        return {
            "status": PROTECTION_PARTIAL,
            "label": PROTECTION_PARTIAL,
            "detail": "Partial taken. The registered stop still uses the prior share count until it is replaced.",
            "source": "exit_manager",
        }
    if stop_id or book.get("stop_attached"):
        detail = "A protective stop is registered for this position."
        if not stop_id and book.get("stop_attached"):
            detail = "The entry bracket was accepted. The child stop id was not in the read-back yet."
        return {
            "status": PROTECTION_FULL,
            "label": PROTECTION_FULL,
            "detail": detail,
            "source": "exit_manager",
        }
    if not book:
        return {
            "status": PROTECTION_NONE,
            "label": PROTECTION_NONE,
            "detail": "No protective stop is registered for this symbol.",
            "source": "exit_manager",
        }
    return {
        "status": PROTECTION_NONE,
        "label": PROTECTION_NONE,
        "detail": "This position is active in the exit book and has no stop id.",
        "source": "exit_manager",
    }


def annotate_portfolio_protection(summary: dict[str, Any], skill_dir: Path) -> dict[str, Any]:
    """Attach ``protection`` on each flat position row and list open ambiguity locks."""
    from core.order_ambiguity import AMBIGUOUS_ORDER_ID, AmbiguityStoreError, locks_by_symbol
    from execution_persistence import _load_exit_manager_state

    state = _load_exit_manager_state(Path(skill_dir))
    try:
        locks = locks_by_symbol(Path(skill_dir))
    except AmbiguityStoreError as exc:
        summary["order_ambiguity_error"] = str(exc)
        for row in summary.get("positions") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("asset_type") or "EQUITY").upper() != "EQUITY":
                continue
            if float(row.get("qty") or 0) <= 0:
                continue
            row["protection"] = {
                "status": PROTECTION_LOCKED,
                "label": PROTECTION_LOCKED,
                "detail": str(exc),
                "source": "order_ambiguity",
            }
        summary["protection_counts"] = {
            PROTECTION_FULL: 0,
            PROTECTION_PARTIAL: 0,
            PROTECTION_NONE: 0,
            PROTECTION_LOCKED: sum(
                1
                for row in summary.get("positions") or []
                if isinstance(row, dict) and (row.get("protection") or {}).get("status") == PROTECTION_LOCKED
            ),
        }
        summary["order_ambiguity"] = []
        return summary
    managed_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for pos in (state.get("positions") or {}).values():
        if not isinstance(pos, dict) or pos.get("status") != "active":
            continue
        symbol = str(pos.get("ticker") or "").upper()
        if symbol:
            managed_by_symbol.setdefault(symbol, []).append(pos)

    counts = {PROTECTION_FULL: 0, PROTECTION_PARTIAL: 0, PROTECTION_NONE: 0, PROTECTION_LOCKED: 0}
    for row in summary.get("positions") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        books = managed_by_symbol.get(symbol) or []
        lock = locks.get(symbol)
        if not books:
            described = describe_protection(
                qty=float(row.get("qty") or 0),
                asset_type=str(row.get("asset_type") or "EQUITY"),
                managed=None,
                lock=lock,
                ambiguous_order_id=AMBIGUOUS_ORDER_ID,
            )
        else:
            described = None
            for book in books:
                one = describe_protection(
                    qty=float(row.get("qty") or 0),
                    asset_type=str(row.get("asset_type") or "EQUITY"),
                    managed=book,
                    lock=lock,
                    ambiguous_order_id=AMBIGUOUS_ORDER_ID,
                )
                if described is None or _protection_rank(one["status"]) < _protection_rank(described["status"]):
                    described = one
        row["protection"] = described
        status = str((described or {}).get("status") or "")
        if status in counts:
            counts[status] += 1

    summary["protection_counts"] = counts
    summary["order_ambiguity"] = [
        {
            "ticker": str(item.get("ticker") or symbol),
            "action": item.get("action"),
            "reason": item.get("reason"),
            "at": item.get("at"),
        }
        for symbol, item in sorted(locks.items())
        if isinstance(item, dict)
    ]
    return summary
