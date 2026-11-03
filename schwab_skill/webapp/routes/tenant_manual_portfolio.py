"""SaaS manual (non-Schwab) portfolio pricing + risk pack.

Mirrors local ``POST /api/portfolio/manual/*`` with JWT auth and per-tenant
skill dirs. Rate limit is per user, not per IP.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..recovery_map import map_failure
from ..schemas import ApiResponse, ManualPortfolioBody
from ..security import get_current_user
from ..tenant_runtime import tenant_skill_dir

router = APIRouter(tags=["manual-portfolio"])

_MANUAL_RISK_CACHE_TTL_SEC = 300.0
_MANUAL_BUILD_MIN_INTERVAL_SEC = 60.0
_manual_risk_cache: dict[str, dict[str, Any]] = {}  # user_id -> {key, at, payload}
_manual_risk_cache_lock = threading.Lock()
_manual_build_last_by_user: dict[str, float] = {}
_manual_build_lock = threading.Lock()


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


def _manual_cache_key(body: ManualPortfolioBody) -> str:
    rows = sorted(
        (
            r.ticker.upper().strip(),
            float(r.qty),
            r.acquired_at.isoformat(),
            round(float(r.avg_cost), 6),
        )
        for r in body.positions
    )
    return json.dumps({"rows": rows, "cash": body.cash, "lookback": body.lookback_days})


def _manual_rate_limited(user_id: str) -> float:
    now = time.time()
    with _manual_build_lock:
        last = _manual_build_last_by_user.get(user_id, 0.0)
        wait = _MANUAL_BUILD_MIN_INTERVAL_SEC - (now - last)
        if wait > 0:
            return wait
        cutoff = now - 3600.0
        for stale_id in [k for k, v in _manual_build_last_by_user.items() if v < cutoff]:
            del _manual_build_last_by_user[stale_id]
        _manual_build_last_by_user[user_id] = now
        return 0.0


def _auth_or_none(skill_dir: Any) -> Any:
    """Best-effort Schwab auth; manual pricing still works via yfinance fallback."""
    try:
        from schwab_auth import DualSchwabAuth

        return DualSchwabAuth(skill_dir=skill_dir, auto_refresh=False)
    except Exception:
        return None


@router.post("/api/portfolio/manual/positions", response_model=ApiResponse)
def tenant_manual_portfolio_positions(
    body: ManualPortfolioBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.providers import ManualPortfolioError, ManualPortfolioProvider

        with tenant_skill_dir(db, user.id) as skill_dir:
            auth_cm = _auth_or_none(skill_dir)
            try:
                if auth_cm is not None:
                    with auth_cm as auth:
                        state, summary = ManualPortfolioProvider.build(
                            [r.model_dump() for r in body.positions],
                            cash=body.cash,
                            skill_dir=skill_dir,
                            auth=auth,
                        )
                else:
                    state, summary = ManualPortfolioProvider.build(
                        [r.model_dump() for r in body.positions],
                        cash=body.cash,
                        skill_dir=skill_dir,
                        auth=None,
                    )
            except ManualPortfolioError as exc:
                return ApiResponse(ok=False, error=str(exc), data={"unpriced_tickers": exc.unpriced})
        payload = dict(summary)
        payload["positions"] = [
            {**row, "weight_pct": round(row["market_value"] / state.equity * 100, 2) if state.equity else None}
            for row in summary["positions"]
        ]
        payload["as_of"] = datetime.now(timezone.utc).isoformat()
        return _ok(payload)
    except Exception as exc:
        return _err_response("manual_portfolio_positions", exc)


@router.post("/api/portfolio/risk-dashboard/manual", response_model=ApiResponse)
def tenant_portfolio_risk_dashboard_manual(
    body: ManualPortfolioBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        from core.portfolio_analytics_service import build_portfolio_risk_dashboard
        from core.providers import ManualPortfolioError, ManualPortfolioProvider
        from sector_strength import get_ticker_sector_etf

        from .._shared import build_portfolio_risk_analytics

        cache_key = _manual_cache_key(body)
        if not body.force:
            with _manual_risk_cache_lock:
                cached = _manual_risk_cache.get(user.id)
                if (
                    cached
                    and cached.get("key") == cache_key
                    and cached.get("payload") is not None
                    and (time.time() - float(cached.get("at") or 0)) < _MANUAL_RISK_CACHE_TTL_SEC
                ):
                    payload = dict(cached["payload"])
                    payload["cache_hit"] = True
                    return _ok(payload)

        wait = _manual_rate_limited(user.id)
        if wait > 0:
            return ApiResponse(
                ok=False,
                error=f"Rate limited: manual risk builds are capped at one per minute. Retry in {int(wait) + 1}s.",
                data={"retry_after_sec": int(wait) + 1},
            )

        with tenant_skill_dir(db, user.id) as skill_dir:
            auth_cm = _auth_or_none(skill_dir)
            try:
                if auth_cm is not None:
                    with auth_cm as auth:
                        state, summary = ManualPortfolioProvider.build(
                            [r.model_dump() for r in body.positions],
                            cash=body.cash,
                            skill_dir=skill_dir,
                            auth=auth,
                            sector_lookup=lambda t: get_ticker_sector_etf(t, skill_dir=skill_dir),
                        )
                        static_risk = build_portfolio_risk_analytics(summary, skill_dir=skill_dir)
                        pack = build_portfolio_risk_dashboard(
                            state,
                            summary,
                            static_risk=static_risk,
                            skill_dir=skill_dir,
                            auth=auth,
                            lookback_days=body.lookback_days,
                            equity_curve=[],
                        )
                else:
                    state, summary = ManualPortfolioProvider.build(
                        [r.model_dump() for r in body.positions],
                        cash=body.cash,
                        skill_dir=skill_dir,
                        auth=None,
                        sector_lookup=lambda t: get_ticker_sector_etf(t, skill_dir=skill_dir),
                    )
                    static_risk = build_portfolio_risk_analytics(summary, skill_dir=skill_dir)
                    pack = build_portfolio_risk_dashboard(
                        state,
                        summary,
                        static_risk=static_risk,
                        skill_dir=skill_dir,
                        auth=None,
                        lookback_days=body.lookback_days,
                        equity_curve=[],
                    )
            except ManualPortfolioError as exc:
                return ApiResponse(ok=False, error=str(exc), data={"unpriced_tickers": exc.unpriced})
        payload = pack.model_dump(mode="json")
        payload["source"] = "manual"
        payload["cached_at"] = datetime.now(timezone.utc).isoformat()
        payload["cache_hit"] = False
        with _manual_risk_cache_lock:
            _manual_risk_cache[user.id] = {"key": cache_key, "at": time.time(), "payload": payload}
        return _ok(payload)
    except Exception as exc:
        return _err_response("portfolio_risk_dashboard_manual", exc)
