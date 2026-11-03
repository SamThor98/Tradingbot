"""Persistence for events and their observation history.

Two guarantees this module exists to enforce:

1. **Append-only history.** ``append_observation`` never updates an existing
   row. A repeated ingest at the same timestamp is reported as a duplicate.
2. **Point-in-time reads.** :meth:`EventRepository.event_as_of` rebuilds an
   event's state from history rows at or before ``as_of`` and recomputes the
   change metrics from that slice, so a backtest cannot observe a probability
   that had not been published yet.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.event_probability._util import as_utc
from core.event_probability.normalizer import compute_probability_changes
from core.event_probability.schema import EventObservation, NormalizedEvent

LOG = logging.getLogger(__name__)


class EventRepository:
    """Thin data-access layer over the event-probability tables."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------ writes

    def upsert_event(self, event: NormalizedEvent) -> None:
        """Replace the *current* state row for an event."""
        from webapp.models import EventProbabilityEvent

        row = self._db.get(EventProbabilityEvent, event.event_id)
        if row is None:
            row = EventProbabilityEvent(event_id=event.event_id)
            self._db.add(row)
        row.source = event.source
        row.source_market_id = event.source_market_id
        row.event_name = event.event_name
        row.event_category = event.event_category
        row.description = event.description
        row.resolution_date = event.resolution_date
        row.current_probability = event.current_probability
        row.adjusted_probability = event.adjusted_probability
        row.previous_probability = event.previous_probability
        row.probability_change_1d = event.probability_change_1d
        row.probability_change_7d = event.probability_change_7d
        row.probability_change_30d = event.probability_change_30d
        row.volume = event.volume
        row.liquidity = event.liquidity
        row.spread = event.spread
        row.confidence_score = event.confidence_score
        row.last_updated = event.last_updated
        row.resolution_status = event.resolution_status
        self._db.flush()

    def append_observation(self, observation: EventObservation) -> bool:
        """Append one history row. Returns ``False`` when it already exists."""
        from webapp.models import EventProbabilityHistory

        existing = self._db.execute(
            select(EventProbabilityHistory.id).where(
                EventProbabilityHistory.event_id == observation.event_id,
                EventProbabilityHistory.timestamp == observation.timestamp,
            )
        ).first()
        if existing is not None:
            return False
        self._db.add(
            EventProbabilityHistory(
                event_id=observation.event_id,
                timestamp=observation.timestamp,
                raw_probability=observation.raw_probability,
                adjusted_probability=observation.adjusted_probability,
                volume=observation.volume,
                liquidity=observation.liquidity,
                spread=observation.spread,
            )
        )
        self._db.flush()
        return True

    # ------------------------------------------------------------------- reads

    def get_event(self, event_id: str) -> NormalizedEvent | None:
        from webapp.models import EventProbabilityEvent

        row = self._db.get(EventProbabilityEvent, str(event_id))
        return None if row is None else _event_from_row(row)

    def list_events(self, *, event_ids: list[str] | None = None) -> list[NormalizedEvent]:
        from webapp.models import EventProbabilityEvent

        stmt = select(EventProbabilityEvent)
        if event_ids is not None:
            if not event_ids:
                return []
            stmt = stmt.where(EventProbabilityEvent.event_id.in_(list(event_ids)))
        rows = self._db.execute(stmt.order_by(EventProbabilityEvent.event_id)).scalars().all()
        return [_event_from_row(row) for row in rows]

    def history(
        self,
        event_id: str,
        *,
        as_of: datetime | None = None,
        since: datetime | None = None,
    ) -> list[EventObservation]:
        """Observations for an event, oldest first, never past ``as_of``."""
        from webapp.models import EventProbabilityHistory

        stmt = select(EventProbabilityHistory).where(EventProbabilityHistory.event_id == str(event_id))
        if as_of is not None:
            stmt = stmt.where(EventProbabilityHistory.timestamp <= as_of)
        if since is not None:
            stmt = stmt.where(EventProbabilityHistory.timestamp >= since)
        rows = self._db.execute(stmt.order_by(EventProbabilityHistory.timestamp)).scalars().all()
        return [_observation_from_row(row) for row in rows]

    def latest_adjusted_probability(self, event_id: str) -> float | None:
        """Newest adjusted probability, used as the calibration prior."""
        rows = self.history(event_id)
        return rows[-1].adjusted_probability if rows else None

    def event_as_of(self, event_id: str, *, as_of: datetime, lookback_days: tuple[int, ...]) -> NormalizedEvent | None:
        """Reconstruct an event's state using only data published by ``as_of``.

        Static descriptive fields (name, category, resolution date) come from
        the current row; every probability figure is recomputed from the history
        slice. Mutable descriptive fields are not versioned in Version 1 -- see
        the known limitations in the docs.
        """
        current = self.get_event(event_id)
        if current is None:
            return None
        observations = self.history(event_id, as_of=as_of)
        if not observations:
            return None
        latest = observations[-1]
        previous = observations[-2].adjusted_probability if len(observations) > 1 else None
        changes = compute_probability_changes(
            current_adjusted=latest.adjusted_probability,
            history=observations[:-1],
            as_of=as_of,
            lookback_days=lookback_days,
        )
        resolution_status = current.resolution_status
        if current.resolution_date is not None and current.resolution_date > as_of:
            # The event had not reached its resolution date yet, so a later
            # settlement must not leak backwards into the replay.
            resolution_status = "open"
        return NormalizedEvent(
            event_id=current.event_id,
            source=current.source,
            source_market_id=current.source_market_id,
            event_name=current.event_name,
            event_category=current.event_category,
            description=current.description,
            resolution_date=current.resolution_date,
            current_probability=latest.raw_probability,
            adjusted_probability=latest.adjusted_probability,
            previous_probability=previous,
            probability_change_1d=changes.get(1),
            probability_change_7d=changes.get(7),
            probability_change_30d=changes.get(30),
            volume=latest.volume,
            liquidity=latest.liquidity,
            spread=latest.spread,
            confidence_score=current.confidence_score,
            last_updated=latest.timestamp,
            resolution_status=resolution_status,
        )


def _event_from_row(row: Any) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=row.event_id,
        source=row.source,
        source_market_id=row.source_market_id,
        event_name=row.event_name,
        event_category=row.event_category,
        description=row.description,
        resolution_date=as_utc(row.resolution_date),
        current_probability=row.current_probability,
        adjusted_probability=row.adjusted_probability,
        previous_probability=row.previous_probability,
        probability_change_1d=row.probability_change_1d,
        probability_change_7d=row.probability_change_7d,
        probability_change_30d=row.probability_change_30d,
        volume=row.volume,
        liquidity=row.liquidity,
        spread=row.spread,
        confidence_score=row.confidence_score,
        last_updated=as_utc(row.last_updated) or row.last_updated,
        resolution_status=row.resolution_status,
    )


def _observation_from_row(row: Any) -> EventObservation:
    return EventObservation(
        event_id=row.event_id,
        timestamp=as_utc(row.timestamp) or row.timestamp,
        raw_probability=row.raw_probability,
        adjusted_probability=row.adjusted_probability,
        volume=row.volume,
        liquidity=row.liquidity,
        spread=row.spread,
    )
