"""Canonical internal contracts for the Trading Cockpit (Phase 0).

These are the *only* shapes that cockpit UI panels, providers, and execution
policies are allowed to consume. Raw Schwab / yfinance / Polygon JSON is
normalized into these DTOs by the provider layer (``core.providers``) so that
no panel-specific ad-hoc transform leaks across the codebase.

Every domain object embeds a shared :class:`Provenance` envelope so each panel
can render ``source`` / ``as_of`` / ``confidence`` (architecture guardrail).
"""

from __future__ import annotations

from core.contracts.decision_packet import (
    DecisionKind,
    DecisionPacket,
    OutcomeLabel,
    PacketOutcome,
)
from core.contracts.execution import (
    ExecutionFills,
    ExecutionQuality,
    ExecutionState,
    ExecutionStateName,
    OrderIntent,
)
from core.contracts.market import MarketSnapshot, RegimeState, SectorStrength, VolatilityState
from core.contracts.portfolio import (
    ClosedTradeMetrics,
    ConcentrationBlock,
    ConcentrationStats,
    CorrelationSummary,
    ExposureBreakdown,
    FxStressSummary,
    HistoricalStressRow,
    LimitBreach,
    MonteCarloSummary,
    PortfolioAnalyticsPack,
    PortfolioRiskDashboardPack,
    PortfolioRiskState,
    Position,
    RiskAdjustedMetrics,
    RiskContributionBlock,
    RiskContributionRow,
    RiskMetricsTable,
    SingleNameStressRow,
    StressBlock,
)
from core.contracts.provenance import ConfidenceLevel, DataSource, Provenance, utc_now
from core.contracts.symbol import (
    ConfidenceInfo,
    GateStatus,
    OptionsIntel,
    PreTradeChecks,
    QualityFlags,
    RankScores,
    SetupInfo,
    SymbolDecisionCard,
    TradePlan,
)

__all__ = [
    # provenance
    "Provenance",
    "DataSource",
    "ConfidenceLevel",
    "utc_now",
    # market
    "MarketSnapshot",
    "SectorStrength",
    "RegimeState",
    "VolatilityState",
    # symbol
    "SymbolDecisionCard",
    "RankScores",
    "SetupInfo",
    "TradePlan",
    "ConfidenceInfo",
    "QualityFlags",
    "GateStatus",
    "PreTradeChecks",
    "OptionsIntel",
    # execution
    "ExecutionState",
    "ExecutionStateName",
    "OrderIntent",
    "ExecutionFills",
    "ExecutionQuality",
    # decision packet
    "DecisionPacket",
    "PacketOutcome",
    "DecisionKind",
    "OutcomeLabel",
    # portfolio
    "PortfolioRiskState",
    "PortfolioAnalyticsPack",
    "PortfolioRiskDashboardPack",
    "RiskAdjustedMetrics",
    "RiskMetricsTable",
    "RiskContributionBlock",
    "RiskContributionRow",
    "CorrelationSummary",
    "ClosedTradeMetrics",
    "ConcentrationBlock",
    "LimitBreach",
    "HistoricalStressRow",
    "SingleNameStressRow",
    "FxStressSummary",
    "MonteCarloSummary",
    "StressBlock",
    "Position",
    "ExposureBreakdown",
    "ConcentrationStats",
]

CONTRACTS_VERSION = "cockpit.v0"
