"""SymbolDecisionCard contract — feeds "Ranked Opportunities" + drilldown.

This is a typed superset of today's scanner signal dict plus the
``GET /api/decision-card/{ticker}`` synthesis, so adopting it is a refactor
rather than a rewrite.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.contracts.provenance import Provenance

GateDisposition = Literal[
    "kept",
    "filtered_self_study",
    "filtered_quality_gates",
    "filtered_event_risk",
    "filtered_ensemble",
    "filtered_meta_policy",
    "trimmed_top_n",
    "unknown",
]

# Stable set mirroring ``GateDisposition`` for runtime validation by providers.
GATE_DISPOSITIONS: frozenset[str] = frozenset(
    {
        "kept",
        "filtered_self_study",
        "filtered_quality_gates",
        "filtered_event_risk",
        "filtered_ensemble",
        "filtered_meta_policy",
        "trimmed_top_n",
        "unknown",
    }
)


class RankScores(BaseModel):
    rank_score: float | None = None
    composite_score: float | None = None
    signal_score: float | None = None
    edge_score: float | None = None
    reliability_score: float | None = None
    execution_score: float | None = None
    p_up_calibrated: float | None = None
    ev_10d: float | None = None
    rank_basis: str | None = None


class SetupInfo(BaseModel):
    stage2: bool | None = None
    vcp: bool | None = None
    breakout_confirmed: bool = False
    sector_etf: str | None = None
    strategy_top_live: str | None = None
    sma_50: float | None = None
    sma_200: float | None = None


class TradePlan(BaseModel):
    """Sizing + invalidation, sourced from the decision-card builder."""

    entry_zone: list[float] | None = None
    stop_invalidation: float | None = None
    size_qty: int | None = None
    size_usd: float | None = None


class ConfidenceInfo(BaseModel):
    bucket: str | None = None  # high | medium | low
    mirofish_conviction: float | None = None
    expected_move_10d: float | None = None


class QualityFlags(BaseModel):
    sec_risk_tag: str | None = None
    sec_risk_reasons: list[str] = Field(default_factory=list)
    forensic_flags: list[str] = Field(default_factory=list)
    pead_beat: bool | None = None
    pead_surprise_pct: float | None = None
    guidance_signal: str | None = None


class PreTradeChecks(BaseModel):
    """Phase 1 pre-trade quality gates. Shipped shadow first (annotate-only)."""

    spread_bps: float | None = None
    quote_fresh: bool | None = None
    quote_age_sec: float | None = None
    liquidity_ok: bool | None = None
    event_risk: str | None = None  # none | earnings | macro_blackout
    tradeable: bool | None = None  # overall pre-trade verdict
    blockers: list[str] = Field(default_factory=list)


class OptionsIntel(BaseModel):
    """Phase 2 options-chain intelligence (from Schwab /chains)."""

    iv_rank: float | None = None  # requires IV history; None until P2.5
    atm_iv: float | None = None  # at-the-money implied vol (nearest expiry)
    put_call_skew: float | None = None  # ATM put IV − ATM call IV
    expected_move_pct: float | None = None  # ATM straddle / underlying
    nearest_expiry: str | None = None


class GateStatus(BaseModel):
    disposition: GateDisposition = "unknown"
    reasons: list[str] = Field(default_factory=list)


class SymbolDecisionCard(BaseModel):
    """Trader-facing decision object for a single symbol."""

    ticker: str
    price: float | None = None
    rank: RankScores = Field(default_factory=RankScores)
    setup: SetupInfo = Field(default_factory=SetupInfo)
    trade_plan: TradePlan = Field(default_factory=TradePlan)
    confidence: ConfidenceInfo = Field(default_factory=ConfidenceInfo)
    quality_flags: QualityFlags = Field(default_factory=QualityFlags)
    gate_status: GateStatus = Field(default_factory=GateStatus)
    pre_trade: PreTradeChecks = Field(default_factory=PreTradeChecks)
    options_intel: OptionsIntel | None = None
    key_reasons: list[str] = Field(default_factory=list)
    block_reason: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)
