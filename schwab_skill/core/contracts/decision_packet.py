"""DecisionPacket contract — the unit of post-trade evaluation (Phase 4).

Every trade decision is snapshotted into a packet so the learning loop can later
attribute outcomes by regime, setup type, and market condition. The ``outcome``
block is backfilled once the trade resolves (via self-study / weekly digest).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.contracts.provenance import Provenance, utc_now

DecisionKind = Literal["approved", "rejected", "staged", "filtered"]
OutcomeLabel = Literal["win", "loss", "scratch", "pending", "unknown"]


class PacketOutcome(BaseModel):
    """Backfilled when the trade resolves."""

    label: OutcomeLabel = "pending"
    realized_return_pct: float | None = None
    horizon_days: int | None = None
    realized_slippage_bps: float | None = None
    resolved_at: datetime | None = None


class DecisionPacket(BaseModel):
    packet_id: str
    created_at: datetime = Field(default_factory=utc_now)
    ticker: str
    kind: DecisionKind = "approved"

    # Context at decision time (compact, denormalized for offline analysis).
    regime_state: str | None = None  # bullish | neutral | bearish
    regime_score: float | None = None
    volatility_state: str | None = None  # market condition bucket
    setup_type: str | None = None  # strategy_top_live
    gate_disposition: str | None = None
    policy_id: str | None = None

    # Predicted edge at decision time.
    rank_score: float | None = None
    edge_score: float | None = None
    p_up_calibrated: float | None = None
    expected_slippage_bps: float | None = None
    entry_price: float | None = None  # decision-time price, anchors realized-return backfill

    # Resolved outcome (backfilled).
    outcome: PacketOutcome = Field(default_factory=PacketOutcome)

    # Raw references for deep dives (kept small).
    refs: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
