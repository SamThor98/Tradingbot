"""Normalized internal shapes for the event-probability layer.

Provider payloads are converted to :class:`RawMarket` first, then to
:class:`NormalizedEvent`. Everything downstream (calibration, mapping,
valuation, dashboard) only ever sees the normalized shapes, so adding a second
prediction-market source never touches the impact or valuation code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.event_probability._util import clamp01

RESOLUTION_OPEN = "open"
RESOLUTION_RESOLVED_YES = "resolved_yes"
RESOLUTION_RESOLVED_NO = "resolved_no"
RESOLUTION_CANCELLED = "cancelled"
RESOLUTION_UNKNOWN = "unknown"

RESOLUTION_STATUSES: frozenset[str] = frozenset(
    {
        RESOLUTION_OPEN,
        RESOLUTION_RESOLVED_YES,
        RESOLUTION_RESOLVED_NO,
        RESOLUTION_CANCELLED,
        RESOLUTION_UNKNOWN,
    }
)


class EventProbabilityError(Exception):
    """Base class for layer-specific failures."""


class MalformedEventError(EventProbabilityError):
    """Provider payload could not be normalized (missing id/name/probability)."""


class ProviderError(EventProbabilityError):
    """Provider call failed. ``code`` is a stable string for counters/logging."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


class ValuationUnavailableError(EventProbabilityError):
    """Valuation inputs are missing or unusable for this ticker."""


@dataclass(slots=True)
class RawMarket:
    """Provider-native market after minimal shape validation."""

    source: str
    source_market_id: str
    question: str
    raw_probability: float
    description: str = ""
    category_hint: str | None = None
    volume: float | None = None
    liquidity: float | None = None
    spread: float | None = None
    resolution_date: datetime | None = None
    updated_ts: datetime | None = None
    resolution_status: str = RESOLUTION_OPEN


@dataclass(slots=True)
class EventObservation:
    """One append-only point-in-time observation of an event."""

    event_id: str
    timestamp: datetime
    raw_probability: float
    adjusted_probability: float
    volume: float | None = None
    liquidity: float | None = None
    spread: float | None = None


@dataclass(slots=True)
class NormalizedEvent:
    """Current normalized state of an event."""

    event_id: str
    source: str
    source_market_id: str
    event_name: str
    event_category: str
    description: str
    resolution_date: datetime | None
    current_probability: float
    adjusted_probability: float
    previous_probability: float | None
    probability_change_1d: float | None
    probability_change_7d: float | None
    probability_change_30d: float | None
    volume: float | None
    liquidity: float | None
    spread: float | None
    confidence_score: float
    last_updated: datetime
    resolution_status: str = RESOLUTION_OPEN

    def __post_init__(self) -> None:
        self.current_probability = clamp01(self.current_probability)
        self.adjusted_probability = clamp01(self.adjusted_probability)
        if self.previous_probability is not None:
            self.previous_probability = clamp01(self.previous_probability)
        self.confidence_score = max(0.0, min(1.0, float(self.confidence_score)))

    def is_open(self) -> bool:
        return self.resolution_status == RESOLUTION_OPEN

    def probability_change(self, lookback_days: int) -> float | None:
        return {
            1: self.probability_change_1d,
            7: self.probability_change_7d,
            30: self.probability_change_30d,
        }.get(int(lookback_days))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "source": self.source,
            "source_market_id": self.source_market_id,
            "event_name": self.event_name,
            "event_category": self.event_category,
            "description": self.description,
            "resolution_date": self.resolution_date.isoformat() if self.resolution_date else None,
            "current_probability": round(self.current_probability, 6),
            "adjusted_probability": round(self.adjusted_probability, 6),
            "previous_probability": (
                round(self.previous_probability, 6) if self.previous_probability is not None else None
            ),
            "probability_change_1d": _round_opt(self.probability_change_1d),
            "probability_change_7d": _round_opt(self.probability_change_7d),
            "probability_change_30d": _round_opt(self.probability_change_30d),
            "volume": self.volume,
            "liquidity": self.liquidity,
            "spread": self.spread,
            "confidence_score": round(self.confidence_score, 6),
            "last_updated": self.last_updated.isoformat(),
            "resolution_status": self.resolution_status,
        }


@dataclass(slots=True)
class ExposureMapping:
    """One versioned event -> security exposure row.

    ``impact_if_yes`` / ``impact_if_no`` are deltas on ``fundamental_variable``
    (see ``valuation.FUNDAMENTAL_VARIABLES``). Discrete and continuous effects
    use the same numeric fields; a discrete effect simply has ``impact_if_no``
    of ``0.0``.
    """

    event_id: str
    ticker: str
    fundamental_variable: str
    impact_if_yes: float
    impact_if_no: float = 0.0
    industry: str | None = None
    exposure_type: str = "fundamental"
    direction: str = "unknown"
    materiality: float = 1.0
    confidence: float = 0.5
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    notes: str = ""

    def is_effective(self, as_of: datetime) -> bool:
        if self.effective_from is not None and as_of < self.effective_from:
            return False
        if self.effective_to is not None and as_of >= self.effective_to:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ticker": self.ticker,
            "industry": self.industry,
            "exposure_type": self.exposure_type,
            "direction": self.direction,
            "materiality": self.materiality,
            "confidence": self.confidence,
            "fundamental_variable": self.fundamental_variable,
            "impact_if_yes": self.impact_if_yes,
            "impact_if_no": self.impact_if_no,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "notes": self.notes,
        }


@dataclass(slots=True)
class EventProbabilityConfig:
    """Resolved ``EVENT_PROB_*`` settings."""

    enabled: bool = False
    mode: str = "off"
    provider: str = "stub"
    timeout_ms: int = 1500
    cache_ttl_sec: int = 300
    max_event_age_hours: float = 72.0
    min_liquidity: float = 1000.0
    max_spread: float = 0.08
    max_market_weight: float = 0.85
    lookback_days: tuple[int, ...] = (1, 7, 30)
    ems_lookback_days: int = 7
    historical_snapshot_path: str = ""

    def is_active(self) -> bool:
        return bool(self.enabled) and self.mode != "off"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "provider": self.provider,
            "timeout_ms": self.timeout_ms,
            "cache_ttl_sec": self.cache_ttl_sec,
            "max_event_age_hours": self.max_event_age_hours,
            "min_liquidity": self.min_liquidity,
            "max_spread": self.max_spread,
            "max_market_weight": self.max_market_weight,
            "lookback_days": list(self.lookback_days),
            "ems_lookback_days": self.ems_lookback_days,
        }


@dataclass(slots=True)
class IngestDiagnostics:
    """Counters surfaced by the ingest endpoint and logs."""

    processed: int = 0
    stored: int = 0
    duplicates: int = 0
    malformed: int = 0
    stale: int = 0
    invalid_probability: int = 0
    provider_errors: int = 0
    mapping_errors: int = 0
    error_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "malformed": self.malformed,
            "stale": self.stale,
            "invalid_probability": self.invalid_probability,
            "provider_errors": self.provider_errors,
            "mapping_errors": self.mapping_errors,
            "error_codes": list(self.error_codes),
        }


def _round_opt(value: float | None) -> float | None:
    return round(float(value), 6) if value is not None else None
