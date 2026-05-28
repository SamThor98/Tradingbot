"""MarketSnapshot contract — feeds the cockpit "Market Regime" lane."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.contracts.provenance import Provenance

RegimeState = Literal["bullish", "neutral", "bearish"]
VolatilityState = Literal["low", "normal", "elevated", "extreme"]


class SectorStrength(BaseModel):
    """One row of sector breadth (mirrors the existing /api/sectors payload)."""

    etf: str
    name: str | None = None
    rel_strength_pct: float | None = None  # outperformance vs SPY over lookback
    is_winning: bool = False
    rank: int | None = None


class Movers(BaseModel):
    """Market internals / movers. Populated in Phase 2 (empty until then)."""

    gainers: list[str] = Field(default_factory=list)
    losers: list[str] = Field(default_factory=list)
    most_active: list[str] = Field(default_factory=list)


class MarketSnapshot(BaseModel):
    """Normalized top-of-book market context for the cockpit header/lane."""

    regime_state: RegimeState = "neutral"
    regime_score: float | None = None  # regime_v2 composite 0-100
    regime_bucket: str | None = None  # high | medium | low (regime_v2)
    spy_price: float | None = None
    spy_sma_200: float | None = None
    is_regime_bullish: bool = False
    scan_blocked_by_regime: bool = False
    sector_breadth: list[SectorStrength] = Field(default_factory=list)
    volatility_state: VolatilityState = "normal"
    vix_level: float | None = None
    movers: Movers = Field(default_factory=Movers)
    provenance: Provenance = Field(default_factory=Provenance)
