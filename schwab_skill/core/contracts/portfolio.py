"""PortfolioRiskState contract — feeds the "Portfolio Risk + Exposure" lane."""

from __future__ import annotations

from datetime import date
from typing import Any

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
    acquired_at: date | None = None  # manual books: ownership start for period returns


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


class RiskAdjustedMetrics(BaseModel):
    volatility_ann_pct: float | None = None
    variance_ann: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    beta_vs_benchmark: float | None = None
    benchmark: str = "SPY"
    max_drawdown_pct: float | None = None
    current_drawdown_pct: float | None = None
    total_return_pct: float | None = None
    observations: int = 0


class CorrelationSummary(BaseModel):
    matrix: dict[str, dict[str, float]] = Field(default_factory=dict)
    max_pair: tuple[str, str, float] | None = None
    avg_pair_corr: float | None = None
    threshold: float | None = None
    breaches: list[dict[str, Any]] = Field(default_factory=list)


class ClosedTradeMetrics(BaseModel):
    source: str = "decision_packets"
    trades: int = 0
    profit_factor: float | str | None = None
    win_rate: float | None = None
    expectancy_pct: float | None = None
    sharpe: float | None = None
    max_drawdown_pct: float | None = None
    total_return_pct: float | None = None
    per_era: list[dict[str, Any]] = Field(default_factory=list)


class PortfolioAnalyticsPack(BaseModel):
    live: RiskAdjustedMetrics | None = None
    correlation: CorrelationSummary | None = None
    closed_trades: ClosedTradeMetrics | None = None
    lookback_days: int = 60
    data_quality: dict[str, Any] = Field(default_factory=dict)
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)


class RiskMetricsTable(BaseModel):
    """Headline metric table for the risk dashboard (reference: Risk tab)."""

    annualized_return_pct: float | None = None
    volatility_ann_pct: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown_pct: float | None = None
    current_drawdown_pct: float | None = None
    beta_vs_benchmark: float | None = None
    benchmark: str = "SPY"
    var_95_pct: float | None = None
    daily_win_rate_pct: float | None = None
    total_return_pct: float | None = None
    observations: int = 0


class RiskContributionRow(BaseModel):
    ticker: str
    weight_pct: float | None = None
    vol_ann_pct: float | None = None
    risk_contrib_ann_pct: float | None = None
    risk_contrib_pct: float | None = None


class RiskContributionBlock(BaseModel):
    ex_ante_vol_pct: float | None = None  # model forecast: current weights x cov
    realized_vol_pct: float | None = None  # from actual portfolio return series
    rows: list[RiskContributionRow] = Field(default_factory=list)


class LimitBreach(BaseModel):
    kind: str  # single_name | sector | country
    label: str
    value_pct: float | None = None
    limit_pct: float | None = None
    message: str = ""


class ConcentrationBlock(BaseModel):
    hhi: float | None = None  # fractional 0..1
    effective_n: float | None = None
    top_position_pct: float | None = None
    top_5_pct: float | None = None
    top_10_pct: float | None = None
    sector_count: int = 0
    position_count: int = 0
    breaches: list[LimitBreach] = Field(default_factory=list)


class HistoricalStressRow(BaseModel):
    scenario: str
    scenario_type: str = "historical"  # historical | hypothetical
    method: str = "beta_scaled"  # window_replay | beta_scaled | unavailable
    market_move_pct: float | None = None
    portfolio_impact_pct: float | None = None
    stressed_nav: float | None = None
    pnl: float | None = None
    description: str = ""


class SingleNameStressRow(BaseModel):
    scenario: str
    ticker: str
    gap_pct: float | None = None
    weight_pct: float | None = None
    portfolio_impact_pct: float | None = None
    pnl: float | None = None


class FxStressSummary(BaseModel):
    non_usd_weight_pct: float | None = None
    scenario_impact_pct: float | None = None
    scenario_pnl: float | None = None
    broad_em_shock_pct: float | None = None
    broad_em_impact_pnl: float | None = None
    by_country: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_tickers: list[str] = Field(default_factory=list)


class MonteCarloSummary(BaseModel):
    simulations: int = 0
    var_95_pct: float | None = None
    var_99_pct: float | None = None
    cvar_95_pct: float | None = None
    var_95_pnl: float | None = None
    var_99_pnl: float | None = None
    mean_daily_pct: float | None = None


class StressBlock(BaseModel):
    historical: list[HistoricalStressRow] = Field(default_factory=list)
    single_name: list[SingleNameStressRow] = Field(default_factory=list)
    fx: FxStressSummary | None = None
    monte_carlo: MonteCarloSummary | None = None
    tail_risk: dict[str, Any] = Field(default_factory=dict)


class PortfolioRiskDashboardPack(BaseModel):
    """Unified payload for the dedicated portfolio Risk tab."""

    equity: float | None = None
    position_count: int = 0
    metrics: RiskMetricsTable | None = None
    correlation: CorrelationSummary | None = None
    risk_contribution: RiskContributionBlock | None = None
    concentration: ConcentrationBlock | None = None
    sector_allocation: list[dict[str, Any]] = Field(default_factory=list)
    positions_weighted: list[dict[str, Any]] = Field(default_factory=list)
    country_exposure: list[dict[str, Any]] = Field(default_factory=list)
    stress: StressBlock = Field(default_factory=StressBlock)
    risk_flags: list[str] = Field(default_factory=list)
    closed_trades: ClosedTradeMetrics | None = None
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    lookback_days: int = 252
    data_quality: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
