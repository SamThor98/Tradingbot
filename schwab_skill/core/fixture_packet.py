"""Approve-flow fixture signature shared by the packet quarantine and the lab.

The scan-to-execute test used a fake AAPL fill at 185.50 with order id ORD123.
Those rows are not trades. Callers that score labeled cases skip them.
"""

from __future__ import annotations

from typing import Any

FIXTURE_TICKER = "AAPL"
FIXTURE_ENTRY = 185.5
FIXTURE_ORDER_REF = "ORD123"


def is_fixture_packet(packet: dict[str, Any]) -> bool:
    """True for the approve-test signature: AAPL, entry 185.5, order ORD123."""
    if str(packet.get("ticker") or "").upper() != FIXTURE_TICKER:
        return False
    refs = packet.get("refs") if isinstance(packet.get("refs"), dict) else {}
    order = refs.get("order_ref")
    if order is None:
        order = packet.get("order_ref")
    if str(order or "") != FIXTURE_ORDER_REF:
        return False
    try:
        entry = float(packet.get("entry_price"))
    except (TypeError, ValueError):
        return False
    return abs(entry - FIXTURE_ENTRY) < 1e-6
