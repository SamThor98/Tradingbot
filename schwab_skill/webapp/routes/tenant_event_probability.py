"""SaaS event-probability routes.

Mirrors ``routes/event_probability.py`` with Supabase JWT auth instead of the
local API key.

Unlike position intel or the book, this data is **not tenant-scoped**: an event
probability and its exposure catalog are market-wide facts shared by every
tenant, so the service reads the deployment-level config and catalog rather than
a per-tenant skill dir. Auth still applies -- tenants must be signed in -- and
the payload cache is keyed globally because there is nothing tenant-specific in
it to leak.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..recovery_map import map_failure
from ..schemas import ApiResponse
from ..security import get_current_user
from .event_probability import get_service, normalize_ticker, parse_as_of

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["event-probability"])

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err_response(endpoint: str, exc: Exception) -> ApiResponse:
    mapped = map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    raw = str(mapped.get("raw_error") or "").strip()
    summary = str(mapped.get("summary") or "")
    err_out = headline
    if raw and raw.lower() not in summary.lower():
        err_out = f"{headline} — {raw[:220]}"
    return ApiResponse(ok=False, error=err_out, data={"recovery": mapped})


@router.get("/api/event-probability/catalog", response_model=ApiResponse)
def tenant_event_probability_catalog(
    user: User = Depends(get_current_user),
) -> ApiResponse:
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
        LOG.exception("tenant_event_probability_catalog failed")
        return _err_response("event_probability_catalog", exc)


@router.get("/api/event-probability/{ticker}", response_model=ApiResponse)
def tenant_event_probability_for_ticker(
    ticker: str,
    as_of: str | None = Query(default=None, description="ISO-8601 point-in-time timestamp"),
    refresh: int = Query(default=0, ge=0, le=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    symbol = normalize_ticker(ticker)
    if symbol is None:
        return ApiResponse(ok=False, error="Invalid ticker — use 1–10 letters/digits (e.g. AAPL, BRK.B).")
    try:
        stamp = parse_as_of(as_of)
    except ValueError as exc:
        return ApiResponse(ok=False, error=str(exc))

    try:
        service = get_service()
        ttl = int(service.config.cache_ttl_sec)
        # Historical (as_of) requests bypass the cache so a replay always
        # reconstructs from the observation history for that timestamp.
        if stamp is None and not refresh:
            now = time.time()
            with _cache_lock:
                entry = _cache.get(symbol)
                if entry is not None and (now - entry[0]) <= ttl:
                    return _ok(entry[1])
        payload = service.ticker_payload(db, symbol, as_of=stamp)
        if stamp is None:
            with _cache_lock:
                _cache[symbol] = (time.time(), payload)
                if len(_cache) > 256:
                    oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
                    if oldest != symbol:
                        del _cache[oldest]
        return _ok(payload)
    except Exception as exc:
        LOG.exception("tenant_event_probability_for_ticker failed for %s", symbol)
        return _err_response("event_probability_for_ticker", exc)


@router.post("/api/event-probability/ingest", response_model=ApiResponse)
def tenant_event_probability_ingest(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Refresh the shared event store.

    Safe for any signed-in tenant to call: writes are append-only and deduped on
    ``(event_id, timestamp)``, so a repeated ingest is a no-op rather than a way
    to distort another tenant's view.
    """
    try:
        service = get_service()
        if not service.config.is_active():
            return _ok({"status": "disabled", "reason": "feature_off", "config": service.config.to_dict()})
        diagnostics = service.ingest(db)
        db.commit()
        with _cache_lock:
            _cache.clear()
        return _ok(
            {
                "status": "ok",
                "provider": service.config.provider,
                "diagnostics": diagnostics.to_dict(),
            }
        )
    except Exception as exc:
        db.rollback()
        LOG.exception("tenant_event_probability_ingest failed")
        return _err_response("event_probability_ingest", exc)
