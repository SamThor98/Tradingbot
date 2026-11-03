"""SaaS research pricing routes (SEC companyfacts).

Mirrors ``routes/pricing.py`` with Supabase JWT. Companyfacts are market-wide
(one XBRL document per CIK), so the disk cache is global rather than tenant-scoped.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from ..models import User
from ..schemas import ApiResponse
from ..security import get_current_user
from .pricing import PricingAssumptions, _err, _ok, normalize_ticker
from .pricing import research_pricing_ingest as _local_ingest

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["research-pricing"])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent


@router.get("/api/research/pricing/{ticker}", response_model=ApiResponse)
def tenant_research_pricing(
    ticker: str,
    refresh: int = Query(default=0),
    user: User = Depends(get_current_user),
) -> ApiResponse:
    _ = user
    symbol = normalize_ticker(ticker)
    if symbol is None:
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL, BRK.B).")
    try:
        from core.companyfacts.service import ingest_ticker, pricing_payload

        from .pricing import attach_sharadar_cross_check

        if refresh:
            return _ok(
                attach_sharadar_cross_check(ingest_ticker(symbol, skill_dir=SKILL_DIR, force=True), symbol)
            )
        return _ok(
            attach_sharadar_cross_check(
                pricing_payload(symbol, skill_dir=SKILL_DIR, ingest_on_miss=True),
                symbol,
            )
        )
    except Exception as exc:
        LOG.exception("tenant_research_pricing failed for %s", symbol)
        return _err("research_pricing", exc)


@router.post("/api/research/pricing/{ticker}/ingest", response_model=ApiResponse)
def tenant_research_pricing_ingest(
    ticker: str,
    user: User = Depends(get_current_user),
) -> ApiResponse:
    _ = user
    return _local_ingest(ticker)


@router.post("/api/research/pricing/{ticker}/recompute", response_model=ApiResponse)
def tenant_research_pricing_recompute(
    ticker: str,
    body: PricingAssumptions | None = None,
    user: User = Depends(get_current_user),
) -> ApiResponse:
    _ = user
    symbol = normalize_ticker(ticker)
    if symbol is None:
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL, BRK.B).")
    assumptions = body or PricingAssumptions()
    try:
        from core.companyfacts.service import recompute_payload

        return _ok(
            recompute_payload(
                symbol,
                skill_dir=SKILL_DIR,
                growth=assumptions.growth,
                wacc=assumptions.wacc,
                terminal_growth=assumptions.terminal_growth,
                last_fcf=assumptions.last_fcf,
                net_debt=assumptions.net_debt,
                shares=assumptions.shares,
                price=assumptions.price,
            )
        )
    except Exception as exc:
        LOG.exception("tenant_research_pricing_recompute failed for %s", symbol)
        return _err("research_pricing_recompute", exc)
