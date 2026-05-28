"""ExecutionProvider — unify lifecycle vocabularies into ExecutionState.

Normalizes the three status vocabularies in the codebase today into one state
machine:
- ``PendingTrade.status``  (app):   pending | executed | failed | rejected
- SaaS ``Order.status``:            queued | executed | failed
- broker terminal states:           FILLED | REJECTED | CANCELED | EXPIRED | WORKING | ...
"""

from __future__ import annotations

from typing import Any

from core.contracts.execution import (
    ExecutionFills,
    ExecutionQuality,
    ExecutionState,
    ExecutionStateName,
    OrderIntent,
)
from core.contracts.provenance import Provenance, utc_now

# Map raw broker/app statuses -> canonical ExecutionStateName.
_STATE_MAP: dict[str, ExecutionStateName] = {
    # app / saas
    "pending": "pending_approval",
    "staged": "staged",
    "queued": "queued",
    "submitted": "queued",
    "executed": "filled",
    "failed": "failed",
    "rejected": "rejected",
    # broker
    "working": "working",
    "accepted": "working",
    "pending_activation": "working",
    "queued_broker": "queued",
    "awaiting_parent_order": "working",
    "partial": "partial",
    "partially_filled": "partial",
    "filled": "filled",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "expired": "expired",
    "replaced": "cancelled",
}


def _f(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_state(raw_status: str | None) -> ExecutionStateName:
    if not raw_status:
        return "unknown"
    return _STATE_MAP.get(str(raw_status).strip().lower(), "unknown")


class ExecutionProvider:
    domain = "execution"

    @staticmethod
    def from_pending_trade(row: dict[str, Any]) -> ExecutionState:
        """Normalize a local/SaaS pending-trade or order row."""
        r = row or {}
        return ExecutionState(
            order_ref=str(r.get("order_id") or r.get("id") or "") or None,
            ticker=str(r.get("ticker") or "").upper(),
            side=str(r.get("side") or "BUY").upper(),
            qty=_f(r.get("qty")),
            state=normalize_state(r.get("status")),
            intent=OrderIntent(
                side=str(r.get("side") or "BUY").upper(),
                order_type=str(r.get("order_type") or "MARKET").upper(),
                limit_price=_f(r.get("price")),
            ),
            reason=r.get("error") or r.get("reject_reason") or r.get("note"),
            provenance=Provenance(source="schwab", as_of=utc_now(), confidence="high"),
        )

    @staticmethod
    def from_order_result(result: dict[str, Any]) -> ExecutionState:
        """Normalize a ``place_order`` result dict (incl. ``_execution_quality``)."""
        res = result or {}
        eq = res.get("_execution_quality") or {}
        policy = res.get("_execution_policy") or eq.get("policy") or {}
        fill_price = _f(res.get("fill_price") or res.get("avg_fill_price"))
        state = normalize_state(res.get("status"))
        if state == "unknown" and res.get("filled"):
            state = "filled"

        # reprice count: explicit count, attempt count, or reprice-history length.
        reprice_count = None
        rc_raw = eq.get("reprice_count") or eq.get("reprice_attempts")
        if str(rc_raw or "").lstrip("-").isdigit():
            reprice_count = int(rc_raw)
        elif isinstance(res.get("_execution_quality_reprice"), list):
            reprice_count = len(res["_execution_quality_reprice"])

        quality = ExecutionQuality(
            expected_price=_f(eq.get("expected_price") or res.get("expected_price")),
            realized_slippage_bps=_f(
                eq.get("realized_slippage_bps") or eq.get("slippage_bps") or eq.get("expected_slippage_bps")
            ),
            spread_bps_at_submit=_f(eq.get("spread_bps") or eq.get("spread_bps_at_submit")),
            reprice_count=reprice_count,
            latency_ms=_f(eq.get("latency_ms")),
        )

        meta = {
            "provider": "schwab",
            "data_quality": res.get("_data_quality"),
        }
        return ExecutionState(
            order_ref=str(res.get("order_id") or "") or None,
            ticker=str(res.get("ticker") or "").upper(),
            side=str(res.get("side") or "BUY").upper(),
            qty=_f(res.get("qty")),
            state=state,
            intent=OrderIntent(
                side=str(res.get("side") or "BUY").upper(),
                order_type=str(res.get("order_type") or "MARKET").upper(),
                limit_price=_f(res.get("limit_price") or res.get("price")),
                policy_id=res.get("policy_id") or policy.get("policy_id"),
            ),
            fills=ExecutionFills(
                filled_qty=_f(res.get("filled_qty")) or (_f(res.get("qty")) or 0.0 if state == "filled" else 0.0),
                avg_fill_price=fill_price,
            ),
            quality=quality,
            reason=res.get("reason") or res.get("error"),
            shadow=bool(res.get("shadow") or res.get("_shadow")),
            provenance=Provenance.from_lineage(meta),
        )
