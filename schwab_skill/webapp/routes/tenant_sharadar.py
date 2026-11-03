"""SaaS Sharadar Bundle routes. Mirrors ``routes/sharadar.py`` with JWT."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from ..models import User
from ..schemas import ApiResponse
from ..security import get_current_user
from .sharadar import _err, _ok, normalize_ticker

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["research-sharadar"])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent


@router.get("/api/research/sharadar-compare/{ticker_a}/{ticker_b}", response_model=ApiResponse)
def tenant_research_sharadar_compare(
    ticker_a: str,
    ticker_b: str,
    refresh: int = Query(default=0),
    user: User = Depends(get_current_user),
) -> ApiResponse:
    _ = user
    left_sym = normalize_ticker(ticker_a)
    right_sym = normalize_ticker(ticker_b)
    if left_sym is None or right_sym is None:
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL, BRK.B).")
    try:
        from sharadar_data import get_sharadar_research_snapshot
        from sharadar_research import compare_bundle

        payload = compare_bundle(
            get_sharadar_research_snapshot(left_sym, skill_dir=SKILL_DIR, refresh=bool(refresh)),
            get_sharadar_research_snapshot(right_sym, skill_dir=SKILL_DIR, refresh=bool(refresh)),
        )
        payload["refresh"] = bool(refresh)
        return _ok(payload)
    except Exception as exc:
        LOG.exception("tenant_research_sharadar_compare failed for %s vs %s", left_sym, right_sym)
        return _err("research_sharadar_compare", exc)


@router.get("/api/research/sharadar/{ticker}", response_model=ApiResponse)
def tenant_research_sharadar(
    ticker: str,
    refresh: int = Query(default=0),
    user: User = Depends(get_current_user),
) -> ApiResponse:
    _ = user
    symbol = normalize_ticker(ticker)
    if symbol is None:
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL, BRK.B).")
    try:
        from sharadar_data import get_sharadar_research_snapshot

        return _ok(
            get_sharadar_research_snapshot(
                symbol,
                skill_dir=SKILL_DIR,
                refresh=bool(refresh),
            )
        )
    except Exception as exc:
        LOG.exception("tenant_research_sharadar failed for %s", symbol)
        return _err("research_sharadar", exc)
