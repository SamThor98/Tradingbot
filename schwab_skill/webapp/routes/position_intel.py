"""Position Intelligence route: conviction / volatility / options tables.

``GET /api/position-intel`` computes the four Intel tables for current open
positions on demand, with a ~5 minute in-process TTL cache. ``?refresh=1``
forces a recompute.

``GET /api/position-intel/lookup?ticker=`` runs the same analytics for a
single manually entered symbol (held or not) and caches per ticker.

Analytics live in ``core/position_intel.py``; this module only wires
fetchers (history, chains, account status) and caching.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..recovery_map import map_failure as _map_failure
from ..route_helpers import require_api_key_if_set as _require_api_key_if_set
from ..schemas import ApiResponse

router = APIRouter(tags=["position-intel"], dependencies=[Depends(_require_api_key_if_set)])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent

CACHE_TTL_SEC = 300
HISTORY_DAYS = 600  # calendar days -> ~410 trading days (SMA200 + 1y HV percentile)
CHAIN_STRIKE_COUNT = 40
# Common equity symbols: AAPL, BRK.B, BF.A — reject option OCC roots and garbage.
_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")

_cache_lock = threading.Lock()
_compute_lock = threading.Lock()
_cache: dict[str, Any] = {"payload": None, "at": 0.0}

_lookup_lock = threading.Lock()
_lookup_cache: dict[str, dict[str, Any]] = {}  # ticker -> {payload, at}


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err_response(endpoint: str, exc: Exception) -> ApiResponse:
    mapped = _map_failure(str(exc), source=endpoint)
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    raw = str(mapped.get("raw_error") or "").strip()
    summary = str(mapped.get("summary") or "")
    err_out = headline
    if raw and raw.lower() not in summary.lower():
        err_out = f"{headline} — {raw[:220]}"
    return ApiResponse(ok=False, error=err_out, data={"recovery": mapped})


def _normalize_ticker(raw: str) -> str | None:
    ticker = str(raw or "").strip().upper()
    if not ticker or not _TICKER_RE.match(ticker):
        return None
    return ticker


def _history_and_chain_fetchers(auth: Any):
    from market_data import get_daily_history, get_options_chain_with_status

    def _history(ticker: str):
        return get_daily_history(ticker, days=HISTORY_DAYS, auth=auth, skill_dir=SKILL_DIR)

    def _chain(ticker: str) -> dict[str, Any] | None:
        chain, _meta = get_options_chain_with_status(
            ticker,
            contract_type="CALL",
            strike_count=CHAIN_STRIKE_COUNT,
            auth=auth,
            skill_dir=SKILL_DIR,
        )
        return chain

    return _history, _chain


def _spot_from_history(df: Any) -> float | None:
    if df is None or getattr(df, "empty", True) or "close" not in getattr(df, "columns", []):
        return None
    try:
        return float(df["close"].iloc[-1])
    except Exception:
        return None


def _held_equity_row(ticker: str) -> dict[str, Any] | None:
    """Return portfolio equity row for ``ticker`` if currently held, else None."""
    from execution import get_account_status
    from schwab_auth import DualSchwabAuth

    from .._shared import build_portfolio_summary

    auth = DualSchwabAuth(skill_dir=SKILL_DIR)
    status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
    if isinstance(status_data, str):
        # Auth/account failure should not block a research lookup — caller
        # falls back to a synthetic position using history spot.
        return None
    summary = build_portfolio_summary(status_data)
    for pos in summary.get("positions") or []:
        if str(pos.get("symbol") or "").upper() == ticker:
            return pos
    return None


def _compute_payload() -> dict[str, Any]:
    from core.position_intel import build_position_intel
    from execution import get_account_status
    from schwab_auth import DualSchwabAuth

    from .._shared import build_portfolio_summary

    auth = DualSchwabAuth(skill_dir=SKILL_DIR)
    status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
    if isinstance(status_data, str):
        raise RuntimeError(status_data)
    summary = build_portfolio_summary(status_data)
    positions = summary.get("positions") or []
    history_fetcher, chain_fetcher = _history_and_chain_fetchers(auth)

    payload = build_position_intel(positions, history_fetcher=history_fetcher, chain_fetcher=chain_fetcher)
    payload["meta"] = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "cache_ttl_sec": CACHE_TTL_SEC,
        "positions_total": summary.get("positions_count"),
        "mode": "portfolio",
    }
    return payload


def _compute_lookup_payload(ticker: str) -> dict[str, Any]:
    """Single-ticker intel — held or research-only (spot from last close)."""
    from core.position_intel import build_position_intel
    from schwab_auth import DualSchwabAuth

    auth = DualSchwabAuth(skill_dir=SKILL_DIR)
    history_fetcher, chain_fetcher = _history_and_chain_fetchers(auth)

    held = _held_equity_row(ticker)
    if held is not None:
        position = dict(held)
        held_flag = True
        shares = int(held.get("qty") or 0)
    else:
        try:
            df = history_fetcher(ticker)
        except Exception as exc:
            raise RuntimeError(f"price history unavailable for {ticker}: {exc}") from exc
        spot = _spot_from_history(df)
        if spot is None:
            raise RuntimeError(f"price history unavailable for {ticker}")
        position = {"symbol": ticker, "qty": 0, "last": spot}
        held_flag = False
        shares = 0

    payload = build_position_intel([position], history_fetcher=history_fetcher, chain_fetcher=chain_fetcher)
    if not held_flag and payload.get("covered_calls"):
        # Covered-call row is still useful as a hypothetical income setup.
        notes = list(payload.get("data_quality") or [])
        notes.append(f"{ticker}: covered call is hypothetical — shares not held")
        payload["data_quality"] = notes
    payload["meta"] = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "cache_ttl_sec": CACHE_TTL_SEC,
        "mode": "lookup",
        "ticker": ticker,
        "held": held_flag,
        "shares": shares,
    }
    return payload


@router.get("/api/position-intel", response_model=ApiResponse)
def position_intel(refresh: int = Query(default=0, ge=0, le=1)) -> ApiResponse:
    """Four Intel tables for open positions; cached ~5 min, refresh=1 recomputes."""
    try:
        now = time.time()
        if not refresh:
            with _cache_lock:
                if _cache["payload"] is not None and (now - _cache["at"]) < CACHE_TTL_SEC:
                    payload = dict(_cache["payload"])
                    payload["meta"] = {
                        **payload.get("meta", {}),
                        "cache_hit": True,
                        "cache_age_sec": int(now - _cache["at"]),
                    }
                    return _ok(payload)

        # Single-flight: only one thread computes; latecomers re-check the cache.
        with _compute_lock:
            now = time.time()
            if not refresh:
                with _cache_lock:
                    if _cache["payload"] is not None and (now - _cache["at"]) < CACHE_TTL_SEC:
                        payload = dict(_cache["payload"])
                        payload["meta"] = {
                            **payload.get("meta", {}),
                            "cache_hit": True,
                            "cache_age_sec": int(now - _cache["at"]),
                        }
                        return _ok(payload)
            payload = _compute_payload()
            with _cache_lock:
                _cache["payload"] = payload
                _cache["at"] = time.time()
        payload = dict(payload)
        payload["meta"] = {**payload.get("meta", {}), "cache_hit": False, "cache_age_sec": 0}
        return _ok(payload)
    except Exception as exc:
        return _err_response("position_intel", exc)


@router.get("/api/position-intel/lookup", response_model=ApiResponse)
def position_intel_lookup(
    ticker: str = Query(..., min_length=1, max_length=12),
    refresh: int = Query(default=0, ge=0, le=1),
) -> ApiResponse:
    """Single-ticker Intel tables for a manually entered symbol."""
    try:
        symbol = _normalize_ticker(ticker)
        if symbol is None:
            return ApiResponse(
                ok=False,
                error="Invalid ticker — use 1–10 letters/digits (e.g. AAPL, BRK.B).",
            )

        now = time.time()
        if not refresh:
            with _lookup_lock:
                entry = _lookup_cache.get(symbol)
                if entry and (now - entry["at"]) < CACHE_TTL_SEC:
                    payload = dict(entry["payload"])
                    payload["meta"] = {
                        **payload.get("meta", {}),
                        "cache_hit": True,
                        "cache_age_sec": int(now - entry["at"]),
                    }
                    return _ok(payload)

        payload = _compute_lookup_payload(symbol)
        with _lookup_lock:
            _lookup_cache[symbol] = {"payload": payload, "at": time.time()}
            # Bound memory: drop oldest when the map grows large.
            if len(_lookup_cache) > 64:
                oldest = min(_lookup_cache.items(), key=lambda kv: kv[1]["at"])[0]
                if oldest != symbol:
                    del _lookup_cache[oldest]

        out = dict(payload)
        out["meta"] = {**out.get("meta", {}), "cache_hit": False, "cache_age_sec": 0}
        return _ok(out)
    except Exception as exc:
        return _err_response("position_intel_lookup", exc)
