"""Event-probability API.

``GET  /api/event-probability/{ticker}``   panel payload for one symbol
``GET  /api/event-probability/catalog``    configured events and exposures
``POST /api/event-probability/ingest``     pull configured markets, append history

Deliberately *not* folded into ``/api/check/{ticker}``: quick check must stay
fast, and this payload may need a valuation build. The dashboard loads it
alongside quick check instead.

Both the local app and the SaaS app include this router. Events are market-wide
facts, so rows are global rather than tenant-scoped -- the exposure catalog is a
version-controlled file, not per-user state.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..recovery_map import map_failure as _map_failure
from ..route_helpers import require_api_key_if_set as _require_api_key_if_set
from ..schemas import ApiResponse

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["event-probability"], dependencies=[Depends(_require_api_key_if_set)])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent

_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")

_service_lock = threading.Lock()
_service: Any = None

_payload_lock = threading.Lock()
_payload_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err(endpoint: str, exc: Exception) -> ApiResponse:
    mapped = _map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    raw = str(mapped.get("raw_error") or "").strip()
    summary = str(mapped.get("summary") or "")
    message = headline
    if raw and raw.lower() not in summary.lower():
        message = f"{headline} - {raw[:220]}"
    return ApiResponse(ok=False, error=message, data={"recovery": mapped})


def get_service() -> Any:
    """Process-wide service instance, rebuilt only when config changes."""
    from core.event_probability.service import EventProbabilityService
    from core.event_probability.settings import build_event_probability_config

    global _service
    config = build_event_probability_config(SKILL_DIR)
    with _service_lock:
        current = _service
        if current is not None and current.config == config:
            return current
        service = EventProbabilityService.from_config(skill_dir=SKILL_DIR)
        _service = service
        return service


def reset_service_cache() -> None:
    """Drop cached service and payloads. Used by tests and config changes."""
    global _service
    with _service_lock:
        _service = None
    with _payload_lock:
        _payload_cache.clear()


def normalize_ticker(raw: str) -> str | None:
    ticker = str(raw or "").strip().upper()
    if not ticker or not _TICKER_RE.match(ticker):
        return None
    return ticker


def parse_as_of(raw: str | None) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"as_of is not an ISO-8601 timestamp: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@router.get("/api/event-probability/catalog", response_model=ApiResponse)
def event_probability_catalog() -> ApiResponse:
    """Configured exposures and layer status, for settings/diagnostics views."""
    try:
        service = get_service()
        as_of = datetime.now(timezone.utc)
        return _ok(
            {
                "config": service.config.to_dict(),
                "active": service.config.is_active(),
                "mapping_count": len(service.catalog),
                "mapping_errors": service.catalog.errors,
                "tickers": service.catalog.tickers(as_of=as_of),
                "event_ids": service.catalog.event_ids(as_of=as_of),
                "mappings": [row.to_dict() for row in service.catalog.all_mappings()],
            }
        )
    except Exception as exc:
        LOG.exception("event_probability_catalog failed")
        return _err("event_probability_catalog", exc)


@router.get("/api/event-probability/{ticker}", response_model=ApiResponse)
def event_probability_for_ticker(
    ticker: str,
    as_of: str | None = Query(default=None, description="ISO-8601 point-in-time timestamp"),
    refresh: int = Query(default=0),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Active events, sensitivities, valuation impact, and EMS for one symbol."""
    symbol = normalize_ticker(ticker)
    if symbol is None:
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL, BRK.B).")
    try:
        stamp = parse_as_of(as_of)
    except ValueError as exc:
        return ApiResponse(ok=False, error=str(exc))

    try:
        service = get_service()
        # Historical requests are never cached: an as_of payload must always be
        # rebuilt from the observation history for that timestamp.
        cache_key = symbol if stamp is None else ""
        ttl = int(service.config.cache_ttl_sec)
        if cache_key and not refresh:
            cached = _read_cache(cache_key, ttl)
            if cached is not None:
                return _ok(cached)
        payload = service.ticker_payload(db, symbol, as_of=stamp)
        if cache_key:
            _write_cache(cache_key, payload)
        return _ok(payload)
    except Exception as exc:
        LOG.exception("event_probability_for_ticker failed for %s", symbol)
        return _err("event_probability_for_ticker", exc)


@router.post("/api/event-probability/ingest", response_model=ApiResponse)
def event_probability_ingest(db: Session = Depends(get_db)) -> ApiResponse:
    """Fetch configured markets and append observations."""
    try:
        service = get_service()
        if not service.config.is_active():
            return _ok(
                {
                    "status": "disabled",
                    "reason": "feature_off",
                    "config": service.config.to_dict(),
                }
            )
        diagnostics = service.ingest(db)
        db.commit()
        reset_payload_cache()
        return _ok(
            {
                "status": "ok",
                "provider": service.config.provider,
                "diagnostics": diagnostics.to_dict(),
            }
        )
    except Exception as exc:
        db.rollback()
        LOG.exception("event_probability_ingest failed")
        return _err("event_probability_ingest", exc)


def reset_payload_cache() -> None:
    with _payload_lock:
        _payload_cache.clear()


def _read_cache(key: str, ttl_sec: int) -> dict[str, Any] | None:
    now = time.time()
    with _payload_lock:
        entry = _payload_cache.get(key)
        if entry is not None and (now - entry[0]) <= ttl_sec:
            return entry[1]
    return None


def _write_cache(key: str, payload: dict[str, Any]) -> None:
    with _payload_lock:
        _payload_cache[key] = (time.time(), payload)
