"""Probability quality adjustment.

Raw prediction-market prices are not assumed calibrated. Version 1 is a
transparent blend toward a prior:

    adjusted = w * raw + (1 - w) * prior

where ``w`` (the trust placed in the market) falls with thin liquidity, wide
spreads, and stale quotes. ``w`` is additionally capped by
``max_market_weight`` so a single market never fully drives a valuation.

Both raw and adjusted probabilities are always persisted. Everything here is a
pure function of its inputs so a historical replay reproduces the same number,
and so a real calibration model can replace :func:`calibrate` later without
touching the schema.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.event_probability._util import clamp01, safe_float, scale_down, scale_up
from core.event_probability.schema import EventProbabilityConfig

LOG = logging.getLogger(__name__)

DEFAULT_PRIOR = 0.5

# Relative weights of the three quality dimensions in Version 1. Deliberately
# simple and additive so the dashboard can explain any adjustment.
LIQUIDITY_WEIGHT = 0.5
SPREAD_WEIGHT = 0.3
FRESHNESS_WEIGHT = 0.2


@dataclass(slots=True)
class QualityInputs:
    raw_probability: Any
    liquidity: float | None = None
    spread: float | None = None
    volume: float | None = None
    age_hours: float | None = None
    prior_probability: float | None = None
    source_reliability: float = 1.0


@dataclass(slots=True)
class CalibrationResult:
    raw_probability: float
    adjusted_probability: float
    prior_probability: float
    market_weight: float
    confidence_score: float
    components: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_probability": round(self.raw_probability, 6),
            "adjusted_probability": round(self.adjusted_probability, 6),
            "prior_probability": round(self.prior_probability, 6),
            "market_weight": round(self.market_weight, 6),
            "confidence_score": round(self.confidence_score, 6),
            "components": {k: round(v, 6) for k, v in self.components.items()},
            "warnings": list(self.warnings),
        }


def calibrate(inputs: QualityInputs, *, config: EventProbabilityConfig) -> CalibrationResult:
    """Blend a raw market probability toward a prior based on market quality."""
    warnings: list[str] = []

    raw = safe_float(inputs.raw_probability)
    if raw is None:
        warnings.append("invalid_probability")
        raw = DEFAULT_PRIOR
    elif raw < 0.0 or raw > 1.0:
        warnings.append("probability_out_of_range")
        raw = clamp01(raw)

    prior = inputs.prior_probability
    prior = DEFAULT_PRIOR if prior is None else clamp01(prior)

    liquidity_score = _liquidity_score(inputs.liquidity, min_liquidity=config.min_liquidity, warnings=warnings)
    spread_score = _spread_score(inputs.spread, max_spread=config.max_spread, warnings=warnings)
    freshness_score = _freshness_score(inputs.age_hours, max_age_hours=config.max_event_age_hours, warnings=warnings)

    quality = (
        (liquidity_score * LIQUIDITY_WEIGHT) + (spread_score * SPREAD_WEIGHT) + (freshness_score * FRESHNESS_WEIGHT)
    )
    reliability = max(0.0, min(1.0, float(inputs.source_reliability)))
    quality = max(0.0, min(1.0, quality * reliability))

    # A market with no depth carries no information: fall all the way back to
    # the prior rather than half-trusting a price nobody traded against.
    market_weight = 0.0 if liquidity_score <= 0.0 else min(config.max_market_weight, quality)

    adjusted = clamp01((market_weight * raw) + ((1.0 - market_weight) * prior))

    return CalibrationResult(
        raw_probability=clamp01(raw),
        adjusted_probability=adjusted,
        prior_probability=prior,
        market_weight=market_weight,
        confidence_score=quality,
        components={
            "liquidity_score": liquidity_score,
            "spread_score": spread_score,
            "freshness_score": freshness_score,
            "source_reliability": reliability,
        },
        warnings=warnings,
    )


def _liquidity_score(value: float | None, *, min_liquidity: float, warnings: list[str]) -> float:
    liquidity = safe_float(value)
    if liquidity is None:
        warnings.append("liquidity_missing")
        return 0.0
    if liquidity <= 0.0:
        warnings.append("zero_liquidity")
        return 0.0
    if liquidity < min_liquidity:
        warnings.append("thin_liquidity")
    return scale_up(liquidity, lo=0.0, hi=max(min_liquidity * 5.0, 1.0))


def _spread_score(value: float | None, *, max_spread: float, warnings: list[str]) -> float:
    spread = safe_float(value)
    if spread is None:
        warnings.append("spread_missing")
        return 0.5
    if spread > max_spread:
        warnings.append("wide_spread")
    return scale_down(spread, lo=0.0, hi=max(max_spread, 1e-4))


def _freshness_score(value: float | None, *, max_age_hours: float, warnings: list[str]) -> float:
    age = safe_float(value)
    if age is None:
        warnings.append("age_unknown")
        return 0.5
    if age > max_age_hours:
        warnings.append("stale_price")
        return 0.0
    return scale_down(max(0.0, age), lo=0.0, hi=max(max_age_hours, 1e-4))
