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


class HmmStateRow(BaseModel):
    """One hidden state's forward-filtered probability on the latest bar."""

    state_id: int
    label: str
    probability: float
    next_probability: float | None = None
    expected_duration_bars: float | None = None
    mean_log_return: float | None = None
    realized_vol: float | None = None
    is_stress: bool = False
    is_active: bool = False


class HmmPlaybookView(BaseModel):
    """Static playbook for the latched state. The book does not execute it."""

    label: str
    style: str
    entry: str
    exit: str
    stop: str
    take_profit: str
    max_size: str
    invalidation: str
    book_allows_entry: bool = False


class HmmSwitch(BaseModel):
    as_of: str
    from_label: str | None = None
    to_label: str
    reason: str


class HmmRegimeView(BaseModel):
    """Advisory HMM regime card. Never an order, never a replacement for regime v2."""

    status: Literal["ok", "flat", "unavailable"] = "unavailable"
    reason: str | None = None
    advisory_only: bool = True
    orders_placed: int = 0
    live_gate: str = "regime_v2"
    probability_kind: str = "forward_filtered"
    as_of: str | None = None
    current_label: str | None = None
    current_probability: float | None = None
    leading_label: str | None = None
    leading_probability: float | None = None
    states: list[HmmStateRow] = Field(default_factory=list)
    stress_next_probability: float | None = None
    uncertain: bool = False
    top_gap: float | None = None
    confirms: int = 0
    confirms_required: int = 2
    takeover_probability: float = 0.70
    min_hold_bars: int = 3
    bars_in_state: int = 0
    hold_bars_remaining: int = 0
    expected_duration_bars: float | None = None
    action: str = "flat"
    action_reason: str = ""
    model_size_multiplier: float = 0.0
    advisory_size_multiplier: float = 0.0
    active_playbook: HmmPlaybookView | None = None
    entries_frozen: bool = False
    freeze_reasons: list[str] = Field(default_factory=list)
    refit_as_of: str | None = None
    refit_reused: bool = False
    selected_states: int | None = None
    bic_by_states: dict[str, float] = Field(default_factory=dict)
    mean_train_loglik: float | None = None
    mean_live_loglik: float | None = None
    transition_labels: list[str] = Field(default_factory=list)
    transition_matrix: list[list[float]] = Field(default_factory=list)
    recent_switches: list[HmmSwitch] = Field(default_factory=list)
    thresholds: dict[str, float] = Field(default_factory=dict)
    scan_blocked_by_regime_v2: bool = False
    bars_used: int = 0
    feature_names: list[str] = Field(default_factory=list)


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
    hmm_regime: HmmRegimeView | None = None
    provenance: Provenance = Field(default_factory=Provenance)
