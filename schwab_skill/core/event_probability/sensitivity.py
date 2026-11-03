"""Event sensitivity: d(StockValue) / d(EventProbability).

The derivative of the binary expected value with respect to the probability is
exactly ``Value_if_yes - Value_if_no``, so no numerical differencing step is
needed. It is reported per **percentage point** because that is the unit an
operator reads off a prediction market:

    -$0.18 per +1pp   /   -0.7% of price per +1pp

``materiality`` is already inside the shocks (see ``impact.EventExposure``), so
these numbers describe what the model actually does, not a pre-scaled ideal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.event_probability.impact import EventExposure
from core.event_probability.valuation import ValuationInputs, value_equity, value_with_shocks

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class EventSensitivity:
    event_id: str
    ticker: str
    fundamental_variable: str
    direction: str
    materiality: float
    confidence: float
    probability: float
    base_value: float
    value_if_yes: float
    value_if_no: float
    dollar_per_pp: float
    pct_of_price_per_pp: float | None
    modeled_valuation_impact: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ticker": self.ticker,
            "fundamental_variable": self.fundamental_variable,
            "direction": self.direction,
            "materiality": round(self.materiality, 6),
            "confidence": round(self.confidence, 6),
            "probability": round(self.probability, 6),
            "base_value": round(self.base_value, 6),
            "value_if_yes": round(self.value_if_yes, 6),
            "value_if_no": round(self.value_if_no, 6),
            "dollar_per_pp": round(self.dollar_per_pp, 6),
            "pct_of_price_per_pp": (
                round(self.pct_of_price_per_pp, 6) if self.pct_of_price_per_pp is not None else None
            ),
            "modeled_valuation_impact": round(self.modeled_valuation_impact, 6),
        }


def compute_sensitivity(
    inputs: ValuationInputs,
    exposure: EventExposure,
    *,
    base_value: float | None = None,
) -> EventSensitivity:
    """Sensitivity and standalone valuation impact for one event/company pair."""
    base = value_equity(inputs) if base_value is None else float(base_value)
    value_yes = value_with_shocks(inputs, exposure.shocks(occurred=True))
    value_no = value_with_shocks(inputs, exposure.shocks(occurred=False))

    probability = exposure.probability
    derivative = value_yes - value_no
    dollar_per_pp = derivative / 100.0
    pct_per_pp = (dollar_per_pp / inputs.price) * 100.0 if inputs.price > 0 else None
    standalone_ev = (probability * value_yes) + ((1.0 - probability) * value_no)

    return EventSensitivity(
        event_id=exposure.event.event_id,
        ticker=inputs.ticker,
        fundamental_variable=exposure.mapping.fundamental_variable,
        direction=exposure.mapping.direction,
        materiality=exposure.mapping.materiality,
        confidence=exposure.mapping.confidence,
        probability=probability,
        base_value=base,
        value_if_yes=value_yes,
        value_if_no=value_no,
        dollar_per_pp=dollar_per_pp,
        pct_of_price_per_pp=pct_per_pp,
        modeled_valuation_impact=standalone_ev - base,
    )


def compute_sensitivities(
    inputs: ValuationInputs,
    exposures: list[EventExposure],
    *,
    base_value: float | None = None,
) -> list[EventSensitivity]:
    """Sensitivities for a set of exposures, skipping any that fail to price."""
    base = value_equity(inputs) if base_value is None else float(base_value)
    out: list[EventSensitivity] = []
    for exposure in exposures:
        try:
            out.append(compute_sensitivity(inputs, exposure, base_value=base))
        except Exception as exc:
            LOG.warning(
                "Sensitivity failed for %s/%s: %s",
                inputs.ticker,
                exposure.event.event_id,
                exc,
            )
    return out


def largest_exposures(
    sensitivities: list[EventSensitivity],
) -> tuple[EventSensitivity | None, EventSensitivity | None]:
    """Largest positive and largest negative modeled valuation impacts."""
    positives = [row for row in sensitivities if row.modeled_valuation_impact > 0]
    negatives = [row for row in sensitivities if row.modeled_valuation_impact < 0]
    best = max(positives, key=lambda row: row.modeled_valuation_impact, default=None)
    worst = min(negatives, key=lambda row: row.modeled_valuation_impact, default=None)
    return best, worst
