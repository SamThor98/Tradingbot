"""Orchestration for the event-probability layer.

Two entry points:

``ingest``
    Pull the markets referenced by the exposure catalog, normalize + calibrate
    them, replace the current state row, and append an observation.
``ticker_payload``
    Build the dashboard/API payload for one ticker at one timestamp.

Both are point-in-time aware: every read goes through
``EventRepository.event_as_of`` so a historical ``as_of`` cannot observe a
probability published later. Valuation inputs are injectable so tests (and
backtests) can supply frozen fundamentals instead of live yfinance data.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.event_probability.impact import ASSUME_INDEPENDENT_EVENTS, EventExposure, compute_impact
from core.event_probability.mapper import DEFAULT_CATALOG_PATH, ExposureCatalog, load_catalog
from core.event_probability.normalizer import normalize_market, observation_from_event
from core.event_probability.providers import EventMarketProvider, build_provider
from core.event_probability.repository import EventRepository
from core.event_probability.schema import (
    EventProbabilityConfig,
    IngestDiagnostics,
    MalformedEventError,
    NormalizedEvent,
    ProviderError,
    ValuationUnavailableError,
)
from core.event_probability.sensitivity import compute_sensitivities, largest_exposures
from core.event_probability.settings import build_event_probability_config
from core.event_probability.signal import compute_event_mispricing_score
from core.event_probability.valuation import ValuationInputs, load_valuation_inputs

LOG = logging.getLogger(__name__)

SKILL_DIR = Path(__file__).resolve().parent.parent.parent

ValuationLoader = Callable[[str], ValuationInputs]
RealizedReturnLoader = Callable[..., float | None]

ASSUMPTIONS = (
    "Events are treated as statistically independent (Version 1).",
    "Scenario values come from the report DCF recompute, not a Monte Carlo simulation.",
    "Materiality scales the modeled impact once, inside the DCF shock.",
    "Adjusted probability blends the raw market toward a prior based on liquidity, spread, and staleness.",
    "Valuation inputs are current fundamentals; historical replays must supply frozen inputs.",
)


class EventProbabilityService:
    """Coordinates provider, repository, catalog, valuation, and signal layers."""

    def __init__(
        self,
        *,
        config: EventProbabilityConfig,
        provider: EventMarketProvider,
        catalog: ExposureCatalog,
        valuation_loader: ValuationLoader | None = None,
        realized_return_loader: RealizedReturnLoader | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._catalog = catalog
        self._valuation_loader = valuation_loader or load_valuation_inputs
        self._realized_return_loader = realized_return_loader or default_realized_return_pct

    @classmethod
    def from_config(
        cls,
        *,
        skill_dir: Path | None = None,
        catalog_path: Path | None = None,
    ) -> EventProbabilityService:
        base_dir = Path(skill_dir) if skill_dir is not None else SKILL_DIR
        config = build_event_probability_config(base_dir)
        catalog = load_catalog(catalog_path or (base_dir / DEFAULT_CATALOG_PATH))
        provider = build_provider(config, skill_dir=base_dir, market_ids=catalog.source_market_ids())
        return cls(config=config, provider=provider, catalog=catalog)

    @property
    def config(self) -> EventProbabilityConfig:
        return self._config

    @property
    def catalog(self) -> ExposureCatalog:
        return self._catalog

    # ------------------------------------------------------------------ ingest

    def ingest(self, db: Session, *, as_of: datetime | None = None) -> IngestDiagnostics:
        """Fetch, normalize, and persist every catalog-referenced market."""
        stamp = _as_of(as_of)
        diagnostics = IngestDiagnostics(mapping_errors=len(self._catalog.errors))
        repository = EventRepository(db)

        for market in self._fetch_markets(stamp, diagnostics):
            diagnostics.processed += 1
            try:
                event, calibration = normalize_market(
                    market,
                    as_of=stamp,
                    history=self._history_for(repository, market),
                    config=self._config,
                )
            except MalformedEventError as exc:
                diagnostics.malformed += 1
                LOG.warning("Malformed event skipped during ingest: %s", exc)
                continue

            if {"invalid_probability", "probability_out_of_range"} & set(calibration.warnings):
                diagnostics.invalid_probability += 1
                LOG.warning(
                    "Invalid probability from %s for %s; clamped to [0,1]",
                    market.source,
                    market.source_market_id,
                )
            if "stale_price" in calibration.warnings:
                diagnostics.stale += 1
                LOG.warning("Stale event observed during ingest: %s", event.event_id)

            repository.upsert_event(event)
            if repository.append_observation(observation_from_event(event, timestamp=stamp)):
                diagnostics.stored += 1
            else:
                diagnostics.duplicates += 1

        if diagnostics.mapping_errors:
            LOG.warning("Exposure catalog reported %d invalid rows", diagnostics.mapping_errors)
        return diagnostics

    def _fetch_markets(self, as_of: datetime, diagnostics: IngestDiagnostics) -> list[Any]:
        market_ids = self._catalog.source_market_ids()
        if not market_ids:
            try:
                return self._provider.list_markets(as_of=as_of)
            except ProviderError as exc:
                diagnostics.provider_errors += 1
                diagnostics.error_codes.append(exc.code)
                LOG.warning("Provider %s list_markets failed: %s", self._provider.provider_name, exc)
                return []
        markets: list[Any] = []
        for market_id in market_ids:
            try:
                market = self._provider.fetch_market(market_id, as_of=as_of)
            except ProviderError as exc:
                diagnostics.provider_errors += 1
                diagnostics.error_codes.append(exc.code)
                LOG.warning("Provider %s failed for market %s: %s", self._provider.provider_name, market_id, exc)
                continue
            except Exception as exc:
                diagnostics.provider_errors += 1
                diagnostics.error_codes.append(f"unexpected:{type(exc).__name__}")
                LOG.exception("Unexpected provider failure for market %s", market_id)
                continue
            if market is None:
                LOG.info("Provider %s has no data for market %s", self._provider.provider_name, market_id)
                continue
            markets.append(market)
        return markets

    def _history_for(self, repository: EventRepository, market: Any) -> list[Any]:
        event_id = f"{str(market.source).strip().lower()}:{str(market.source_market_id).strip()}"
        return repository.history(event_id)

    # ----------------------------------------------------------------- payload

    def ticker_payload(
        self,
        db: Session,
        ticker: str,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """Panel payload: active events, sensitivities, valuation impact, EMS."""
        symbol = str(ticker or "").strip().upper()
        stamp = _as_of(as_of)
        payload: dict[str, Any] = {
            "ticker": symbol,
            "as_of": stamp.isoformat(),
            "mode": self._config.mode,
            "provider": self._provider.provider_name,
            "config": self._config.to_dict(),
            "assumptions": list(ASSUMPTIONS),
            "independent_events": ASSUME_INDEPENDENT_EVENTS,
            "warnings": [],
            "events": [],
            "summary": _empty_summary(),
        }

        if not self._config.is_active():
            payload["status"] = "disabled"
            payload["reason"] = "feature_off"
            return payload

        mappings = self._catalog.for_ticker(symbol, as_of=stamp)
        if not mappings:
            payload["status"] = "no_exposure"
            payload["reason"] = "no_mapped_events"
            return payload

        repository = EventRepository(db)
        exposures: list[EventExposure] = []
        events_by_id: dict[str, NormalizedEvent] = {}
        warnings: list[dict[str, str]] = []

        for mapping in mappings:
            event = repository.event_as_of(
                mapping.event_id,
                as_of=stamp,
                lookback_days=self._config.lookback_days,
            )
            if event is None:
                warnings.append({"event_id": mapping.event_id, "warning": "no_observation_as_of"})
                LOG.info("No observation at or before %s for event %s", stamp.isoformat(), mapping.event_id)
                continue
            age_hours = max(0.0, (stamp - event.last_updated).total_seconds() / 3600.0)
            if age_hours > self._config.max_event_age_hours:
                warnings.append({"event_id": event.event_id, "warning": "stale_event"})
            if not event.is_open():
                warnings.append({"event_id": event.event_id, "warning": f"not_open:{event.resolution_status}"})
            events_by_id[event.event_id] = event
            exposures.append(EventExposure(event=event, mapping=mapping))

        payload["warnings"] = warnings
        if not exposures:
            payload["status"] = "no_data"
            payload["reason"] = "no_event_observations"
            return payload

        try:
            inputs = self._valuation_loader(symbol)
        except ValuationUnavailableError as exc:
            payload["status"] = "valuation_unavailable"
            payload["reason"] = str(exc)
            payload["events"] = [
                {"event": exposure.event.to_dict(), "mapping": exposure.mapping.to_dict(), "sensitivity": None}
                for exposure in exposures
            ]
            LOG.warning("Valuation unavailable for %s: %s", symbol, exc)
            return payload
        except Exception as exc:
            payload["status"] = "error"
            payload["reason"] = f"valuation_error:{type(exc).__name__}"
            LOG.exception("Valuation loader failed for %s", symbol)
            return payload

        try:
            impact = compute_impact(inputs, exposures)
            sensitivities = compute_sensitivities(inputs, exposures, base_value=impact.base_value)
        except Exception as exc:
            payload["status"] = "error"
            payload["reason"] = f"impact_error:{type(exc).__name__}"
            LOG.exception("Impact engine failed for %s", symbol)
            return payload

        realized = self._safe_realized_return(symbol, stamp)
        mispricing = compute_event_mispricing_score(
            ticker=symbol,
            events_by_id=events_by_id,
            sensitivities=sensitivities,
            lookback_days=self._config.ems_lookback_days,
            realized_return_pct=realized,
        )

        sensitivity_by_event = {row.event_id: row for row in sensitivities}
        best, worst = largest_exposures(sensitivities)

        payload["status"] = "ok"
        payload["valuation_inputs"] = inputs.to_dict()
        payload["impact"] = impact.to_dict()
        payload["mispricing"] = mispricing.to_dict()
        payload["events"] = [
            {
                "event": exposure.event.to_dict(),
                "mapping": exposure.mapping.to_dict(),
                "sensitivity": (
                    sensitivity_by_event[exposure.event.event_id].to_dict()
                    if exposure.event.event_id in sensitivity_by_event
                    else None
                ),
            }
            for exposure in exposures
        ]
        payload["summary"] = {
            "event_count": len(exposures),
            "base_value": round(impact.base_value, 6),
            "event_adjusted_expected_value": round(impact.expected_value, 6),
            "valuation_adjustment": round(impact.valuation_adjustment, 6),
            "valuation_adjustment_pct": (
                round((impact.valuation_adjustment / inputs.price) * 100.0, 6) if inputs.price > 0 else None
            ),
            "largest_positive_exposure": best.to_dict() if best is not None else None,
            "largest_negative_exposure": worst.to_dict() if worst is not None else None,
            "event_mispricing_score": mispricing.ems_gap_pct,
            "distribution": impact.distribution.to_dict(),
        }
        return payload

    def _safe_realized_return(self, ticker: str, as_of: datetime) -> float | None:
        try:
            return self._realized_return_loader(
                ticker,
                lookback_days=self._config.ems_lookback_days,
                as_of=as_of,
            )
        except Exception as exc:
            LOG.warning("Realized return unavailable for %s: %s", ticker, exc)
            return None


def default_realized_return_pct(
    ticker: str,
    *,
    lookback_days: int,
    as_of: datetime | None = None,
) -> float | None:
    """Percent price change over the lookback window from daily history."""
    from market_data import get_daily_history_with_meta

    stamp = _as_of(as_of)
    df, _meta = get_daily_history_with_meta(
        str(ticker).strip().upper(),
        days=max(int(lookback_days) * 4, 40),
        skill_dir=SKILL_DIR,
    )
    if df is None or getattr(df, "empty", True) or "close" not in getattr(df, "columns", []):
        return None

    closes = df["close"].dropna()
    if closes.empty:
        return None
    cutoff = stamp - timedelta(days=int(lookback_days))
    baseline_series = closes[[_index_to_utc(idx) <= cutoff for idx in closes.index]]
    if baseline_series.empty:
        return None
    baseline = float(baseline_series.iloc[-1])
    latest = float(closes.iloc[-1])
    if baseline <= 0:
        return None
    return ((latest / baseline) - 1.0) * 100.0


def _index_to_utc(value: Any) -> datetime:
    stamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if not isinstance(stamp, datetime):
        return datetime.min.replace(tzinfo=timezone.utc)
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _as_of(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _empty_summary() -> dict[str, Any]:
    return {
        "event_count": 0,
        "base_value": None,
        "event_adjusted_expected_value": None,
        "valuation_adjustment": None,
        "valuation_adjustment_pct": None,
        "largest_positive_exposure": None,
        "largest_negative_exposure": None,
        "event_mispricing_score": None,
        "distribution": None,
    }
