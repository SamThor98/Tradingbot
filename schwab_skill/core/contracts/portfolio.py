"""PortfolioRiskState contract — feeds the "Portfolio Risk + Exposure" lane."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.contracts.provenance import Provenance


class Position(BaseModel):
    ticker: str
    qty: float = 0.0
    avg_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    sector_etf: str | None = None
    weight_pct: float | None = None  # market_value / equity


class ExposureBreakdown(BaseModel):
    by_sector: dict[str, float] = Field(default_factory=dict)  # etf -> pct of equity
    gross_pct: float | None = None  # sum(|position|) / equity
    net_pct: float | None = None
    largest_position_pct: float | None = None


class ConcentrationStats(BaseModel):
    top1_pct: float | None = None
    top5_pct: float | None = None
    herfindahl: float | None = None  # sum(weight^2), 0..1


class PortfolioRiskState(BaseModel):
    equity: float | None = None
    cash: float | None = None
    buying_power: float | None = None
    positions: list[Position] = Field(default_factory=list)
    exposure: ExposureBreakdown = Field(default_factory=ExposureBreakdown)
    concentration: ConcentrationStats = Field(default_factory=ConcentrationStats)
    # Phase 3 deepens these (correlation proxy, stop integrity, drift).
    risk_flags: list[str] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
