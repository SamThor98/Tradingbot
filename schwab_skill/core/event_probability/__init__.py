"""Prediction Market / Event Probability layer.

Turns external event probabilities into explainable, company-specific valuation
adjustments:

    event -> probability -> company exposure -> financial assumption -> valuation

This is a *research* layer. It does not modify scan scores, ranking, position
sizing, or advisory probabilities -- that is the separate Stage B overlay in
``prediction_market.py``. See ``docs/EVENT_PROBABILITY_LAYER.md``.
"""

from __future__ import annotations

from core.event_probability.calibrator import CalibrationResult, QualityInputs, calibrate
from core.event_probability.impact import (
    ASSUME_INDEPENDENT_EVENTS,
    EventExposure,
    ImpactResult,
    compute_impact,
)
from core.event_probability.mapper import ExposureCatalog, load_catalog
from core.event_probability.normalizer import (
    build_event_id,
    compute_probability_changes,
    normalize_market,
    observation_from_event,
)
from core.event_probability.repository import EventRepository
from core.event_probability.schema import (
    EventObservation,
    EventProbabilityConfig,
    EventProbabilityError,
    ExposureMapping,
    IngestDiagnostics,
    MalformedEventError,
    NormalizedEvent,
    ProviderError,
    RawMarket,
    ValuationUnavailableError,
)
from core.event_probability.sensitivity import EventSensitivity, compute_sensitivities, compute_sensitivity
from core.event_probability.service import EventProbabilityService
from core.event_probability.settings import build_event_probability_config
from core.event_probability.signal import compute_event_mispricing_score
from core.event_probability.valuation import ValuationInputs, value_equity, value_with_shocks

__all__ = [
    "ASSUME_INDEPENDENT_EVENTS",
    "CalibrationResult",
    "EventExposure",
    "EventObservation",
    "EventProbabilityConfig",
    "EventProbabilityError",
    "EventProbabilityService",
    "EventRepository",
    "EventSensitivity",
    "ExposureCatalog",
    "ExposureMapping",
    "ImpactResult",
    "IngestDiagnostics",
    "MalformedEventError",
    "NormalizedEvent",
    "ProviderError",
    "QualityInputs",
    "RawMarket",
    "ValuationInputs",
    "ValuationUnavailableError",
    "build_event_id",
    "build_event_probability_config",
    "calibrate",
    "compute_event_mispricing_score",
    "compute_impact",
    "compute_probability_changes",
    "compute_sensitivities",
    "compute_sensitivity",
    "load_catalog",
    "normalize_market",
    "observation_from_event",
    "value_equity",
    "value_with_shocks",
]
