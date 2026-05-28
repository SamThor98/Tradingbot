"""ExecutionState contract — feeds the cockpit "Execution Blotter".

Unifies the three lifecycle vocabularies in the codebase today
(``PendingTrade`` app status, SaaS ``Order`` status, and broker terminal
states from ``order_monitor``) into one normalized state machine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.contracts.provenance import Provenance

ExecutionStateName = Literal[
    "staged",  # built as order intent, not yet approved
    "pending_approval",  # PendingTrade.status == "pending"
    "queued",  # SaaS Order.status == "queued" / submitted
    "working",  # accepted by broker, not yet (fully) filled
    "partial",  # partially filled
    "filled",  # terminal: fully filled
    "cancelled",  # terminal
    "rejected",  # terminal
    "expired",  # terminal
    "failed",  # terminal: app/broker error
    "unknown",
]

TERMINAL_STATES: frozenset[str] = frozenset({"filled", "cancelled", "rejected", "expired", "failed"})


class OrderIntent(BaseModel):
    side: str = "BUY"
    order_type: str = "MARKET"  # MARKET | LIMIT
    limit_price: float | None = None
    policy_id: str | None = None  # set by Phase 3 execution policies


class ExecutionFills(BaseModel):
    filled_qty: float = 0.0
    avg_fill_price: float | None = None


class ExecutionQuality(BaseModel):
    """Slippage / spread / latency attribution.

    Realized slippage is already computed inside ``execution`` today
    (``result["_execution_quality"]``); this lifts it into the DTO.
    """

    expected_price: float | None = None
    realized_slippage_bps: float | None = None
    spread_bps_at_submit: float | None = None
    reprice_count: int | None = None
    latency_ms: float | None = None


class ExecutionState(BaseModel):
    order_ref: str | None = None  # broker order id or local pending-trade id
    ticker: str
    side: str = "BUY"
    qty: float | None = None
    state: ExecutionStateName = "unknown"
    intent: OrderIntent = Field(default_factory=OrderIntent)
    fills: ExecutionFills = Field(default_factory=ExecutionFills)
    quality: ExecutionQuality = Field(default_factory=ExecutionQuality)
    reason: str | None = None  # rejection / block / failure reason
    shadow: bool = False  # produced under EXECUTION_SHADOW_MODE / preview
    provenance: Provenance = Field(default_factory=Provenance)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES
