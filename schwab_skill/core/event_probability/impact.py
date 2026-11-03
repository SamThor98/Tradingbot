"""Event-weighted company value.

For a single binary event:

    EV = P(event) * Value(if event) + (1 - P(event)) * Value(if no event)

For several events, Version 1 enumerates the full scenario tree and weights it
by the product of the individual probabilities. That product is only valid under
an **explicit independence assumption** (:data:`ASSUME_INDEPENDENT_EVENTS`),
which is wrong for correlated macro events -- two "recession" markets are not
independent coin flips. The scenario enumeration is deliberately factored so a
later version can replace :func:`scenario_probability` with conditional
probabilities, a correlation matrix, or a shared macro regime without touching
the valuation calls.

``materiality`` is applied exactly once, when converting a mapping's declared
impact into the shock actually fed to the DCF. Sensitivities and the mispricing
score therefore already embed it and must not scale by it again.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import Any

from core.event_probability.schema import ExposureMapping, NormalizedEvent
from core.event_probability.valuation import ValuationInputs, value_equity, value_with_shocks

LOG = logging.getLogger(__name__)

#: Version 1 treats mapped events as statistically independent. Documented in
#: ``docs/EVENT_PROBABILITY_LAYER.md`` under known limitations.
ASSUME_INDEPENDENT_EVENTS = True

#: 2**6 = 64 priced scenarios. Beyond this, the lowest-impact events are folded
#: into a deterministic expected-value factor instead of being enumerated.
MAX_ENUMERATED_EVENTS = 6


@dataclass(slots=True)
class EventExposure:
    """One event paired with the mapping that ties it to a security."""

    event: NormalizedEvent
    mapping: ExposureMapping

    @property
    def probability(self) -> float:
        return self.event.adjusted_probability

    def shocks(self, *, occurred: bool) -> list[tuple[str, float]]:
        impact = self.mapping.impact_if_yes if occurred else self.mapping.impact_if_no
        return [(self.mapping.fundamental_variable, impact * self.mapping.materiality)]


@dataclass(slots=True)
class ScenarioOutcome:
    label: str
    probability: float
    value_per_share: float
    active_event_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "probability": round(self.probability, 6),
            "value_per_share": round(self.value_per_share, 6),
            "active_event_ids": list(self.active_event_ids),
        }


@dataclass(slots=True)
class DistributionMetrics:
    expected_value: float
    median_value: float
    p10: float
    p25: float
    p75: float
    p90: float
    expected_return_pct: float | None
    prob_positive_return: float
    prob_return_over_25: float
    prob_return_over_50: float
    prob_loss: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_value": round(self.expected_value, 6),
            "median_value": round(self.median_value, 6),
            "p10": round(self.p10, 6),
            "p25": round(self.p25, 6),
            "p75": round(self.p75, 6),
            "p90": round(self.p90, 6),
            "expected_return_pct": (
                round(self.expected_return_pct, 6) if self.expected_return_pct is not None else None
            ),
            "prob_positive_return": round(self.prob_positive_return, 6),
            "prob_return_over_25": round(self.prob_return_over_25, 6),
            "prob_return_over_50": round(self.prob_return_over_50, 6),
            "prob_loss": round(self.prob_loss, 6),
        }


@dataclass(slots=True)
class ImpactResult:
    ticker: str
    base_value: float
    expected_value: float
    valuation_adjustment: float
    scenarios: list[ScenarioOutcome]
    distribution: DistributionMetrics
    residual_factor: float = 1.0
    truncated_event_ids: list[str] = field(default_factory=list)
    excluded: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "base_value": round(self.base_value, 6),
            "expected_value": round(self.expected_value, 6),
            "valuation_adjustment": round(self.valuation_adjustment, 6),
            "scenario_count": len(self.scenarios),
            "scenarios": [row.to_dict() for row in self.scenarios],
            "distribution": self.distribution.to_dict(),
            "residual_factor": round(self.residual_factor, 6),
            "truncated_event_ids": list(self.truncated_event_ids),
            "excluded": list(self.excluded),
            "assumptions": {
                "independent_events": ASSUME_INDEPENDENT_EVENTS,
                "max_enumerated_events": MAX_ENUMERATED_EVENTS,
                "materiality_applied_once": True,
                "valuation_model": "report_dcf_recompute",
            },
        }


def eligible_exposures(exposures: list[EventExposure]) -> tuple[list[EventExposure], list[dict[str, str]]]:
    """Split exposures into priceable ones and excluded ones with reasons."""
    keep: list[EventExposure] = []
    excluded: list[dict[str, str]] = []
    for exposure in exposures:
        event = exposure.event
        if not event.is_open():
            excluded.append({"event_id": event.event_id, "reason": f"resolution_status:{event.resolution_status}"})
            continue
        keep.append(exposure)
    return keep, excluded


def scenario_probability(exposures: list[EventExposure], states: tuple[bool, ...]) -> float:
    """Joint probability of a scenario.

    Independence-only in Version 1. This is the single seam a correlated model
    would replace.
    """
    probability = 1.0
    for exposure, occurred in zip(exposures, states):
        p = exposure.probability
        probability *= p if occurred else (1.0 - p)
    return probability


def compute_impact(inputs: ValuationInputs, exposures: list[EventExposure]) -> ImpactResult:
    """Price the scenario tree implied by an event set."""
    base_value = value_equity(inputs)
    priceable, excluded = eligible_exposures(exposures)

    enumerated, residual = _split_by_impact(inputs, priceable, base_value)
    residual_factor = 1.0
    for exposure in residual:
        residual_factor *= _expected_value_factor(inputs, exposure, base_value)

    scenarios = _enumerate_scenarios(inputs, enumerated, base_value=base_value, residual_factor=residual_factor)
    expected_value = sum(row.probability * row.value_per_share for row in scenarios)
    distribution = distribution_metrics(scenarios, price=inputs.price, expected_value=expected_value)

    return ImpactResult(
        ticker=inputs.ticker,
        base_value=base_value,
        expected_value=expected_value,
        valuation_adjustment=expected_value - base_value,
        scenarios=scenarios,
        distribution=distribution,
        residual_factor=residual_factor,
        truncated_event_ids=[exposure.event.event_id for exposure in residual],
        excluded=excluded,
    )


def distribution_metrics(
    scenarios: list[ScenarioOutcome],
    *,
    price: float,
    expected_value: float | None = None,
) -> DistributionMetrics:
    """Summary stats over the discrete, probability-weighted scenario set."""
    if not scenarios:
        zero = 0.0 if expected_value is None else expected_value
        return DistributionMetrics(
            expected_value=zero,
            median_value=zero,
            p10=zero,
            p25=zero,
            p75=zero,
            p90=zero,
            expected_return_pct=None,
            prob_positive_return=0.0,
            prob_return_over_25=0.0,
            prob_return_over_50=0.0,
            prob_loss=0.0,
        )
    ev = sum(row.probability * row.value_per_share for row in scenarios) if expected_value is None else expected_value
    ordered = sorted(((row.value_per_share, row.probability) for row in scenarios), key=lambda pair: pair[0])
    ref_price = float(price)

    def prob_where(predicate: Any) -> float:
        return sum(prob for value, prob in ordered if predicate(value))

    expected_return = ((ev / ref_price) - 1.0) * 100.0 if ref_price > 0 else None
    return DistributionMetrics(
        expected_value=ev,
        median_value=_weighted_percentile(ordered, 0.50),
        p10=_weighted_percentile(ordered, 0.10),
        p25=_weighted_percentile(ordered, 0.25),
        p75=_weighted_percentile(ordered, 0.75),
        p90=_weighted_percentile(ordered, 0.90),
        expected_return_pct=expected_return,
        prob_positive_return=prob_where(lambda v: ref_price > 0 and v > ref_price),
        prob_return_over_25=prob_where(lambda v: ref_price > 0 and v >= ref_price * 1.25),
        prob_return_over_50=prob_where(lambda v: ref_price > 0 and v >= ref_price * 1.50),
        prob_loss=prob_where(lambda v: ref_price > 0 and v < ref_price),
    )


def _enumerate_scenarios(
    inputs: ValuationInputs,
    exposures: list[EventExposure],
    *,
    base_value: float,
    residual_factor: float,
) -> list[ScenarioOutcome]:
    if not exposures:
        return [
            ScenarioOutcome(
                label="base",
                probability=1.0,
                value_per_share=base_value * residual_factor,
                active_event_ids=(),
            )
        ]
    scenarios: list[ScenarioOutcome] = []
    for states in itertools.product((False, True), repeat=len(exposures)):
        probability = scenario_probability(exposures, states)
        if probability <= 0.0:
            continue
        shocks: list[tuple[str, float]] = []
        active: list[str] = []
        for exposure, occurred in zip(exposures, states):
            shocks.extend(exposure.shocks(occurred=occurred))
            if occurred:
                active.append(exposure.event.event_id)
        value = value_with_shocks(inputs, shocks) * residual_factor
        scenarios.append(
            ScenarioOutcome(
                label="+".join(active) if active else "none",
                probability=probability,
                value_per_share=value,
                active_event_ids=tuple(active),
            )
        )
    return scenarios


def _split_by_impact(
    inputs: ValuationInputs,
    exposures: list[EventExposure],
    base_value: float,
) -> tuple[list[EventExposure], list[EventExposure]]:
    """Keep the highest-impact events for enumeration; fold the rest into EV."""
    if len(exposures) <= MAX_ENUMERATED_EVENTS:
        return list(exposures), []
    ranked = sorted(exposures, key=lambda e: abs(_swing(inputs, e, base_value)), reverse=True)
    kept = ranked[:MAX_ENUMERATED_EVENTS]
    residual = ranked[MAX_ENUMERATED_EVENTS:]
    LOG.info(
        "Event impact enumeration truncated for %s: pricing %d of %d events individually",
        inputs.ticker,
        len(kept),
        len(exposures),
    )
    return kept, residual


def _swing(inputs: ValuationInputs, exposure: EventExposure, base_value: float) -> float:
    try:
        value_yes = value_with_shocks(inputs, exposure.shocks(occurred=True))
        value_no = value_with_shocks(inputs, exposure.shocks(occurred=False))
    except Exception as exc:  # pragma: no cover - guarded by valuation tests
        LOG.warning("Impact swing failed for %s: %s", exposure.event.event_id, exc)
        return 0.0
    return (value_yes - value_no) * exposure.probability if base_value else (value_yes - value_no)


def _expected_value_factor(inputs: ValuationInputs, exposure: EventExposure, base_value: float) -> float:
    """Standalone EV of one event expressed as a multiple of the base value."""
    if base_value == 0.0:
        return 1.0
    p = exposure.probability
    value_yes = value_with_shocks(inputs, exposure.shocks(occurred=True))
    value_no = value_with_shocks(inputs, exposure.shocks(occurred=False))
    return ((p * value_yes) + ((1.0 - p) * value_no)) / base_value


def _weighted_percentile(ordered: list[tuple[float, float]], quantile: float) -> float:
    total = sum(prob for _, prob in ordered)
    if total <= 0.0:
        return ordered[0][0] if ordered else 0.0
    target = quantile * total
    cumulative = 0.0
    for value, prob in ordered:
        cumulative += prob
        if cumulative >= target:
            return value
    return ordered[-1][0]
