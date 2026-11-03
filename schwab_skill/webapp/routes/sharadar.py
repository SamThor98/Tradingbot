"""Research Sharadar Bundle snapshot API.

``GET /api/research/sharadar/{ticker}`` — cache-first PIT snapshot
(profile, daily multiples, ART fundamentals, insiders, 13F holders,
actions/events, S&P 500 membership).

Research only: does not change scan scores, ranking, sizing, or advisory.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..recovery_map import map_failure as _map_failure
from ..route_helpers import require_api_key_if_set as _require_api_key_if_set
from ..schemas import ApiResponse

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["research-sharadar"], dependencies=[Depends(_require_api_key_if_set)])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")


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


def normalize_ticker(raw: str) -> str | None:
    ticker = str(raw or "").strip().upper()
    if not ticker or not _TICKER_RE.match(ticker):
        return None
    return ticker


@router.get("/api/research/sharadar-compare/{ticker_a}/{ticker_b}", response_model=ApiResponse)
def research_sharadar_compare(
    ticker_a: str,
    ticker_b: str,
    refresh: int = Query(default=0),
) -> ApiResponse:
    left_sym = normalize_ticker(ticker_a)
    right_sym = normalize_ticker(ticker_b)
    if left_sym is None or right_sym is None:
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL, BRK.B).")
    try:
        from sharadar_data import get_sharadar_research_snapshot
        from sharadar_research import compare_bundle

        left = get_sharadar_research_snapshot(left_sym, skill_dir=SKILL_DIR, refresh=bool(refresh))
        right = get_sharadar_research_snapshot(right_sym, skill_dir=SKILL_DIR, refresh=bool(refresh))
        payload = compare_bundle(left, right)
        payload["refresh"] = bool(refresh)
        return _ok(payload)
    except Exception as exc:
        LOG.exception("research_sharadar_compare failed for %s vs %s", left_sym, right_sym)
        return _err("research_sharadar_compare", exc)


@router.get("/api/research/sharadar/{ticker}", response_model=ApiResponse)
def research_sharadar(
    ticker: str,
    refresh: int = Query(default=0),
) -> ApiResponse:
    symbol = normalize_ticker(ticker)
    if symbol is None:
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL, BRK.B).")
    try:
        from sharadar_data import get_sharadar_research_snapshot

        payload = get_sharadar_research_snapshot(
            symbol,
            skill_dir=SKILL_DIR,
            refresh=bool(refresh),
        )
        return _ok(payload)
    except Exception as exc:
        LOG.exception("research_sharadar failed for %s", symbol)
        return _err("research_sharadar", exc)
