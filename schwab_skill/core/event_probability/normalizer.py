"""Provider payload -> :class:`NormalizedEvent`.

Probability changes are computed on the *adjusted* probability, because that is
the number the valuation and mispricing layers consume. Each lookback compares
the current adjusted probability against the newest observation at or before
``as_of - lookback``, so a gap in the history never fabricates a change.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from core.event_probability._util import as_utc, clamp01
from core.event_probability.calibrator import CalibrationResult, QualityInputs, calibrate
from core.event_probability.schema import (
    EventObservation,
    EventProbabilityConfig,
    MalformedEventError,
    NormalizedEvent,
    RawMarket,
)
from core.event_probability.taxonomy import infer_category, normalize_category

LOG = logging.getLogger(__name__)


def build_event_id(source: str, source_market_id: str) -> str:
    """Stable internal id. Never derived from mutable text like the question."""
    src = str(source or "").strip().lower()
    market = str(source_market_id or "").strip()
    if not src or not market:
        raise MalformedEventError(f"cannot build event_id from source={source!r} market={source_market_id!r}")
    return f"{src}:{market}"


def normalize_market(
    market: RawMarket,
    *,
    as_of: datetime,
    history: list[EventObservation] | None = None,
    config: EventProbabilityConfig,
) -> tuple[NormalizedEvent, CalibrationResult]:
    """Validate, calibrate, and derive change metrics for one provider market.

    Raises :class:`MalformedEventError` for payloads that cannot be trusted;
    callers count those and continue rather than aborting an ingest run.
    """
    if not str(market.question or "").strip():
        raise MalformedEventError(f"event_name missing for {market.source}:{market.source_market_id}")
    event_id = build_event_id(market.source, market.source_market_id)

    observations = _sorted_history(history)
    prior = observations[-1].adjusted_probability if observations else None

    updated = as_utc(market.updated_ts) or as_of
    age_hours = max(0.0, (as_of - updated).total_seconds() / 3600.0)

    calibration = calibrate(
        QualityInputs(
            raw_probability=market.raw_probability,
            liquidity=market.liquidity,
            spread=market.spread,
            volume=market.volume,
            age_hours=age_hours,
            prior_probability=prior,
        ),
        config=config,
    )

    category = (
        normalize_category(market.category_hint)
        if market.category_hint
        else infer_category(market.question, market.description)
    )

    adjusted = calibration.adjusted_probability
    changes = compute_probability_changes(
        current_adjusted=adjusted,
        history=observations,
        as_of=as_of,
        lookback_days=config.lookback_days,
    )

    event = NormalizedEvent(
        event_id=event_id,
        source=str(market.source).strip().lower(),
        source_market_id=str(market.source_market_id).strip(),
        event_name=str(market.question).strip(),
        event_category=category,
        description=str(market.description or "").strip(),
        resolution_date=as_utc(market.resolution_date),
        current_probability=calibration.raw_probability,
        adjusted_probability=adjusted,
        previous_probability=prior,
        probability_change_1d=changes.get(1),
        probability_change_7d=changes.get(7),
        probability_change_30d=changes.get(30),
        volume=market.volume,
        liquidity=market.liquidity,
        spread=market.spread,
        confidence_score=calibration.confidence_score,
        last_updated=updated,
        resolution_status=_resolution_status(market, as_of=as_of),
    )
    return event, calibration


def compute_probability_changes(
    *,
    current_adjusted: float,
    history: list[EventObservation],
    as_of: datetime,
    lookback_days: tuple[int, ...] | list[int],
) -> dict[int, float | None]:
    """Change in adjusted probability over each lookback window.

    Returns ``None`` for a window with no observation at or before its cutoff,
    which is different from a zero change and must stay distinguishable.
    """
    current = clamp01(current_adjusted)
    observations = _sorted_history(history)
    out: dict[int, float | None] = {}
    for days in lookback_days:
        window = int(days)
        cutoff = as_of - timedelta(days=window)
        baseline = _newest_at_or_before(observations, cutoff)
        out[window] = None if baseline is None else current - clamp01(baseline.adjusted_probability)
    return out


def observation_from_event(event: NormalizedEvent, *, timestamp: datetime | None = None) -> EventObservation:
    """Append-only history row for a normalized event."""
    return EventObservation(
        event_id=event.event_id,
        timestamp=as_utc(timestamp) or event.last_updated,
        raw_probability=event.current_probability,
        adjusted_probability=event.adjusted_probability,
        volume=event.volume,
        liquidity=event.liquidity,
        spread=event.spread,
    )


def _resolution_status(market: RawMarket, *, as_of: datetime) -> str:
    status = str(market.resolution_status or "").strip().lower()
    resolution_date = as_utc(market.resolution_date)
    if status in {"resolved_yes", "resolved_no", "cancelled"}:
        return status
    if resolution_date is not None and resolution_date <= as_of:
        # Past its resolution date but the provider has not published an
        # outcome. Treat as unknown so the impact engine excludes it instead of
        # pricing a settled event.
        return "unknown"
    return "open"


def _sorted_history(history: list[EventObservation] | None) -> list[EventObservation]:
    rows = [row for row in (history or []) if row.timestamp is not None]
    return sorted(rows, key=lambda row: as_utc(row.timestamp) or row.timestamp)


def _newest_at_or_before(observations: list[EventObservation], cutoff: datetime) -> EventObservation | None:
    best: EventObservation | None = None
    for row in observations:
        ts = as_utc(row.timestamp)
        if ts is None or ts > cutoff:
            continue
        best = row
    return best
