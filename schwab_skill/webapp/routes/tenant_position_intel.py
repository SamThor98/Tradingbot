"""SaaS Position Intelligence routes.

Mirrors ``routes/position_intel.py`` with JWT auth, per-tenant skill dirs,
and caches keyed by user_id so tenants cannot share payloads.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..recovery_map import map_failure
from ..schemas import ApiResponse
from ..security import get_current_user
from ..tenant_runtime import tenant_skill_dir, user_can_materialize_for_scan, user_has_account_session
from .position_intel import (
    CACHE_TTL_SEC,
    compute_position_intel_lookup_payload,
    compute_position_intel_payload,
    normalize_ticker,
)

router = APIRouter(tags=["position-intel"])

_cache_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}  # user_id -> {payload, at}
_compute_locks_guard = threading.Lock()
_compute_locks: dict[str, threading.Lock] = {}
_lookup_lock = threading.Lock()
_lookup_cache: dict[tuple[str, str], dict[str, Any]] = {}  # (user_id, ticker) -> {payload, at}


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


def _user_compute_lock(user_id: str) -> threading.Lock:
    with _compute_locks_guard:
        lock = _compute_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _compute_locks[user_id] = lock
        return lock


def _cached_payload(user_id: str, now: float) -> dict[str, Any] | None:
    with _cache_lock:
        entry = _cache.get(user_id)
        if entry and entry.get("payload") is not None and (now - float(entry.get("at") or 0)) < CACHE_TTL_SEC:
            payload = dict(entry["payload"])
            payload["meta"] = {
                **payload.get("meta", {}),
                "cache_hit": True,
                "cache_age_sec": int(now - float(entry["at"])),
            }
            return payload
    return None


@router.get("/api/position-intel", response_model=ApiResponse)
def tenant_position_intel(
    refresh: int = Query(default=0, ge=0, le=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if not user_has_account_session(db, user.id):
        return ApiResponse(ok=False, error="Link Schwab account before loading position intel.")
    try:
        now = time.time()
        if not refresh:
            cached = _cached_payload(user.id, now)
            if cached is not None:
                return _ok(cached)

        with _user_compute_lock(user.id):
            now = time.time()
            if not refresh:
                cached = _cached_payload(user.id, now)
                if cached is not None:
                    return _ok(cached)
            from schwab_auth import DualSchwabAuth

            with tenant_skill_dir(db, user.id) as skill_dir:
                with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
                    payload = compute_position_intel_payload(skill_dir, auth=auth)
            with _cache_lock:
                _cache[user.id] = {"payload": payload, "at": time.time()}
        out = dict(payload)
        out["meta"] = {**out.get("meta", {}), "cache_hit": False, "cache_age_sec": 0}
        return _ok(out)
    except Exception as exc:
        return _err_response("position_intel", exc)


@router.get("/api/position-intel/lookup", response_model=ApiResponse)
def tenant_position_intel_lookup(
    ticker: str = Query(..., min_length=1, max_length=12),
    refresh: int = Query(default=0, ge=0, le=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    ok, reason = user_can_materialize_for_scan(db, user.id)
    if not ok:
        return ApiResponse(ok=False, error=reason)
    try:
        symbol = normalize_ticker(ticker)
        if symbol is None:
            return ApiResponse(
                ok=False,
                error="Invalid ticker — use 1–10 letters/digits (e.g. AAPL, BRK.B).",
            )

        cache_key = (user.id, symbol)
        now = time.time()
        if not refresh:
            with _lookup_lock:
                entry = _lookup_cache.get(cache_key)
                if entry and (now - float(entry["at"])) < CACHE_TTL_SEC:
                    payload = dict(entry["payload"])
                    payload["meta"] = {
                        **payload.get("meta", {}),
                        "cache_hit": True,
                        "cache_age_sec": int(now - float(entry["at"])),
                    }
                    return _ok(payload)

        from schwab_auth import DualSchwabAuth

        with tenant_skill_dir(db, user.id) as skill_dir:
            with DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False) as auth:
                payload = compute_position_intel_lookup_payload(symbol, skill_dir, auth=auth)
        with _lookup_lock:
            _lookup_cache[cache_key] = {"payload": payload, "at": time.time()}
            if len(_lookup_cache) > 128:
                oldest = min(_lookup_cache.items(), key=lambda kv: kv[1]["at"])[0]
                if oldest != cache_key:
                    del _lookup_cache[oldest]
        out = dict(payload)
        out["meta"] = {**out.get("meta", {}), "cache_hit": False, "cache_age_sec": 0}
        return _ok(out)
    except Exception as exc:
        return _err_response("position_intel_lookup", exc)
