"""Event Mispricing Score (EMS).

Asks a narrow question: over the lookback window, how much should the mapped
event-probability moves have shifted this company's value, and did the stock
move that much?

    implied_move_pct = sum over events of  (dP in pp) * (pct of price per pp)
    ems_gap_pct      = implied_move_pct - realized_return_pct

A large positive gap means event probabilities implied upside the price has not
delivered; a large negative gap is the reverse. This is a **research signal for
triage**, not a trading recommendation, and it is deliberately not wired into
``composite_score`` or rank v2.

Materiality is already embedded in each sensitivity (see ``impact``), so the
spec's separate materiality factor is not applied a second time here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.event_probability.schema import NormalizedEvent
from core.event_probability.sensitivity import EventSensitivity

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class EventMispricingScore:
    ticker: str
    lookback_days: int
    implied_move_pct: float
    realized_return_pct: float | None
    ems_gap_pct: float | None
    contributing_events: int
    missing_change_events: int
    components: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "lookback_days": self.lookback_days,
            "implied_move_pct": round(self.implied_move_pct, 6),
            "realized_return_pct": (
                round(self.realized_return_pct, 6) if self.realized_return_pct is not None else None
            ),
            "ems_gap_pct": round(self.ems_gap_pct, 6) if self.ems_gap_pct is not None else None,
            "contributing_events": self.contributing_events,
            "missing_change_events": self.missing_change_events,
            "components": list(self.components),
        }


def compute_event_mispricing_score(
    *,
    ticker: str,
    events_by_id: dict[str, NormalizedEvent],
    sensitivities: list[EventSensitivity],
    lookback_days: int,
    realized_return_pct: float | None,
) -> EventMispricingScore:
    """Combine probability drift and sensitivity into an implied-vs-realized gap."""
    implied = 0.0
    contributing = 0
    missing = 0
    components: list[dict[str, Any]] = []

    for sens in sensitivities:
        event = events_by_id.get(sens.event_id)
        if event is None:
            missing += 1
            continue
        change = event.probability_change(lookback_days)
        if change is None:
            # No baseline observation in the window. A missing change is not a
            # zero change, so the event contributes nothing and is counted.
            missing += 1
            continue
        if sens.pct_of_price_per_pp is None:
            missing += 1
            continue
        change_pp = change * 100.0
        contribution = change_pp * sens.pct_of_price_per_pp
        implied += contribution
        contributing += 1
        components.append(
            {
                "event_id": sens.event_id,
                "event_name": event.event_name,
                "probability_change_pp": round(change_pp, 6),
                "pct_of_price_per_pp": round(sens.pct_of_price_per_pp, 6),
                "implied_move_pct": round(contribution, 6),
            }
        )

    gap = None if realized_return_pct is None else implied - float(realized_return_pct)
    return EventMispricingScore(
        ticker=str(ticker).strip().upper(),
        lookback_days=int(lookback_days),
        implied_move_pct=implied,
        realized_return_pct=realized_return_pct,
        ems_gap_pct=gap,
        contributing_events=contributing,
        missing_change_events=missing,
        components=components,
    )
