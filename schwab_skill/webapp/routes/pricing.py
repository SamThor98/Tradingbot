"""Research pricing-model API backed by SEC companyfacts.

``GET  /api/research/pricing/{ticker}``        cache-first DCF + provenance
``POST /api/research/pricing/{ticker}/ingest`` fetch the XBRL document once
``POST /api/research/pricing/{ticker}/recompute`` CPU-only assumption overlay

Not folded into ``/api/check/{ticker}``: Quick Check stays fast, and a first
ingest is one SEC request. Slider moves never hit the network.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..recovery_map import map_failure as _map_failure
from ..route_helpers import require_api_key_if_set as _require_api_key_if_set
from ..schemas import ApiResponse

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["research-pricing"], dependencies=[Depends(_require_api_key_if_set)])

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")


class PricingAssumptions(BaseModel):
    growth: float | None = Field(default=None, ge=-0.9, le=1.0)
    wacc: float | None = Field(default=None, ge=0.01, le=0.6)
    terminal_growth: float | None = Field(default=None, ge=-0.05, le=0.1)
    last_fcf: float | None = None
    net_debt: float | None = None
    shares: float | None = Field(default=None, gt=0)
    price: float | None = Field(default=None, ge=0)


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


def attach_sharadar_cross_check(payload: dict[str, Any], ticker: str) -> dict[str, Any]:
    """Add a labeled Sharadar column. Never mixed into the companyfacts DCF."""
    out = dict(payload or {})
    try:
        from sharadar_research import pricing_cross_check, snapshot_for_research

        snap = snapshot_for_research(ticker, skill_dir=SKILL_DIR)
        out["sharadar_cross_check"] = pricing_cross_check(snap)
    except Exception as exc:
        LOG.warning("Sharadar pricing cross-check skipped for %s: %s", ticker, exc)
        out["sharadar_cross_check"] = {
            "available": False,
            "status": "error",
            "enabled": False,
            "errors": [f"sharadar_overlay_error:{type(exc).__name__}"],
            "label": "Sharadar Bundle",
        }
    return out


@router.get("/api/research/pricing/{ticker}", response_model=ApiResponse)
def research_pricing(
    ticker: str,
    refresh: int = Query(default=0),
) -> ApiResponse:
    symbol = normalize_ticker(ticker)
    if symbol is None:
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL, BRK.B).")
    try:
        from core.companyfacts.service import ingest_ticker, pricing_payload

        if refresh:
            return _ok(attach_sharadar_cross_check(ingest_ticker(symbol, skill_dir=SKILL_DIR, force=True), symbol))
        return _ok(
            attach_sharadar_cross_check(
                pricing_payload(symbol, skill_dir=SKILL_DIR, ingest_on_miss=True),
                symbol,
            )
        )
    except Exception as exc:
        LOG.exception("research_pricing failed for %s", symbol)
        return _err("research_pricing", exc)


@router.post("/api/research/pricing/{ticker}/ingest", response_model=ApiResponse)
def research_pricing_ingest(ticker: str) -> ApiResponse:
    symbol = normalize_ticker(ticker)
    if symbol is None:
        return ApiResponse(ok=False, error="Invalid ticker - use 1-10 letters/digits (e.g. AAPL, BRK.B).")
    try:
        from core.companyfacts.service import ingest_ticker

        return _ok(attach_sharadar_cross_check(ingest_ticker(symbol, skill_dir=SKILL_DIR, force=True), symbol))
    except Exception as exc:
        LOG.exception("research_pricing_ingest failed for %s", symbol)
        return _err("research_pricing_ingest", exc)


@router.post("/api/research/pricing/{ticker}/recompute", response_model=ApiResponse)
def research_pricing_recompute(ticker: str, body: PricingAssumptions | None = None) -> ApiResponse:
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
        LOG.exception("research_pricing_recompute failed for %s", symbol)
        return _err("research_pricing_recompute", exc)
