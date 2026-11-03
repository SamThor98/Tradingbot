# ruff: noqa: E402
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import re
import secrets
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

# Promote schwab_skill/.env values into os.environ before any module that
# only consults os.getenv (strategy chat, SEC filing summaries, etc.) is
# imported. Process-set vars (Render/Docker) still win because the helper
# never overwrites a populated os.environ entry.
from config import bootstrap_dotenv_into_environ

bootstrap_dotenv_into_environ()

from core.scan_service import run_scan, summarize_live_strategy
from execution import get_account_status, get_position_size_usd, place_order
from market_data import extract_schwab_last_price, get_current_quote, get_current_quote_with_status
from schwab_auth import DualSchwabAuth, write_encrypted_token_file
from sector_strength import get_sector_heatmap

from ._shared import (
    build_portfolio_risk_analytics as _shared_build_portfolio_risk_analytics,
)
from ._shared import (
    build_portfolio_summary as _shared_build_portfolio_summary,
)
from ._shared import (
    manual_jwt_entry_enabled as _manual_jwt_entry_enabled,
)
from ._shared import (
    quote_health_hint as _quote_health_hint,
)
from ._shared import (
    rollup_connection_state as _rollup_connection_state,
)
from ._shared import (
    trade_to_dict as _trade_to_dict,
)
from .backtest_hub import (
    annotate_run_list_item,
    artifact_detail,
    build_hub,
    list_artifact_catalog,
    previous_summary_map,
    public_trade_progress,
    resolve_artifact_path,
    result_metrics_summary,
    serialize_run,
    stored_result_parts,
)
from .backtest_labels import attach_run_labels
from .backtest_leaderboard import SCAN_CAP, leaderboard_item_from_parts
from .checklist_language import with_plain_language
from .cors_config import build_allowed_origins
from .db import DATABASE_URL, Base, SessionLocal, engine
from .decision_dashboard_service import (
    build_decision_dashboard_snapshot,
    build_shadow_scoreboard_payload,
)
from .decision_dashboard_service import (
    latest_validation_status as _latest_validation_status,
)
from .decision_dashboard_service import (
    signal_edge_scan_preflight as _signal_edge_scan_preflight,
)
from .decision_dashboard_service import (
    signal_edge_shadow_summary as _signal_edge_shadow_summary,
)
from .models import AppState, BacktestRun, PendingTrade, ScanResult, User
from .oauth_schwab import exchange_schwab_code_for_tokens, schwab_authorize_url
from .preset_catalog import PRESET_PROFILES, build_preset_catalog_payload
from .recovery_map import map_failure as _map_failure
from .response_helpers import api_err, json_default
from .route_helpers import (
    apply_profile_to_runtime as _shared_apply_profile_to_runtime,
)
from .route_helpers import (
    env_flag as _env_flag,
)
from .route_helpers import (
    is_loopback_host as _shared_is_loopback_host,
)
from .route_helpers import (
    ok as _shared_ok,
)
from .route_helpers import (
    request_origin as _shared_request_origin,
)
from .route_helpers import (
    resolve_schwab_redirect_uri as _shared_resolve_schwab_redirect_uri,
)
from .routes.book import router as book_router
from .routes.event_probability import router as event_probability_router
from .routes.grok_agent import router as grok_agent_router
from .routes.learning import router as learning_router
from .routes.liquid import router as liquid_router
from .routes.operator_desk import router as operator_desk_router
from .routes.position_intel import router as position_intel_router
from .routes.pricing import router as pricing_router
from .routes.research import router as research_router
from .routes.sharadar import router as sharadar_router
from .scan_payload import parse_scan_run_body, scan_runtime_kwargs
from .schemas import (
    ApiResponse,
    ApproveTradeRequest,
    BacktestStudyNextRequest,
    CreatePendingTrade,
    ManualPortfolioBody,
    PluginModeWriteRequest,
    QueueUserBacktestRequest,
)
from .security_headers import SecurityHeadersMiddleware
from .static_assets import NoCacheStaticFiles, render_dashboard_html

LOCAL_DASHBOARD_USER_ID = (os.getenv("WEB_LOCAL_USER_ID", "local") or "local").strip() or "local"

LOG = logging.getLogger("webapp")
if not LOG.handlers:
    logging.basicConfig(level=logging.INFO)

APP_DIR = Path(__file__).resolve().parent
SKILL_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
AUDIT_LOG_PATH = APP_DIR / "audit.log"
VALIDATION_ARTIFACT_DIR = SKILL_DIR / "validation_artifacts"
EXPERIMENT_REGISTRY_PATH = VALIDATION_ARTIFACT_DIR / "experiment_registry.jsonl"
BACKTEST_RESULTS_PATH = SKILL_DIR / ".backtest_results.json"
TRADE_OUTCOMES_PATH = SKILL_DIR / ".trade_outcomes.json"
EXECUTION_METRICS_PATH = SKILL_DIR / "execution_safety_metrics.json"
# Token files default to the source dir, but honour SCHWAB_TOKEN_DIR so a
# single-user deploy on an ephemeral host (e.g. a Render web instance) can
# persist tokens on a mounted disk outside the read-only source tree. Must
# match the resolution in schwab_auth.DualSchwabAuth so reads and writes agree.
TOKEN_DIR = Path((os.getenv("SCHWAB_TOKEN_DIR") or "").strip() or SKILL_DIR)
TOKENS_MARKET_PATH = TOKEN_DIR / "tokens_market.enc"
TOKENS_ACCOUNT_PATH = TOKEN_DIR / "tokens_account.enc"
ONBOARDING_TARGET_MINUTES = 20
DEFAULT_AUTOMATION_OPT_IN = False
DEFAULT_UI_MODE = "standard"
DEFAULT_PROFILE = "balanced"
_LOCAL_OAUTH_STATE_TTL_SEC = 600

# Process-wide shared Schwab auth. Read endpoints reuse a single
# ``DualSchwabAuth`` so there is exactly one background refresh thread per
# session for the whole process. Building a fresh ``DualSchwabAuth`` per
# request (the previous behaviour) spawned a new 25-min refresh thread on the
# first token read of each request; those orphan threads accumulated and raced
# on Schwab's single-use refresh tokens, causing ``400 unsupported_token_type``
# storms that invalidated both sessions intermittently.
_shared_auth: DualSchwabAuth | None = None
_shared_auth_lock = threading.Lock()


def get_shared_auth() -> DualSchwabAuth:
    """Return the process-wide shared ``DualSchwabAuth`` (lazily created).

    The singleton keeps a single refresh thread per session alive for the
    lifetime of the process. Call ``reset_shared_auth()`` after dashboard OAuth
    writes new token files so the next request reloads from disk.
    """
    global _shared_auth
    with _shared_auth_lock:
        if _shared_auth is None:
            _shared_auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        return _shared_auth


def reset_shared_auth() -> None:
    """Drop the cached ``DualSchwabAuth`` so the next call reloads token files."""
    global _shared_auth
    with _shared_auth_lock:
        if _shared_auth is not None:
            try:
                _shared_auth.close()
            except Exception as exc:
                LOG.debug("shared auth close during reset failed: %s", exc)
            _shared_auth = None


def _session_token_ok(session: Any) -> bool:
    """Non-raising connection probe for a single Schwab session.

    ``SchwabSession.get_access_token`` returns ``None`` (never raises) when the
    session is unauthenticated, so one disconnected session can never mask the
    other's state or blow up the whole status/health response.
    """
    try:
        return bool(session.get_access_token())
    except Exception:
        return False


def _session_connection_state(present: bool, refresh_status: str) -> tuple[bool, str]:
    """Map (token present, refresh-token health) to (usable, display state).

    A present-but-expired refresh token cannot be refreshed, so the cached
    access token will 401 on every call — surfacing "Connected" in that case is
    misleading (the user reconnects and nothing works). Report it as
    "Reauth needed" and treat the session as not usable so the health ribbon and
    downstream gates are honest. ``refresh_status`` is the per-session value from
    ``DualSchwabAuth.get_token_health()`` ("expired"|"critical"|"warn"|
    "healthy"|"unknown"). Note: an age-based probe cannot detect a *revoked*
    token that is still young; the live probe in ``/api/health/deep`` covers
    that case.
    """
    if not present:
        return False, "Disconnected"
    if refresh_status == "expired":
        return False, "Reauth needed"
    return True, "Connected"


# Sector heatmap is the one read endpoint that fans out across ~12 ETF symbols
# over live market data. When Schwab market data is degraded (e.g. a 401
# entitlement issue) each symbol burns Schwab retries + a yfinance fallback, so
# the whole call can take minutes and the request hangs. We run it under a time
# budget with single-flight semantics (one background computation at a time) and
# serve the last good result while a slow refresh runs in the background.
_SECTORS_TIME_BUDGET_SEC = float((os.getenv("SECTORS_TIME_BUDGET_SEC") or "8").strip() or "8")
_sectors_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sectors-heatmap")
_sectors_lock = threading.Lock()
_sectors_future: Future | None = None
_sectors_cache: dict[str, Any] = {"data": None, "at": None}


def _compute_sector_heatmap() -> dict[str, Any]:
    return get_sector_heatmap(auth=get_shared_auth(), skill_dir=SKILL_DIR)


def _on_sectors_done(fut: Future) -> None:
    """Cache a completed heatmap and clear the in-flight slot (runs in worker)."""
    global _sectors_future
    with _sectors_lock:
        try:
            _sectors_cache["data"] = fut.result()
            _sectors_cache["at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            LOG.warning("Sector heatmap computation failed: %s", exc)
        finally:
            if _sectors_future is fut:
                _sectors_future = None


def _ensure_local_dashboard_user() -> None:
    db = SessionLocal()
    try:
        if db.get(User, LOCAL_DASHBOARD_USER_ID) is None:
            db.add(
                User(
                    id=LOCAL_DASHBOARD_USER_ID,
                    email=None,
                    auth_provider="local_dashboard",
                )
            )
            db.commit()
    finally:
        db.close()


def _fail_orphaned_local_backtest_runs() -> None:
    """Mark queued/running local backtest rows as failed after a restart.

    Local backtests run in an in-process daemon thread, so a server restart
    kills the run but leaves the row in a non-terminal state; without this the
    dashboard would show a run as "running" forever.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(BacktestRun)
            .filter(
                BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID,
                BacktestRun.status.in_(("queued", "running")),
            )
            .all()
        )
        for row in rows:
            row.status = "failed"
            row.error_message = "Server restarted before the backtest finished. Queue it again."
        if rows:
            db.commit()
            LOG.info("Marked %d orphaned local backtest run(s) as failed after restart.", len(rows))
    except Exception as exc:
        LOG.warning("Orphaned backtest cleanup skipped: %s", exc)
        db.rollback()
    finally:
        db.close()


def _run_alembic_upgrade_head_for_sqlite() -> None:
    """Apply Alembic revisions so existing SQLite files gain new columns (e.g. Stripe billing)."""
    if (os.getenv("WEBAPP_SKIP_ALEMBIC") or "").strip().lower() in ("1", "true", "yes", "on"):
        return
    if not DATABASE_URL.startswith("sqlite"):
        return
    alembic_ini = APP_DIR.parent / "alembic.ini"
    if not alembic_ini.is_file():
        return
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config(str(alembic_ini)), "head")


def _validate_startup_configuration() -> None:
    env = (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower()
    production_like = env in ("prod", "production", "staging") or bool((os.getenv("RENDER") or "").strip())
    if not production_like:
        return
    configured = (os.getenv("WEB_API_KEY") or "").strip()
    if configured:
        return
    unsafe = (os.getenv("WEB_ALLOW_UNSAFE_LOCAL_WRITES") or "").strip().lower() in ("1", "true", "yes", "on")
    if not unsafe:
        raise RuntimeError("WEB_API_KEY is required in production-like environments.")


def _is_production_like() -> bool:
    env = (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower()
    return env in ("prod", "production", "staging") or bool((os.getenv("RENDER") or "").strip())


Base.metadata.create_all(bind=engine)
try:
    from feature_store import ensure_table as _ensure_feature_store_table

    _ensure_feature_store_table()
except Exception:
    pass
_run_alembic_upgrade_head_for_sqlite()
_ensure_local_dashboard_user()
_fail_orphaned_local_backtest_runs()
_validate_startup_configuration()


@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    """App lifespan: drain a waiting local backtest, then stop background threads."""
    try:
        db = SessionLocal()
        try:
            with _local_backtest_lock:
                _kick_next_local_backtest_locked(db)
        finally:
            db.close()
    except Exception as exc:
        LOG.debug("startup backtest kick failed: %s", exc)
    desk_stop = threading.Event()

    def _operator_desk_clock() -> None:
        while True:
            try:
                from operator_desk import tick_operator_desk

                tick_operator_desk(SKILL_DIR)
            except Exception as exc:
                LOG.warning("Operator desk clock failed: %s", exc)
            try:
                from core.tradeable_brief import tick_tradeable_brief

                tick_tradeable_brief(SKILL_DIR)
            except Exception as exc:
                LOG.warning("Tradeable brief clock failed: %s", exc)
            if desk_stop.wait(20):
                break

    threading.Thread(target=_operator_desk_clock, daemon=True, name="operator-desk-clock").start()
    yield
    desk_stop.set()
    if _shared_auth is not None:
        try:
            _shared_auth.close()
        except Exception as exc:
            LOG.debug("shared auth close on shutdown failed: %s", exc)
    try:
        _sectors_executor.shutdown(wait=False, cancel_futures=True)
    except Exception as exc:
        LOG.debug("sectors executor shutdown failed: %s", exc)


app = FastAPI(
    title="TradingBot Web Dashboard API",
    version="0.2.0",
    description="Web API for scanning, approvals, portfolio, and sector health.",
    lifespan=_lifespan,
)

allowed_origins = build_allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-User"],
)

app.add_middleware(SecurityHeadersMiddleware)

app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")

app.include_router(research_router)
app.include_router(operator_desk_router)
app.include_router(learning_router)
app.include_router(book_router)
app.include_router(position_intel_router)
app.include_router(event_probability_router)
app.include_router(grok_agent_router)
app.include_router(pricing_router)
app.include_router(sharadar_router)
app.include_router(liquid_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        msg = str(detail.get("message") or detail.get("error") or detail)
    elif isinstance(detail, list):
        msg = "; ".join(str(item) for item in detail)
    else:
        msg = str(detail or "Request failed.")
    payload = api_err(msg).model_dump()
    payload["detail"] = msg
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse | PlainTextResponse:
    if request.url.path.startswith("/api/"):
        LOG.exception("Unhandled API error on %s: %s", request.url.path, exc)
        if _is_production_like():
            msg = "Internal server error."
        else:
            msg = f"Internal server error: {type(exc).__name__}: {str(exc)[:220]}"
        payload = api_err(msg).model_dump()
        payload["detail"] = msg
        return JSONResponse(status_code=500, content=payload)
    LOG.exception("Unhandled non-API error on %s: %s", request.url.path, exc)
    return PlainTextResponse("Internal server error.", status_code=500)


_metrics_lock = threading.Lock()
_request_metrics: dict[str, Any] = {
    "requests_total": 0,
    # Counts HTTP 5xx only (plus worker `_record_endpoint_error`). Client 4xx live in `client_errors_total`.
    "errors_total": 0,
    "client_errors_total": 0,
    "by_path": {},
    "endpoint_errors": {},
}

# Cap persisted scan payloads so AppState rows stay reasonable.
_LAST_SCAN_SIGNALS_CAP = min(200, int(os.getenv("WEB_LAST_SCAN_SIGNALS_CAP", "120") or 120))
# Hard cap for a local scan thread. A full SP1500 pass with fallbacks can run
# past 90 minutes (2026-10-29 finished in 5597s). Failing the job at 60 minutes
# made the dashboard give up while that worker was still enriching.
_SCAN_STALE_SECONDS = max(60, int(os.getenv("WEB_SCAN_STALE_SECONDS", "10800") or 10800))
_scan_lock = threading.Lock()
_scan_worker_thread: threading.Thread | None = None
_scan_job: dict[str, Any] = {
    "job_id": None,
    "status": "idle",  # idle | running | completed | failed
    "started_at": None,
    "finished_at": None,
    "signals_found": None,
    "diagnostics": None,
    "diagnostics_summary": None,
    "strategy_summary": None,
    "signals": [],
    # Full Stage-B shortlist with disposition tags. Always populated when the
    # scanner is run via core.scan_service.run_scan; the dashboard surfaces
    # this so operators can see filtered candidates alongside survivors.
    "shortlist_signals": [],
    "error": None,
}

# Local backtest queue: one run at a time in a daemon thread, persisted to the
# existing BacktestRun table so the frontend can reuse the SaaS queue contract
# (`/api/backtest-runs*`) unchanged. `celery_task_id` stores the run id on
# local rows so the task-status route resolves with the same URL shape.
# Extra jobs stay `queued` and drain FIFO when the worker finishes.
_local_backtest_lock = threading.Lock()
_study_queue_lock = threading.Lock()
_local_backtest_thread: threading.Thread | None = None
LOCAL_BACKTEST_MAX_WAITING = 8

# Map local BacktestRun.status values onto the Celery status vocabulary the
# frontend poller already understands.
_LOCAL_BACKTEST_STATUS_TO_CELERY = {
    "queued": "pending",
    "running": "started",
    "success": "success",
    "failed": "failure",
    "cancelled": "revoked",
}


_sse_subscribers: list[queue.Queue] = []
_sse_subscribers_lock = threading.Lock()
_local_oauth_states: dict[str, dict[str, Any]] = {}
_local_oauth_state_lock = threading.Lock()


def _sse_publish(event: str, data: dict[str, Any] | None = None) -> None:
    payload = json.dumps({"event": event, **(data or {})}, default=_json_default)
    with _sse_subscribers_lock:
        dead: list[queue.Queue] = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_subscribers.remove(q)


def _record_endpoint_error(endpoint: str) -> None:
    with _metrics_lock:
        bucket = _request_metrics.setdefault("endpoint_errors", {})
        bucket[endpoint] = int(bucket.get(endpoint, 0) or 0) + 1
        _request_metrics["errors_total"] = int(_request_metrics.get("errors_total", 0) or 0) + 1


def _record_request(path: str, method: str, status_code: int, latency_ms: float) -> None:
    key = f"{method} {path}"
    with _metrics_lock:
        _request_metrics["requests_total"] = int(_request_metrics.get("requests_total", 0) or 0) + 1
        bucket = _request_metrics.setdefault("by_path", {}).setdefault(
            key,
            {
                "count": 0,
                "errors": 0,
                "client_errors": 0,
                "server_errors": 0,
                "last_status": 0,
                "last_latency_ms": 0.0,
            },
        )
        bucket["count"] = int(bucket.get("count", 0) or 0) + 1
        bucket["last_status"] = status_code
        bucket["last_latency_ms"] = round(latency_ms, 2)
        if status_code >= 500:
            bucket["server_errors"] = int(bucket.get("server_errors", 0) or 0) + 1
            bucket["errors"] = int(bucket.get("errors", 0) or 0) + 1
            _request_metrics["errors_total"] = int(_request_metrics.get("errors_total", 0) or 0) + 1
        elif status_code >= 400:
            bucket["client_errors"] = int(bucket.get("client_errors", 0) or 0) + 1
            bucket["errors"] = int(bucket.get("errors", 0) or 0) + 1
            _request_metrics["client_errors_total"] = int(_request_metrics.get("client_errors_total", 0) or 0) + 1


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    _record_request(request.url.path, request.method, response.status_code, elapsed_ms)
    LOG.info("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code, elapsed_ms)

    if request.url.path.startswith("/api/"):
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
        response.headers["Cache-Control"] = "no-store"
    return response


def _ok(data: Any = None) -> ApiResponse:
    return _shared_ok(data)


def _json_default(value: Any) -> Any:
    return json_default(value)


def _err(endpoint: str, exc: Exception) -> ApiResponse:
    _record_endpoint_error(endpoint)
    mapped = _map_failure(str(exc), source=endpoint)
    raw = str(mapped.get("raw_error") or "").strip()
    headline = f"{mapped.get('title', 'Error')}: {mapped.get('summary', 'Something went wrong.')}"
    summary = str(mapped.get("summary") or "")
    err_out = headline
    if raw and raw.lower() not in summary.lower():
        err_out = f"{headline} — {raw[:220]}"
    return api_err(err_out, {"recovery": mapped})


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_ui_settings(db: Session) -> dict[str, Any]:
    return _load_state(
        db,
        key="ui_settings",
        default={
            "mode": DEFAULT_UI_MODE,
            "profile": DEFAULT_PROFILE,
            "automation_opt_in": DEFAULT_AUTOMATION_OPT_IN,
        },
    )


def _apply_profile_to_runtime(profile: str) -> dict[str, str]:
    return _shared_apply_profile_to_runtime(profile)


def _token_health() -> dict[str, Any]:
    return {
        "market_token_file": TOKENS_MARKET_PATH.exists(),
        "account_token_file": TOKENS_ACCOUNT_PATH.exists(),
    }


def _is_loopback_host(hostname: str) -> bool:
    return _shared_is_loopback_host(hostname)


def _request_origin(request: Request) -> str:
    return _shared_request_origin(request)


def _resolve_schwab_redirect_uri(request: Request, *, market: bool) -> str:
    return _shared_resolve_schwab_redirect_uri(request, market=market)


def _single_schwab_callback_uri(request: Request) -> str:
    uri = _resolve_schwab_redirect_uri(request, market=False)
    return uri.replace("/api/oauth/schwab/market/callback", "/api/oauth/schwab/callback")


def _oauth_wants_browser_redirect(request: Request, redirect: bool) -> bool:
    if redirect:
        return True
    accept = (request.headers.get("accept") or "").lower()
    sec_fetch_mode = (request.headers.get("sec-fetch-mode") or "").lower()
    sec_fetch_dest = (request.headers.get("sec-fetch-dest") or "").lower()
    return "text/html" in accept or sec_fetch_mode == "navigate" or sec_fetch_dest == "document"


def _frontend_oauth_return_url(request: Request | None = None) -> str:
    """Resolve where browser OAuth callbacks should return users.

    Priority:
    1) WEB_FRONTEND_RETURN_URL (full URL, can include query params)
    2) SAAS_FRONTEND_URL / WEB_PUBLIC_ORIGIN (+ local connect route)
    3) Local relative connect route fallback.
    """
    explicit = (os.getenv("WEB_FRONTEND_RETURN_URL") or "").strip()
    if explicit.startswith(("http://", "https://")):
        return explicit

    frontend_origin = (
        (os.getenv("SAAS_FRONTEND_URL") or "").strip() or (os.getenv("WEB_PUBLIC_ORIGIN") or "").strip()
    ).rstrip("/")
    if frontend_origin.startswith(("http://", "https://")):
        return f"{frontend_origin}/?section=connect"

    if request is not None:
        origin = _request_origin(request).rstrip("/")
        if origin.startswith(("http://", "https://")):
            return f"{origin}/?section=connect"
    return "/?section=connect"


def _append_query(url: str, query: str) -> str:
    query = (query or "").strip().lstrip("?")
    if not query:
        return url
    return f"{url}{'&' if '?' in url else '?'}{query}"


def _new_local_oauth_state(kind: str) -> str:
    now = int(time.time())
    token = secrets.token_urlsafe(32)
    with _local_oauth_state_lock:
        for key, payload in list(_local_oauth_states.items()):
            exp = int(payload.get("exp") or 0)
            if exp < now:
                _local_oauth_states.pop(key, None)
        _local_oauth_states[token] = {"k": str(kind or ""), "exp": now + _LOCAL_OAUTH_STATE_TTL_SEC}
    return token


def _peek_local_oauth_state(token: str) -> str | None:
    """Return OAuth kind for ``token`` without consuming it (root callback dispatch)."""
    if not token:
        return None
    now = int(time.time())
    with _local_oauth_state_lock:
        payload = _local_oauth_states.get(token)
    if not isinstance(payload, dict):
        return None
    exp = int(payload.get("exp") or 0)
    kind = str(payload.get("k") or "").strip().lower()
    if not kind or exp < now:
        return None
    return kind


def _consume_local_oauth_state(token: str) -> str | None:
    if not token:
        return None
    now = int(time.time())
    with _local_oauth_state_lock:
        payload = _local_oauth_states.pop(token, None)
    if not isinstance(payload, dict):
        return None
    exp = int(payload.get("exp") or 0)
    kind = str(payload.get("k") or "").strip().lower()
    if not kind or exp < now:
        return None
    return kind


def _preflight_sizing_context(trade: PendingTrade, signal: dict[str, Any]) -> dict[str, Any]:
    """Sizing rationale + portfolio exposure hints for the approve dialog."""
    ticker = str(trade.ticker or "").strip().upper()
    price = float(trade.price or 0)
    qty = int(trade.qty or 0)
    sector_etf = str(signal.get("sector_etf") or "").strip().upper()
    out: dict[str, Any] = {
        "position_sizing_method": "fixed_usd",
        "position_sizing_rationale": "Default fixed dollar size (POSITION_SIZE_USD).",
        "recommended_notional_usd": None,
        "portfolio_sector_etf": sector_etf,
        "portfolio_sector_weight_pct": None,
        "portfolio_sector_weight_after_pct": None,
        "portfolio_total_value_usd": None,
    }
    try:
        from config import get_volatility_sizing_enabled

        recommended = int(
            get_position_size_usd(
                ticker=ticker or None,
                price=price if price > 0 else None,
                skill_dir=SKILL_DIR,
            )
        )
        out["recommended_notional_usd"] = recommended
        if get_volatility_sizing_enabled(SKILL_DIR) and ticker and price > 0:
            out["position_sizing_method"] = "volatility_atr"
            out["position_sizing_rationale"] = (
                f"Volatility-adjusted size targets ~${recommended:,} notional (ATR-based risk budget, not flat $5k)."
            )
        else:
            out["position_sizing_rationale"] = (
                f"Fixed position budget ~${recommended:,} per trade "
                f"(set POSITION_SIZE_USD or enable VOLATILITY_SIZING_ENABLED)."
            )
    except Exception as exc:
        LOG.debug("Preflight sizing context skipped: %s", exc)

    try:
        auth = get_shared_auth()
        account = get_account_status(auth=auth, skill_dir=SKILL_DIR)
        summary = _build_portfolio_summary(account if isinstance(account, dict) else {})
        total_value = float(summary.get("total_value") or 0)
        out["portfolio_total_value_usd"] = round(total_value, 2) if total_value > 0 else None
        positions = summary.get("positions_weighted") if isinstance(summary.get("positions_weighted"), list) else []
        sector_etf = out["portfolio_sector_etf"]
        if sector_etf and total_value > 0 and positions:
            sector_mkt = sum(
                float(p.get("market_value") or 0)
                for p in positions
                if str(p.get("sector_etf") or "").strip().upper() == sector_etf
            )
            current = round((sector_mkt / total_value) * 100.0, 2)
            out["portfolio_sector_weight_pct"] = current
            if price > 0 and qty > 0:
                add_pct = round(((price * qty) / total_value) * 100.0, 2)
                out["portfolio_sector_weight_after_pct"] = round(current + add_pct, 2)
    except Exception as exc:
        LOG.debug("Preflight portfolio context skipped: %s", exc)
    return out


def _build_pretrade_checklist(trade: PendingTrade, signal: dict[str, Any]) -> dict[str, Any]:
    env = _read_json_file(EXECUTION_METRICS_PATH, {"days": {}})
    days = env.get("days", {}) if isinstance(env, dict) else {}
    today = datetime.now(timezone.utc).date().isoformat()
    todays_events = ((days.get(today) or {}).get("events") or {}) if isinstance(days, dict) else {}
    live_trades_today = int(todays_events.get("action_live", 0) or 0)
    shadow_trades_today = int(todays_events.get("action_shadow", 0) or 0)

    max_trades = int(os.getenv("MAX_TRADES_PER_DAY", "20") or 20)
    max_total_account = float(os.getenv("MAX_TOTAL_ACCOUNT_VALUE", "500000") or 500000)
    est_value = float((trade.price or 0) * (trade.qty or 0))
    est_risk_pct = (
        round((est_value / max_total_account) * 100.0, 2) if max_total_account > 0 and est_value > 0 else None
    )
    event_risk = signal.get("event_risk") if isinstance(signal, dict) else {}
    regime = signal.get("regime_v2") if isinstance(signal, dict) else {}
    blocked = []
    if live_trades_today >= max_trades:
        blocked.append("max_daily_trades_reached")
    if (
        isinstance(event_risk, dict)
        and event_risk.get("mode") == "live"
        and event_risk.get("flagged")
        and event_risk.get("action") == "block"
    ):
        blocked.append("event_risk_block")
    if isinstance(regime, dict) and str(regime.get("mode", "off")) == "live":
        score = float(regime.get("score", 100) or 100)
        gate = float(os.getenv("REGIME_V2_ENTRY_MIN_SCORE", "55") or 55)
        if score < gate:
            blocked.append("regime_v2_block")

    return with_plain_language(
        {
            "risk_percent_estimate": est_risk_pct,
            "max_daily_trades": max_trades,
            "live_trades_today": live_trades_today,
            "shadow_trades_today": shadow_trades_today,
            "event_risk": event_risk if isinstance(event_risk, dict) else {},
            "regime_status": regime if isinstance(regime, dict) else {},
            "blocked": bool(blocked),
            "block_reasons": blocked,
            "requires_explicit_approval": True,
            **_preflight_sizing_context(trade, signal if isinstance(signal, dict) else {}),
        }
    )


def _build_portfolio_summary(account_status: dict[str, Any]) -> dict[str, Any]:
    return _shared_build_portfolio_summary(account_status)


def _build_portfolio_risk_analytics(summary: dict[str, Any], *, skill_dir: Path) -> dict[str, Any]:
    return _shared_build_portfolio_risk_analytics(summary, skill_dir=skill_dir)


def _scan_snapshot() -> dict[str, Any]:
    with _scan_lock:
        _expire_stale_scan_job_locked()
        elapsed_seconds: int | None = None
        started_at = _scan_job.get("started_at")
        if isinstance(started_at, str):
            try:
                started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                elapsed_seconds = max(0, int((datetime.now(timezone.utc) - started_dt).total_seconds()))
            except Exception:
                elapsed_seconds = None
        return {
            "job_id": _scan_job.get("job_id"),
            "status": _scan_job.get("status"),
            "started_at": started_at,
            "finished_at": _scan_job.get("finished_at"),
            "elapsed_seconds": elapsed_seconds,
            "signals_found": _scan_job.get("signals_found"),
            "diagnostics": _scan_job.get("diagnostics"),
            "diagnostics_summary": _scan_job.get("diagnostics_summary"),
            "strategy_summary": _scan_job.get("strategy_summary"),
            "signals": _scan_job.get("signals") or [],
            "shortlist_signals": _scan_job.get("shortlist_signals") or [],
            "error": _scan_job.get("error"),
        }


def _bind_operator_desk_scan_status() -> None:
    """Let the in-process desk read the scan job without looping back over HTTPS."""
    from operator_desk import set_local_scan_status

    set_local_scan_status(_scan_snapshot)


_bind_operator_desk_scan_status()


def _resolve_local_scan_signal(db: Session, symbol: str) -> dict[str, Any] | None:
    """Find a scan-row payload for Evaluate setup / decision-card.

    Order: in-memory job (kept, then shortlist) → persisted last_scan → ScanResult.
    Hydrated Today-tab rows come from last_scan after a restart, so looking only
    at ``_scan_job`` leaves the brief dropdowns empty.
    """
    from .decision_card_service import coerce_scan_signal, find_signal_in_scan_collections

    needle = str(symbol or "").upper().strip()
    if not needle:
        return None
    with _scan_lock:
        job_signals = list(_scan_job.get("signals") or [])
        job_shortlist = list(_scan_job.get("shortlist_signals") or [])
    found = find_signal_in_scan_collections(needle, job_signals, job_shortlist)
    if found:
        return found
    last_scan = _load_state(db, "last_scan", {})
    if isinstance(last_scan, dict):
        found = find_signal_in_scan_collections(
            needle,
            last_scan.get("signals"),
            last_scan.get("shortlist_signals"),
        )
        if found:
            return found
    row = (
        db.query(ScanResult)
        .filter(ScanResult.user_id == LOCAL_DASHBOARD_USER_ID, ScanResult.ticker == needle)
        .order_by(ScanResult.created_at.desc())
        .first()
    )
    if row is None:
        return None
    return coerce_scan_signal(row.payload_json)


def _parse_iso_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _bind_scan_worker(worker: threading.Thread) -> None:
    global _scan_worker_thread
    _scan_worker_thread = worker


def _scan_worker_abandoned_locked() -> bool:
    """True after the scan thread has started and exited without clearing the job."""
    worker = _scan_worker_thread
    if worker is None or worker.ident is None:
        return False
    return not worker.is_alive()


def _expire_stale_scan_job_locked() -> bool:
    """Fail a scan that can no longer finish.

    A live worker stays ``running`` until ``WEB_SCAN_STALE_SECONDS``. Marking it
    failed earlier lets the page stop polling while enrichment is still going,
    and it also lets a second scan start on top of the first. A thread that
    already exited is failed immediately so the job cannot sit on ``running``.
    """
    if str(_scan_job.get("status") or "") != "running":
        return False
    started_dt = _parse_iso_datetime(_scan_job.get("started_at"))
    if started_dt is None:
        return False
    elapsed = (datetime.now(timezone.utc) - started_dt).total_seconds()
    abandoned = _scan_worker_abandoned_locked()
    if not abandoned and elapsed <= float(_SCAN_STALE_SECONDS):
        return False
    if abandoned:
        error = "Scan worker exited without a result."
    else:
        error = f"Scan watchdog timeout: exceeded {_SCAN_STALE_SECONDS}s runtime ({int(elapsed)}s elapsed)."
    _scan_job.update(
        {
            "status": "failed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }
    )
    return True


def _scan_lifecycle_payload(
    snapshot: dict[str, Any],
    last_scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = str(snapshot.get("status") or "idle")
    out: dict[str, Any] = {
        "mode": "local",
        "transport": "local_thread",
        "job_id": snapshot.get("job_id"),
        "task_id": None,
        "status": status,
        "phase": "idle",
        "started_at": snapshot.get("started_at"),
        "finished_at": snapshot.get("finished_at"),
        "elapsed_seconds": snapshot.get("elapsed_seconds"),
        "signals_found": snapshot.get("signals_found"),
        "scan_id": None,
        "worker_queue": None,
    }
    if status == "running":
        out["phase"] = "running"
    elif status == "completed":
        out["phase"] = "completed"
    elif status == "failed":
        out["phase"] = "failed"
    elif status == "idle":
        out["phase"] = "idle"
    if status in {"completed", "failed"}:
        diag = snapshot.get("diagnostics") or {}
        if isinstance(diag, dict):
            out["scan_id"] = diag.get("scan_id")
    if status == "idle" and isinstance(last_scan, dict):
        out["last_scan"] = last_scan
        out["signals_found"] = last_scan.get("signals_found")
        diag = last_scan.get("diagnostics") or {}
        if isinstance(diag, dict):
            out["scan_id"] = diag.get("scan_id")
    if status == "failed":
        out["error"] = snapshot.get("error")
    if status in {"completed", "failed"}:
        out["diagnostics"] = snapshot.get("diagnostics")
        out["diagnostics_summary"] = snapshot.get("diagnostics_summary")
        out["strategy_summary"] = snapshot.get("strategy_summary")
        out["signals"] = snapshot.get("signals") or []
        out["shortlist_signals"] = snapshot.get("shortlist_signals") or []
    # Preflight hits disk/config. Skip it while a scan is running so 5s
    # status polls do not compete with Stage A on the single uvicorn thread.
    if status != "running":
        out["signal_edge_preflight"] = _signal_edge_scan_preflight(SKILL_DIR)
    return out


def _decision_dashboard_snapshot(db: Session) -> dict[str, Any]:
    last_scan = _load_state(
        db,
        key="last_scan",
        default={
            "at": None,
            "signals_found": None,
            "signals": [],
            "diagnostics": None,
            "diagnostics_summary": None,
            "strategy_summary": None,
        },
    )
    return build_decision_dashboard_snapshot(skill_dir=SKILL_DIR, last_scan=last_scan)


def _strategy_summary(signals: list[dict[str, Any]] | None) -> dict[str, Any]:
    return summarize_live_strategy(signals)


_SIGNAL_EDGE_SHADOW_BLOCKER_KEYS = frozenset(
    {
        "rank_filter_would_drop_composite",
        "rank_filter_would_drop_rank_v2",
        "rank_filter_would_drop_signal",
        "rank_filter_would_drop_any",
        "stage2_shadow_would_filter",
        "entry_shadow_would_filter_sma50_low",
        "entry_shadow_would_filter_sma50_high",
        "entry_shadow_would_filter_breakout_buffer",
        "entry_shadow_would_filter_any",
    }
)

_DIAGNOSTIC_FLAG_KEYS = frozenset(
    {
        "regime_fail_closed_mode",
        "scan_allow_bear_regime",
        "regime_bullish",
        "regime_data_unavailable",
    }
)

# Timing / provenance counters must never rank as "top blockers".
_DIAGNOSTIC_NON_BLOCKER_RE = re.compile(
    r"(_ms$|^spy_|provider_|advisory_|watchlist|shadow|mode$|would_|"
    r"headline|scan_id|scan_blocked|data_provider_)",
    re.IGNORECASE,
)
_DIAGNOSTIC_DROPOFF_RE = re.compile(
    r"(^stage2_fail$|^vcp_fail$|not_confirmed|not_winning|no_sector|too_few|"
    r"df_empty|exceptions|timeouts|_filtered$|_pruned$|insufficient|no_price|stale)",
    re.IGNORECASE,
)
# Ordinary funnel counts. They belong on the scan funnel, not the P2 banner.
_FUNNEL_COUNT_KEYS = frozenset(
    {
        "stage2_fail",
        "vcp_fail",
        "breakout_not_confirmed",
        "pead_primary_fail",
        "quality_gates_filtered",
        "pts_52w_cap_blocked",
        "entry_timing_blocked",
    }
)


def _is_actionable_scan_blocker_key(key: str) -> bool:
    k = str(key or "").strip()
    if not k or k in _SIGNAL_EDGE_SHADOW_BLOCKER_KEYS or k in _DIAGNOSTIC_FLAG_KEYS:
        return False
    if _DIAGNOSTIC_NON_BLOCKER_RE.search(k):
        return False
    if k in _FUNNEL_COUNT_KEYS:
        return False
    return bool(_DIAGNOSTIC_DROPOFF_RE.search(k))


def _diagnostics_summary(diag: dict[str, Any] | None, signals: list[dict[str, Any]] | None) -> dict[str, Any]:
    diagnostics = diag or {}
    blocked_reason_raw = diagnostics.get("scan_blocked_reason")
    blocked_reason = str(blocked_reason_raw).strip() if blocked_reason_raw else None
    blocked_human = None
    if blocked_reason == "bear_regime_spy_below_200sma":
        spy_px = diagnostics.get("spy_price")
        spy_sma = diagnostics.get("spy_sma_200")
        if spy_px is not None and spy_sma is not None:
            blocked_human = f"Scan blocked by regime gate: SPY ${spy_px} below 200 SMA ${spy_sma}."
        else:
            blocked_human = "Scan blocked by regime gate: SPY is below 200 SMA."
    elif blocked_reason == "regime_check_failed_data_unavailable":
        bars = diagnostics.get("regime_history_bars")
        provider = diagnostics.get("regime_history_provider")
        blocked_human = (
            "Scan blocked: SPY regime data unavailable "
            f"(bars={bars if bars is not None else '?'}, provider={provider or '?'}). "
            "Not a confirmed bear market — check market data auth."
        )

    ranked: list[dict[str, Any]] = []
    for key, raw in diagnostics.items():
        try:
            value = int(raw)
        except Exception:
            continue
        if (
            value <= 0
            or key == "watchlist_size"
            or key in _SIGNAL_EDGE_SHADOW_BLOCKER_KEYS
            or key in _DIAGNOSTIC_FLAG_KEYS
            or not _is_actionable_scan_blocker_key(str(key))
        ):
            continue
        ranked.append(
            {
                "key": key,
                "value": value,
                "severity": "error" if key in {"exceptions", "df_empty", "stage_a_timeouts"} else "warn",
            }
        )
    ranked.sort(key=lambda x: int(x.get("value") or 0), reverse=True)
    final_count = len(signals or [])
    funnel = _build_funnel_stages(diagnostics, final_count)
    return {
        "scan_blocked": bool(diagnostics.get("scan_blocked")),
        "scan_blocked_reason": blocked_reason,
        "headline": blocked_human or (str(diagnostics.get("coverage_note") or "").strip() or None),
        "top_blockers": ranked[:5],
        "data_quality": diagnostics.get("data_quality"),
        "data_quality_reasons": list(diagnostics.get("data_quality_reasons") or []),
        "signal_edge_shadow": _signal_edge_shadow_summary(diagnostics),
        "funnel": funnel,
    }


def _safe_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except Exception:
        return default


def _build_funnel_stages(diagnostics: dict[str, Any], final_count: int) -> dict[str, Any]:
    """Build a richer pass-funnel that exposes Stage B drop-off.

    Returns a dict with both legacy keys (``watchlist``, ``stage2_pass``,
    ``vcp_pass``, ``final``) for backward-compat, and a ``stages`` array of
    structured nodes for the dashboard funnel chart.

    Each stage carries:
      - ``key``: stable identifier
      - ``label``: short display label
      - ``value``: pass count *at* this stage
      - ``filtered``: count removed since the previous stage (best-effort)
      - ``mode``: optional gate mode (``hard`` / ``shadow`` / ``soft`` / ``off``)
      - ``shadow_filtered``: count that *would* have been filtered in hard mode
      - ``tooltip``: human-readable explanation
    """

    watchlist = _safe_int(diagnostics.get("watchlist_size"))
    stage2_fail = _safe_int(diagnostics.get("stage2_fail"))
    vcp_fail = _safe_int(diagnostics.get("vcp_fail"))
    no_sector_etf = _safe_int(diagnostics.get("no_sector_etf"))
    sector_not_winning = _safe_int(diagnostics.get("sector_not_winning"))
    breakout_not_confirmed = _safe_int(diagnostics.get("breakout_not_confirmed"))

    stage_a_candidates_raw = _safe_int(diagnostics.get("stage_a_candidates"))
    stage_a_shortlisted_raw = _safe_int(diagnostics.get("stage_a_shortlisted"))
    stage_a_pruned = _safe_int(diagnostics.get("stage_a_pruned"))

    primary_provider_filtered = _safe_int(diagnostics.get("primary_provider_filtered"))
    stage_b_exceptions = _safe_int(diagnostics.get("stage_b_exceptions"))
    stage_b_timeouts = _safe_int(diagnostics.get("stage_b_timeouts"))
    self_study_filtered = _safe_int(diagnostics.get("self_study_filtered"))
    self_study_would_filter = _safe_int(diagnostics.get("self_study_would_filter"))
    self_study_would_filter_cached = _safe_int(diagnostics.get("self_study_would_filter_cached"))
    quality_gates_filtered = _safe_int(diagnostics.get("quality_gates_filtered"))

    vcp_would_filter = _safe_int(diagnostics.get("stage_a_vcp_would_filter"))
    sector_would_filter = _safe_int(diagnostics.get("stage_a_sector_would_filter"))
    no_sector_would_filter = _safe_int(diagnostics.get("stage_a_no_sector_would_filter"))

    vcp_gate_mode = str(diagnostics.get("scan_vcp_gate_mode") or "").strip().lower() or None
    sector_gate_mode = str(diagnostics.get("scan_sector_gate_mode") or "").strip().lower() or None
    primary_provider_mode = str(diagnostics.get("scan_primary_provider_mode") or "").strip().lower() or None
    quality_gates_mode = str(diagnostics.get("quality_gates_mode") or "").strip().lower() or None
    self_study_mode = str(diagnostics.get("self_study_mode") or "").strip().lower() or None
    signal_edge_shadow_mode = str(diagnostics.get("signal_edge_shadow_mode") or "").strip().lower() or None
    stage2_shadow_would_filter = _safe_int(diagnostics.get("stage2_shadow_would_filter"))
    rank_filter_would_drop_any = _safe_int(diagnostics.get("rank_filter_would_drop_any"))

    from core.stage_a_coverage import stage_a_funnel_counts

    funnel_counts = stage_a_funnel_counts(diagnostics)
    n_watchlist = watchlist
    n_evaluated = funnel_counts["evaluated"]
    n_unresolved = funnel_counts["unresolved"]
    n_stage2 = funnel_counts["stage2_pass"]
    n_vcp = max(0, n_stage2 - vcp_fail)
    sector_filtered = no_sector_etf + sector_not_winning
    n_sector = max(0, n_vcp - sector_filtered)
    # Timeouts are already excluded from n_stage2. Subtracting the global
    # exceptions counter again would hide names the executable pass finished.
    stage_a_task_exceptions = _safe_int(diagnostics.get("stage_a_task_exceptions"))
    n_breakout = max(0, n_sector - breakout_not_confirmed - stage_a_task_exceptions)

    # ``stage_a_candidates`` is the authoritative count of tickers that
    # passed every Stage A gate. Fall back to the chain-derived estimate
    # only when the counter is missing.
    n_stage_a = stage_a_candidates_raw if stage_a_candidates_raw > 0 else n_breakout
    n_after_provider = max(0, n_stage_a - primary_provider_filtered)
    n_shortlist = stage_a_shortlisted_raw if stage_a_shortlisted_raw > 0 else max(0, n_after_provider - stage_a_pruned)
    quality_filtered_total = stage_b_exceptions + stage_b_timeouts + self_study_filtered + quality_gates_filtered
    n_quality = max(0, n_shortlist - quality_filtered_total)
    # Anything remaining after quality gates that did not make the final
    # ranked list was trimmed by the top-N cap.
    top_n_trimmed = max(0, n_quality - final_count)

    stages: list[dict[str, Any]] = [
        {
            "key": "watchlist",
            "label": "Watchlist",
            "value": n_watchlist,
            "filtered": 0,
            "tooltip": "Total tickers in the scan universe (e.g. SP1500 or your custom list).",
        },
        {
            "key": "evaluated",
            "label": "Stage A evaluated",
            "value": n_evaluated,
            "filtered": n_unresolved,
            "tooltip": (
                "Names that received a Stage A decision. Timeouts and Stage A task "
                "exceptions are not counted as Stage 2 passes."
            ),
        },
        {
            "key": "stage2",
            "label": "Passed Stage 2",
            "value": n_stage2,
            "filtered": stage2_fail,
            "shadow_filtered": stage2_shadow_would_filter if signal_edge_shadow_mode == "shadow" else 0,
            "mode": signal_edge_shadow_mode if stage2_shadow_would_filter > 0 else None,
            "tooltip": (
                "Tickers in a Stage 2 uptrend (above 30-week SMA, proper trend structure). Failures: stage2_fail. "
                "When signal-edge shadow is on, the would-filter count shows candidates that pass live Stage 2 "
                "but fail the tighter shadow thresholds (higher 52-week proximity, longer SMA uptrend)."
            ),
        },
        {
            "key": "vcp",
            "label": "Passed VCP",
            "value": n_vcp,
            "filtered": vcp_fail,
            "shadow_filtered": vcp_would_filter,
            "mode": vcp_gate_mode,
            "tooltip": (
                "Tickers showing volatility-contraction-pattern volume signature. "
                "In shadow mode the VCP gate observes but does not filter; the "
                "would-filter count shows how many it would have removed."
            ),
        },
        {
            "key": "sector",
            "label": "Sector OK",
            "value": n_sector,
            "filtered": sector_filtered,
            "shadow_filtered": sector_would_filter + no_sector_would_filter,
            "mode": sector_gate_mode,
            "tooltip": (
                "Tickers in a winning sector ETF. Filtered by no_sector_etf + "
                "sector_not_winning when the sector gate is hard."
            ),
        },
        {
            "key": "stage_a",
            "label": "Stage A Candidates",
            "value": n_stage_a,
            "filtered": max(0, n_sector - n_stage_a),
            "tooltip": (
                "Final Stage A pass count after breakout confirmation, exceptions, "
                "and any timed gates. Sourced from stage_a_candidates."
            ),
            "shadow_filtered": self_study_would_filter_cached if self_study_mode == "shadow" else 0,
            "mode": self_study_mode if self_study_would_filter_cached > 0 else None,
        },
        {
            "key": "shortlist",
            "label": "Shortlist (top-scored)",
            "value": n_shortlist,
            "filtered": max(0, n_stage_a - n_shortlist),
            "mode": primary_provider_mode,
            "tooltip": (
                "Top-scored Stage A candidates picked for expensive Stage B "
                "enrichment (forensic, PEAD, advisory, MiroFish). Lower-scored "
                "tickers are pruned by the shortlist cap."
            ),
        },
        {
            "key": "quality",
            "label": "Quality Gates",
            "value": n_quality,
            "filtered": quality_filtered_total,
            "mode": quality_gates_mode,
            "shadow_filtered": (
                self_study_would_filter if self_study_mode == "shadow" else 0
            ),
            "tooltip": (
                "Survivors of Stage B exceptions/timeouts, self-study min "
                "conviction, and quality gates (forensic, weak breakout volume, "
                "etc.). Self-study shadow would-filter is counted separately and "
                "does not remove names."
            ),
        },
        {
            "key": "final",
            "label": "Final Signals",
            "value": final_count,
            "filtered": top_n_trimmed,
            "tooltip": (
                "Tradeable signals returned by the scan after the top-N rank cap. "
                "If this is much smaller than Quality Gates, the cap (TOP_N) is "
                "trimming output."
            ),
        },
    ]

    if signal_edge_shadow_mode == "shadow" and rank_filter_would_drop_any > 0:
        n_after_rank_shadow = max(0, final_count - rank_filter_would_drop_any)
        stages.append(
            {
                "key": "rank_filter_shadow",
                "label": "After rank-filter shadow",
                "value": n_after_rank_shadow,
                "filtered": rank_filter_would_drop_any,
                "shadow_filtered": rank_filter_would_drop_any,
                "mode": "shadow",
                "tooltip": (
                    "Post-scan shadow rank filter (composite p50, rank_v2 p70, signal p70). "
                    "Would drop signals below batch quantile thresholds. Does not remove live signals."
                ),
            }
        )

    return {
        # Legacy keys preserved for any external consumer that still reads them.
        "watchlist": n_watchlist,
        "stage2_pass": n_stage2,
        "vcp_pass": n_vcp,
        "final": final_count,
        # New, richer payload consumed by the dashboard funnel chart.
        "stages": stages,
        "vcp_gate_mode": vcp_gate_mode,
        "sector_gate_mode": sector_gate_mode,
        "primary_provider_mode": primary_provider_mode,
        "quality_gates_mode": quality_gates_mode,
        "self_study_mode": self_study_mode,
    }


def _build_report_verdicts(report: dict[str, Any]) -> dict[str, Any]:
    technical = report.get("technical") or {}
    dcf = report.get("dcf") or {}
    health = report.get("health") or {}
    miro = report.get("mirofish") or {}
    signal_score = float(technical.get("signal_score", 0) or 0)
    mos = float(dcf.get("margin_of_safety", 0) or 0)
    health_flags = health.get("flags") or []
    conviction = float(miro.get("conviction_score", 0) or 0)

    def bucket(score: float, high: float, low: float) -> str:
        if score >= high:
            return "bullish"
        if score <= low:
            return "bearish"
        return "neutral"

    return {
        "technical": {
            "verdict": bucket(signal_score, 65.0, 45.0),
            "takeaway": "Trend setup aligned."
            if technical.get("stage_2") and technical.get("vcp")
            else "Setup quality is mixed.",
        },
        "dcf": {
            "verdict": bucket(mos, 10.0, -10.0),
            "takeaway": "Valuation supports upside." if mos >= 0 else "Valuation indicates premium pricing.",
        },
        "health": {
            "verdict": "bullish" if len(health_flags) == 0 else ("bearish" if len(health_flags) >= 3 else "neutral"),
            "takeaway": "Balance sheet and margins are stable."
            if len(health_flags) == 0
            else "Review flagged financial risks.",
        },
        "mirofish": {
            "verdict": bucket(conviction, 30.0, -30.0),
            "takeaway": (miro.get("summary") or "No sentiment synthesis available.")[:220],
        },
    }


def _sec_analysis_settings() -> dict[str, Any]:
    from config import (
        get_edgar_user_agent,
        get_sec_filing_analysis_enabled,
        get_sec_filing_cache_hours,
        get_sec_filing_compare_enabled,
        get_sec_filing_llm_summary_enabled,
        get_sec_filing_max_chars,
        get_sec_filing_max_compare_items,
    )

    return {
        "analysis_enabled": bool(get_sec_filing_analysis_enabled(SKILL_DIR)),
        "compare_enabled": bool(get_sec_filing_compare_enabled(SKILL_DIR)),
        "user_agent": get_edgar_user_agent(SKILL_DIR),
        "cache_hours": float(get_sec_filing_cache_hours(SKILL_DIR)),
        "max_chars": int(get_sec_filing_max_chars(SKILL_DIR)),
        "max_compare_items": int(get_sec_filing_max_compare_items(SKILL_DIR)),
        "llm_enabled": bool(get_sec_filing_llm_summary_enabled(SKILL_DIR)),
    }


# Retention window for the local ScanResult table — keeps `flagged_days` queries cheap
# and stops the SQLite file from growing unbounded across repeated scans.
_LOCAL_SCAN_RESULT_RETENTION_DAYS = max(7, int(os.getenv("WEB_LOCAL_SCAN_RESULT_RETENTION_DAYS", "30") or 30))
_LOCAL_FLAGGED_DAYS_LOOKBACK = max(1, int(os.getenv("WEB_LOCAL_FLAGGED_DAYS_LOOKBACK", "30") or 30))


def _scan_result_signal_score(signal: dict[str, Any]) -> float | None:
    raw = signal.get("composite_score")
    if raw is None:
        raw = signal.get("signal_score")
    if raw is None:
        raw = signal.get("score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _persist_scan_results_local(
    db: Session,
    job_id: str,
    signals: list[dict[str, Any]] | None,
) -> int:
    """Mirror the SaaS path: insert one ScanResult row per signal for the local user.

    These rows are what `_enrich_signals_with_flagged_days` then groups by ticker to
    derive the per-row "Days Flagged" count shown in the dashboard.
    """
    rows = signals or []
    if not rows:
        return 0
    inserted = 0
    for sig in rows:
        ticker = str(sig.get("ticker") or sig.get("symbol") or "").strip().upper()
        if not ticker:
            continue
        try:
            payload_obj = json.loads(json.dumps(sig, default=_json_default))
        except (TypeError, ValueError):
            payload_obj = {"ticker": ticker}
        db.add(
            ScanResult(
                user_id=LOCAL_DASHBOARD_USER_ID,
                job_id=job_id,
                ticker=ticker,
                signal_score=_scan_result_signal_score(sig),
                payload_json=payload_obj,
            )
        )
        inserted += 1
    if inserted:
        try:
            db.commit()
        except Exception as exc:
            LOG.warning("Local scan_results commit failed: %s", exc)
            db.rollback()
            return 0
    return inserted


def _enrich_signals_with_flagged_days(
    db: Session,
    signals: list[dict[str, Any]] | None,
    *,
    lookback_days: int = _LOCAL_FLAGGED_DAYS_LOOKBACK,
) -> list[dict[str, Any]]:
    """Attach `flagged_days` to each signal in-place using the local ScanResult table.

    Mirrors the SaaS query: for each ticker, count distinct UTC dates where the local
    user produced a ScanResult within the lookback window. The dashboard reads
    `signal.flagged_days` via the same fallback chain it already uses for SaaS payloads.
    """
    from datetime import timedelta

    rows = signals or []
    if not rows:
        return rows
    tickers = sorted(
        {
            str(sig.get("ticker") or sig.get("symbol") or "").strip().upper()
            for sig in rows
            if str(sig.get("ticker") or sig.get("symbol") or "").strip()
        }
    )
    if not tickers:
        return rows
    flagged_days_map: dict[str, int] = {}
    try:
        # `func.date(...)` collapses timestamps to UTC calendar days on both SQLite and
        # Postgres; counting distinct values gives the "days flagged" metric.
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(lookback_days)))
        day_counts = (
            db.query(
                ScanResult.ticker,
                func.count(func.distinct(func.date(ScanResult.created_at))),
            )
            .filter(
                ScanResult.user_id == LOCAL_DASHBOARD_USER_ID,
                ScanResult.ticker.in_(tickers),
                ScanResult.created_at >= cutoff,
            )
            .group_by(ScanResult.ticker)
            .all()
        )
        flagged_days_map = {
            str(ticker or "").upper(): int(days or 0) for ticker, days in day_counts if str(ticker or "").strip()
        }
    except Exception as exc:
        LOG.debug("flagged_days enrichment skipped: %s", exc)
        return rows
    for sig in rows:
        ticker = str(sig.get("ticker") or sig.get("symbol") or "").strip().upper()
        if not ticker:
            continue
        sig["flagged_days"] = int(flagged_days_map.get(ticker, 0))
    return rows


def _prune_local_scan_results(
    db: Session,
    *,
    retention_days: int = _LOCAL_SCAN_RESULT_RETENTION_DAYS,
) -> None:
    """Drop ScanResult rows older than `retention_days` for the local user."""
    from datetime import timedelta

    if retention_days <= 0:
        return
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(retention_days))
        (
            db.query(ScanResult)
            .filter(
                ScanResult.user_id == LOCAL_DASHBOARD_USER_ID,
                ScanResult.created_at < cutoff,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
    except Exception as exc:
        LOG.debug("Local scan_results prune skipped: %s", exc)
        db.rollback()


def _record_scan_outcomes(signals: list[dict[str, Any]] | None, diagnostics: Any) -> None:
    """Snapshot kept signals and label matured packets. Never fails the scan."""
    try:
        from core.outcome_backfill import run_scan_outcome_pass

        run_scan_outcome_pass(
            SKILL_DIR,
            signals,
            diagnostics=diagnostics if isinstance(diagnostics, dict) else None,
        )
    except Exception as exc:
        LOG.warning("Scan outcome pass failed: %s", exc)


def _scan_worker(job_id: str, scan_kwargs: dict[str, Any] | None = None) -> None:
    try:
        _sse_publish("scan_started", {"job_id": job_id})
        skw = scan_kwargs or {}
        scan_out = run_scan(skill_dir=SKILL_DIR, **skw)
        signals = scan_out.signals
        diagnostics = scan_out.diagnostics
        shortlist_signals = scan_out.shortlist_signals
        diagnostics_summary = _diagnostics_summary(diagnostics, signals)
        strategy_summary = _strategy_summary(signals)
        finished_at = datetime.now(timezone.utc).isoformat()
        db = SessionLocal()
        try:
            _persist_scan_results_local(db, job_id, signals)
            _enrich_signals_with_flagged_days(db, signals)
            _enrich_signals_with_flagged_days(db, shortlist_signals)
            _prune_local_scan_results(db)
            signals_persist = signals[:_LAST_SCAN_SIGNALS_CAP]
            shortlist_persist = shortlist_signals[:_LAST_SCAN_SIGNALS_CAP]
            last_scan = {
                "at": finished_at,
                "signals_found": len(signals),
                "signals": signals_persist,
                "shortlist_signals": shortlist_persist,
                "diagnostics": diagnostics,
                "diagnostics_summary": diagnostics_summary,
                "strategy_summary": strategy_summary,
            }
            with _scan_lock:
                still_current = _scan_job.get("job_id") == job_id
            if still_current:
                _save_last_scan(db, last_scan)
            else:
                LOG.info("Scan %s finished after a newer job started; result not saved", job_id)
            try:
                from config import get_entry_timing_experiment_readiness
                from core.entry_timing_live_compare import write_live_entry_shadow_compare_report

                write_live_entry_shadow_compare_report(
                    diagnostics,
                    skill_dir=SKILL_DIR,
                    live_meta={
                        "source": "post_scan",
                        "scan_at": finished_at,
                        "signals_found": len(signals),
                        "watchlist_size": diagnostics.get("watchlist_size"),
                    },
                    experiment_env_ready=bool(get_entry_timing_experiment_readiness(SKILL_DIR).get("ready")),
                )
            except Exception as exc:
                LOG.debug("Entry timing live compare artifact skipped: %s", exc)
            _harvest_experiment_scan(diagnostics, signals, finished_at)
        finally:
            db.close()
        published = False
        with _scan_lock:
            if _scan_job.get("job_id") == job_id:
                _scan_job.update(
                    {
                        "status": "completed",
                        "finished_at": finished_at,
                        "signals_found": len(signals),
                        "diagnostics": diagnostics,
                        "diagnostics_summary": diagnostics_summary,
                        "strategy_summary": strategy_summary,
                        "signals": signals,
                        "shortlist_signals": shortlist_signals,
                        "error": None,
                    }
                )
                published = True
        if published:
            _sse_publish(
                "scan_completed",
                {
                    "job_id": job_id,
                    "signals_found": len(signals),
                    "diagnostics_summary": diagnostics_summary,
                    "strategy_summary": strategy_summary,
                },
            )
            _record_scan_outcomes(signals, diagnostics)
    except Exception as e:
        with _scan_lock:
            if _scan_job.get("job_id") == job_id:
                _scan_job.update(
                    {
                        "status": "failed",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "error": str(e),
                    }
                )
        _sse_publish("scan_failed", {"job_id": job_id, "error": str(e)})
        _record_endpoint_error("scan_worker")


def _safe_telemetry_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(out)


def _safe_telemetry_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _build_standard_telemetry(signal: dict[str, Any]) -> dict[str, Any]:
    advisory = signal.get("advisory") if isinstance(signal.get("advisory"), dict) else {}
    meta_policy = signal.get("meta_policy") if isinstance(signal.get("meta_policy"), dict) else {}
    score_components = signal.get("score_components") if isinstance(signal.get("score_components"), dict) else {}
    return {
        "mirofish_conviction": _safe_telemetry_float(signal.get("mirofish_conviction")),
        "advisory_prob": _safe_telemetry_float(advisory.get("p_up_10d")),
        "agent_uncertainty": _safe_telemetry_float(meta_policy.get("uncertainty_score")),
        "vcp_volume_ratio": _safe_telemetry_float(score_components.get("avg_vcp_volume_ratio")),
        "sector_rs_rank": _safe_telemetry_int(
            signal.get("sector_rs_rank", signal.get("sector_relative_strength_rank"))
        ),
    }


def _coerce_json_dict(raw: Any, default: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = default or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else fallback
        except Exception:
            return fallback
    return fallback


def _load_state(db: Session, key: str, default: dict[str, Any]) -> dict[str, Any]:
    row = db.query(AppState).filter(AppState.user_id == LOCAL_DASHBOARD_USER_ID, AppState.key == key).first()
    if not row:
        return default
    try:
        raw = row.value_json
        data = _coerce_json_dict(raw, default=default)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def _save_state(db: Session, key: str, value: dict[str, Any]) -> None:
    row = db.query(AppState).filter(AppState.user_id == LOCAL_DASHBOARD_USER_ID, AppState.key == key).first()
    if not row:
        row = AppState(
            user_id=LOCAL_DASHBOARD_USER_ID,
            key=key,
            value_json=value,
        )
        db.add(row)
    else:
        row.value_json = value
    db.commit()


def _save_last_scan(db: Session, last_scan: dict[str, Any]) -> None:
    """Persist the new scan and roll the previous one into ``prev_scan`` so the
    cockpit can compute "what changed since last cycle" deltas."""
    try:
        existing = _load_state(db, "last_scan", {})
        if isinstance(existing, dict) and existing.get("signals"):
            _save_state(db, "prev_scan", existing)
    except Exception:
        pass
    _save_state(db, "last_scan", last_scan)


def _harvest_experiment_scan(
    diagnostics: dict[str, Any],
    signals: list[dict[str, Any]],
    scan_at: str,
) -> None:
    try:
        from core.experiment_collector import harvest_scan

        harvest_scan(
            SKILL_DIR,
            diagnostics,
            signals=signals,
            signals_found=len(signals),
            scan_at=scan_at,
            persist_last_scan=False,
            scan_label="dashboard",
        )
    except Exception as exc:
        LOG.warning("Experiment harvest after dashboard scan failed: %s", exc)


def _audit_event(
    event: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": actor,
        "payload": payload or {},
    }
    try:
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=_json_default) + "\n")
    except Exception as e:
        LOG.warning("Audit write failed: %s", e)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _request_is_loopback(request: Request) -> bool:
    host = (request.url.hostname or "").strip()
    if not host:
        host = str(request.headers.get("host") or "").split(":")[0].strip()
    if host:
        return _shared_is_loopback_host(host)
    client_host = request.client.host if request.client is not None else None
    return _shared_is_loopback_host(client_host)


def require_trade_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
) -> dict[str, str]:
    """Require a configured API key for trade-grade mutating operations."""
    configured = os.getenv("WEB_API_KEY", "").strip()
    if not configured:
        render_env = (os.getenv("RENDER") or "").strip()
        # Local/dev workflows should keep working without a server-side API key.
        # Enforce strict missing-key failures only on hosted deployments.
        if not render_env:
            return {"actor": (x_user or "unsafe-local-user").strip() or "unsafe-local-user"}
        production_like = _is_production_like()
        unsafe = (os.getenv("WEB_ALLOW_UNSAFE_LOCAL_WRITES") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        # Local/dev runs should work without WEB_API_KEY; production-like hosts still
        # require explicit opt-in (unsafe flag) or strict loopback-only origin.
        if unsafe or (not production_like) or _request_is_loopback(request):
            return {"actor": (x_user or "unsafe-local-user").strip() or "unsafe-local-user"}
        raise HTTPException(
            status_code=503,
            detail="WEB_API_KEY is required for write operations. Configure WEB_API_KEY on the server.",
        )
    if not x_api_key or x_api_key != configured:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")
    return {"actor": (x_user or "web-user").strip() or "web-user"}


def require_api_key_if_set(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
) -> dict[str, str]:
    """Backward-compatible wrapper that now enforces strict key checks."""
    return require_trade_api_key(request=request, x_api_key=x_api_key, x_user=x_user)


def require_api_key_if_set_or_query(
    api_key: str | None = None,
    x_api_key: str | None = Header(default=None),
    x_user: str | None = Header(default=None),
) -> dict[str, str]:
    """SSE-compatible auth: allow API key in query string when headers are unavailable."""
    configured = os.getenv("WEB_API_KEY", "").strip()
    if not configured:
        return {"actor": (x_user or "web-user").strip() or "web-user"}
    provided = (x_api_key or api_key or "").strip()
    if not provided or provided != configured:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    return {"actor": (x_user or "web-user").strip() or "web-user"}


@app.get("/", response_model=None)
def index(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    # Legacy local Schwab apps register https://127.0.0.1:8182/ as the callback.
    if code.strip() or error.strip() or error_description.strip() or state.strip():
        if _peek_local_oauth_state(state) == "market":
            return local_schwab_market_oauth_callback(
                request,
                code=code,
                state=state,
                error=error or error_description,
            )
        return local_schwab_oauth_callback(
            request,
            code=code,
            state=state,
            error=error or error_description,
        )
    return render_dashboard_html(STATIC_DIR / "index.html")


@app.get("/legal.html", include_in_schema=False)
def legal_page() -> RedirectResponse:
    """Bare /legal.html 404s; the page lives under the static mount."""
    return RedirectResponse("/static/legal.html", status_code=302)


@app.get("/simple")
def simple_dashboard() -> RedirectResponse:
    """Legacy focused-scan page, now a display-mode preset of the dashboard."""
    return RedirectResponse("/?display=simple", status_code=302)


@app.get("/login")
def login_page() -> RedirectResponse:
    """Legacy login path now forwards to connect-first dashboard flow."""
    return RedirectResponse(_frontend_oauth_return_url(), status_code=302)


_STARTUP_TIME = datetime.now(timezone.utc)


@app.get("/api/health", response_model=ApiResponse)
def health() -> ApiResponse:
    now = datetime.now(timezone.utc)
    uptime_seconds = int((now - _STARTUP_TIME).total_seconds())
    return _ok(
        {
            "status": "ok",
            "time": now.isoformat(),
            "uptime_seconds": uptime_seconds,
            "version": app.version,
        }
    )


@app.get("/healthz", response_class=PlainTextResponse, include_in_schema=False)
def healthz_plaintext() -> PlainTextResponse:
    """Tiny plaintext liveness probe for uptime monitors and Render health checks.

    Intentionally allocation-free and unauthenticated.
    """
    return PlainTextResponse("ok", media_type="text/plain")


@app.get("/api/events")
async def sse_events(
    _auth: dict[str, str] = Depends(require_api_key_if_set_or_query),
) -> StreamingResponse:
    """Server-Sent Events stream for real-time dashboard updates."""
    q: queue.Queue = queue.Queue(maxsize=256)
    with _sse_subscribers_lock:
        _sse_subscribers.append(q)

    async def stream():
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    payload = q.get_nowait()
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    await asyncio.sleep(1)
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            with _sse_subscribers_lock:
                try:
                    _sse_subscribers.remove(q)
                except ValueError:
                    pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _scan_studio_public_config() -> dict[str, Any]:
    from core.scan_catalog import scan_studio_public_config

    return scan_studio_public_config()


@app.get("/api/public-config", response_model=ApiResponse)
def public_config() -> ApiResponse:
    """Non-secret client config (optional Supabase browser sign-in)."""
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    anon = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    configured_api_key = (os.getenv("WEB_API_KEY") or "").strip()
    supabase: dict[str, str] | None = None
    # Local dashboard uses API-key / loopback auth. Supabase browser sign-in
    # needs SaaS session routes (/api/auth/session); keep it off unless opted in.
    if url and anon and _env_flag("WEB_ENABLE_SUPABASE_SIGNIN", default=False):
        supabase = {"url": url, "anon_key": anon}
    plat_kill = (os.getenv("LIVE_TRADING_KILL_SWITCH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    schwab_oauth = bool(
        (os.getenv("SCHWAB_ACCOUNT_APP_KEY") or "").strip() and (os.getenv("SCHWAB_ACCOUNT_APP_SECRET") or "").strip()
    )
    schwab_market_oauth = bool(
        (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip() and (os.getenv("SCHWAB_MARKET_APP_SECRET") or "").strip()
    )
    account_callback = (os.getenv("SCHWAB_CALLBACK_URL") or "").strip()
    market_callback = (os.getenv("SCHWAB_MARKET_CALLBACK_URL") or "").strip()
    data: dict[str, Any] = {
        "supabase": supabase,
        "saas_mode": False,
        "runtime_mode": "local",
        "ui_contract_version": "2026-04-webapp-stabilization",
        "scan_transport": "local_thread",
        "backtest_transport": "local_thread",
        "sse_enabled": True,
        "schwab_oauth": schwab_oauth,
        "schwab_market_oauth": schwab_market_oauth,
        "manual_jwt_entry_enabled": _manual_jwt_entry_enabled(default=True),
        "platform_live_trading_kill_switch": plat_kill,
        "api_key_required": bool(configured_api_key),
        "plugin_mode_writes_enabled": True,
        "ablation_writes_enabled": True,
        "scan_studio": _scan_studio_public_config(),
    }
    if account_callback:
        data["schwab_account_callback_url"] = account_callback
    if market_callback:
        data["schwab_market_callback_url"] = market_callback
    impl = (os.getenv("WEB_IMPLEMENTATION_GUIDE_URL") or "").strip()
    if impl.startswith(("http://", "https://")):
        data["implementation_guide_url"] = impl
    return _ok(data)


@app.get("/api/scan-catalog", response_model=ApiResponse)
def scan_catalog() -> ApiResponse:
    """Timeframes, strategy descriptions, and scan universe presets for the dashboard studio."""
    from core.scan_catalog import build_scan_catalog_payload

    return _ok(build_scan_catalog_payload())


@app.get("/api/runtime-contract", response_model=ApiResponse)
def runtime_contract() -> ApiResponse:
    return _ok(
        {
            "runtime_mode": "local",
            "scan_transport": "local_thread",
            "sse_enabled": True,
            "api_envelope": "ApiResponse",
            "ui_contract_version": "2026-04-webapp-stabilization",
        }
    )


@app.get("/api/oauth/schwab/authorize-url", response_model=ApiResponse)
def local_schwab_authorize_url(
    request: Request,
    redirect: bool = False,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse | RedirectResponse:
    client_id = (os.getenv("SCHWAB_ACCOUNT_APP_KEY") or "").strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="Configure SCHWAB_ACCOUNT_APP_KEY for OAuth.")
    redirect_uri = _single_schwab_callback_uri(request)
    state = _new_local_oauth_state("account")
    url = schwab_authorize_url(client_id, redirect_uri, state)
    if _oauth_wants_browser_redirect(request, redirect):
        return RedirectResponse(url, status_code=302)
    return _ok({"url": url, "state": state})


@app.get("/api/oauth/schwab/start", include_in_schema=False)
def local_schwab_authorize_start(
    request: Request,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> RedirectResponse:
    out = local_schwab_authorize_url(request, redirect=True, _auth=_auth)
    assert isinstance(out, RedirectResponse)
    return out


@app.get("/api/oauth/schwab/market/authorize-url", response_model=ApiResponse)
def local_schwab_market_authorize_url(
    request: Request,
    redirect: bool = False,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse | RedirectResponse:
    client_id = (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
    if not client_id:
        raise HTTPException(status_code=503, detail="Configure SCHWAB_MARKET_APP_KEY for market OAuth.")
    redirect_uri = _resolve_schwab_redirect_uri(request, market=True)
    state = _new_local_oauth_state("market")
    url = schwab_authorize_url(client_id, redirect_uri, state)
    if _oauth_wants_browser_redirect(request, redirect):
        return RedirectResponse(url, status_code=302)
    return _ok({"url": url, "state": state})


@app.get("/api/oauth/schwab/market/start", include_in_schema=False)
def local_schwab_market_authorize_start(
    request: Request,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> RedirectResponse:
    out = local_schwab_market_authorize_url(request, redirect=True, _auth=_auth)
    assert isinstance(out, RedirectResponse)
    return out


@app.get("/api/oauth/schwab/portal-config", response_model=ApiResponse)
def local_schwab_oauth_portal_config(
    request: Request,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse:
    """Expose effective Schwab OAuth URLs (same shape as SaaS tenant routes)."""
    account_configured = (os.getenv("SCHWAB_CALLBACK_URL") or "").strip()
    market_configured = (os.getenv("SCHWAB_MARKET_CALLBACK_URL") or "").strip()
    account_effective = _single_schwab_callback_uri(request)
    market_effective = _resolve_schwab_redirect_uri(request, market=True)
    origin = _request_origin(request)
    front = _frontend_oauth_return_url(request)
    return _ok(
        {
            "frontend_origin": origin,
            "frontend_return_url": front,
            "account_authorize_start_url": f"{origin}/api/oauth/schwab/start",
            "market_authorize_start_url": f"{origin}/api/oauth/schwab/market/start",
            "account_callback_url": account_effective,
            "market_callback_url": market_effective,
            "configured_account_callback_url": account_configured or None,
            "configured_market_callback_url": market_configured or None,
            "account_callback_matches_configured": (
                bool(account_configured) and account_effective == account_configured
            ),
            "market_callback_matches_configured": (bool(market_configured) and market_effective == market_configured),
        }
    )


@app.get("/api/oauth/schwab/callback")
def local_schwab_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    front = _frontend_oauth_return_url(request)

    def red(qs: str) -> RedirectResponse:
        return RedirectResponse(_append_query(front, qs), status_code=302)

    def status_key(k: str | None) -> str:
        return "schwab_market_oauth" if k == "market" else "schwab_oauth"

    if error:
        return red(f"{status_key(None)}=error&message={urllib.parse.quote(error)}")
    kind = _consume_local_oauth_state(state)
    if kind != "account" or not code.strip():
        return red("schwab_oauth=error&message=" + urllib.parse.quote("invalid_or_expired_state"))

    client_id = (os.getenv("SCHWAB_ACCOUNT_APP_KEY") or "").strip()
    client_secret = (os.getenv("SCHWAB_ACCOUNT_APP_SECRET") or "").strip()
    redirect_uri = _single_schwab_callback_uri(request)
    if not client_id or not client_secret:
        return red(f"{status_key('account')}=error&message=" + urllib.parse.quote("server_oauth_not_configured"))
    try:
        tok = exchange_schwab_code_for_tokens(client_id, client_secret, code, redirect_uri)
    except Exception as e:
        return red(f"{status_key('account')}=error&message=" + urllib.parse.quote(str(e)[:180]))
    access = str(tok.get("access_token") or "").strip()
    refresh = str(tok.get("refresh_token") or "").strip()
    if not access or not refresh:
        return red(f"{status_key('account')}=error&message=" + urllib.parse.quote("token_response_missing_tokens"))
    write_encrypted_token_file(TOKENS_ACCOUNT_PATH, tok, client_secret)
    reset_shared_auth()
    _audit_event("oauth_schwab_account_callback", "local-dashboard", {"saved": "tokens_account.enc"})
    market_client_id = (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
    market_secret = (os.getenv("SCHWAB_MARKET_APP_SECRET") or "").strip()
    market_missing = not TOKENS_MARKET_PATH.exists()
    if market_client_id and market_secret and market_missing:
        market_state = _new_local_oauth_state("market")
        market_redirect_uri = _resolve_schwab_redirect_uri(request, market=True)
        market_url = schwab_authorize_url(market_client_id, market_redirect_uri, market_state)
        return RedirectResponse(market_url, status_code=302)
    return red("schwab_oauth=ok")


@app.get("/api/oauth/schwab/market/callback")
def local_schwab_market_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    front = _frontend_oauth_return_url(request)

    def red(qs: str) -> RedirectResponse:
        return RedirectResponse(_append_query(front, qs), status_code=302)

    if error:
        return red(f"schwab_market_oauth=error&message={urllib.parse.quote(error)}")
    kind = _consume_local_oauth_state(state)
    if kind != "market" or not code.strip():
        return red("schwab_market_oauth=error&message=" + urllib.parse.quote("invalid_or_expired_state"))

    client_id = (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
    client_secret = (os.getenv("SCHWAB_MARKET_APP_SECRET") or "").strip()
    redirect_uri = _resolve_schwab_redirect_uri(request, market=True)
    if not client_id or not client_secret:
        return red("schwab_market_oauth=error&message=" + urllib.parse.quote("server_market_oauth_not_configured"))
    try:
        tok = exchange_schwab_code_for_tokens(client_id, client_secret, code, redirect_uri)
    except Exception as e:
        return red("schwab_market_oauth=error&message=" + urllib.parse.quote(str(e)[:180]))
    access = str(tok.get("access_token") or "").strip()
    refresh = str(tok.get("refresh_token") or "").strip()
    if not access or not refresh:
        return red("schwab_market_oauth=error&message=" + urllib.parse.quote("token_response_missing_tokens"))
    write_encrypted_token_file(TOKENS_MARKET_PATH, tok, client_secret)
    reset_shared_auth()
    _audit_event("oauth_schwab_market_callback", "local-dashboard", {"saved": "tokens_market.enc"})
    return red("schwab_market_oauth=ok")


@app.get("/api/health/deep", response_model=ApiResponse)
def health_deep(
    db: Session = Depends(get_db),
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse:
    try:
        db_ok = bool(db.query(PendingTrade).limit(1).all() is not None)
        auth = get_shared_auth()
        # Independent, non-raising per-session probes (see /api/status). Fold in
        # refresh-token expiry so a present-but-dead token isn't reported usable.
        try:
            _token_health = auth.get_token_health()
        except Exception:
            _token_health = {"market": {"status": "unknown"}, "account": {"status": "unknown"}}
        market_token_ok, _ = _session_connection_state(
            _session_token_ok(auth.market_session),
            str((_token_health.get("market") or {}).get("status") or ""),
        )
        account_token_ok, _ = _session_connection_state(
            _session_token_ok(auth.account_session),
            str((_token_health.get("account") or {}).get("status") or ""),
        )
        quote, qmeta = get_current_quote_with_status("AAPL", auth=auth, skill_dir=SKILL_DIR)
        quote_ok = extract_schwab_last_price(quote) is not None
        with _metrics_lock:
            metrics = json.loads(json.dumps(_request_metrics))
        qh = {
            "symbol": qmeta.get("symbol"),
            "ok": quote_ok,
            "reason": None if quote_ok else (qmeta.get("reason") or "unknown"),
            "operator_hint": _quote_health_hint(qmeta, quote_ok),
            "http_status": qmeta.get("http_status"),
            "top_level_keys": qmeta.get("top_level_keys"),
            "quote_keys": qmeta.get("quote_keys"),
        }
        if not quote_ok and qmeta.get("error_detail"):
            qh["error_detail"] = str(qmeta["error_detail"])[:400]
        return _ok(
            {
                "db_ok": db_ok,
                "market_token_ok": market_token_ok,
                "account_token_ok": account_token_ok,
                "quote_ok": quote_ok,
                # Honest tri-state for the diagnostics ribbon. "connected" only
                # when the live quote probe actually succeeded (see
                # rollup_connection_state); otherwise "unverified"/"disconnected".
                "connection_state": _rollup_connection_state(market_token_ok, account_token_ok, quote_ok),
                "quote_health": qh,
                "metrics": metrics,
            }
        )
    except Exception as e:
        return _err("health_deep", e)


@app.get("/api/config", response_model=ApiResponse)
def config() -> ApiResponse:
    return _ok(
        {
            "trade_api_key_required": bool(os.getenv("WEB_API_KEY", "").strip()),
            "allowed_origins": allowed_origins,
        }
    )


@app.get("/api/prob-rank/shadow-evidence", response_model=ApiResponse)
def prob_rank_shadow_evidence(
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse:
    """Summarize append-only prob-rank vs rank-v2 shadow evidence ledger."""
    try:
        from research.shadow_evidence import (
            ledger_path,
            load_shadow_evidence_records,
            summarize_shadow_evidence,
        )

        records = load_shadow_evidence_records(SKILL_DIR)
        cf_rows = [r for r in records if r.get("source") == "cf_day_cohort"]
        live_rows = [r for r in records if r.get("source") != "cf_day_cohort"]
        disagreement = None
        disagree_path = (
            SKILL_DIR / "validation_artifacts" / "prob_rank_shadow_evidence" / "disagreement_attribution.json"
        )
        if disagree_path.is_file():
            try:
                disagreement = json.loads(disagree_path.read_text(encoding="utf-8"))
            except Exception:
                disagreement = None
        return _ok(
            {
                "all": summarize_shadow_evidence(records),
                "live_scans": summarize_shadow_evidence(live_rows),
                "cf_day_cohorts": summarize_shadow_evidence(cf_rows),
                "disagreement_attribution": {
                    "verdict": (disagreement or {}).get("verdict"),
                    "rationale": (disagreement or {}).get("rationale"),
                    "mean_jaccard": (disagreement or {}).get("mean_jaccard"),
                    "n_days": (disagreement or {}).get("n_days"),
                    "buckets": (disagreement or {}).get("buckets"),
                    "full_top_n_arms": (disagreement or {}).get("full_top_n_arms"),
                    "path": str(disagree_path) if disagreement else None,
                },
                "ledger": str(ledger_path(SKILL_DIR)),
                "note": "Shadow log only — does not change selection; do not enable live from this alone",
            }
        )
    except Exception as e:
        return _err("prob_rank_shadow_evidence", e)


@app.get("/api/status", response_model=ApiResponse)
def status(
    db: Session = Depends(get_db),
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse:
    try:
        auth = get_shared_auth()
        checked_at = datetime.now(timezone.utc).isoformat()
        # Refresh-token age health (per session + roll-up). Pure read from
        # the encrypted token file — never triggers a network refresh.
        # Wrapped in try/except so a malformed token file or new field
        # rollout never breaks /api/status (which the dashboard polls).
        try:
            schwab_token_health = auth.get_token_health()
        except Exception as exc:
            schwab_token_health = {
                "status": "unknown",
                "market": {"status": "unknown"},
                "account": {"status": "unknown"},
                "error": str(exc)[:200],
            }
        # Evaluate each session independently and non-raising so a single
        # disconnected session can never mask the other's state or error out
        # the whole endpoint (the dashboard polls this and would otherwise
        # mark BOTH pills unavailable). Fold in refresh-token health so an
        # expired token reads "Reauth needed" instead of a misleading
        # "Connected" (a present-but-dead token 401s on every call).
        market_health = str((schwab_token_health.get("market") or {}).get("status") or "")
        account_health = str((schwab_token_health.get("account") or {}).get("status") or "")
        market_token_ok, market_state = _session_connection_state(_session_token_ok(auth.market_session), market_health)
        account_token_ok, account_state = _session_connection_state(
            _session_token_ok(auth.account_session), account_health
        )
        last_scan = _load_state(
            db,
            key="last_scan",
            default={
                "at": None,
                "signals_found": None,
                "signals": [],
                "diagnostics": None,
                "diagnostics_summary": None,
                "strategy_summary": None,
            },
        )
        return _ok(
            {
                "market_token_ok": market_token_ok,
                "account_token_ok": account_token_ok,
                "market_state": market_state,
                "account_state": account_state,
                "checked_at": checked_at,
                "last_scan": last_scan,
                "validation_status": _latest_validation_status(SKILL_DIR),
                "schwab_token_health": schwab_token_health,
            }
        )
    except Exception as e:
        return _err("status", e)


@app.get("/api/validation/status", response_model=ApiResponse)
def validation_status() -> ApiResponse:
    try:
        return _ok(_latest_validation_status(SKILL_DIR))
    except Exception as e:
        return _err("validation_status", e)


@app.get("/api/decision-dashboard", response_model=ApiResponse)
def decision_dashboard(
    db: Session = Depends(get_db),
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse:
    try:
        return _ok(_decision_dashboard_snapshot(db))
    except Exception as e:
        return _err("decision_dashboard", e)


@app.post("/api/scan", response_model=ApiResponse)
def scan(
    async_mode: bool = True,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
    body: dict[str, Any] | None = Body(default=None),
) -> ApiResponse:
    try:
        try:
            parsed_scan = parse_scan_run_body(body)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        skw = scan_runtime_kwargs(parsed_scan)
        env_overrides = dict(skw.get("env_overrides") or {})
        env_overrides.setdefault("SIGNAL_TOP_N", "0")
        skw["env_overrides"] = env_overrides

        if async_mode:
            started = False
            with _scan_lock:
                _expire_stale_scan_job_locked()
                if _scan_job.get("status") == "running":
                    pass
                else:
                    job_id = uuid.uuid4().hex[:10]
                    _scan_job.update(
                        {
                            "job_id": job_id,
                            "status": "running",
                            "started_at": datetime.now(timezone.utc).isoformat(),
                            "finished_at": None,
                            "signals_found": None,
                            "diagnostics": None,
                            "diagnostics_summary": None,
                            "strategy_summary": None,
                            "signals": [],
                            "shortlist_signals": [],
                            "error": None,
                        }
                    )
                    thread = threading.Thread(
                        target=_scan_worker,
                        args=(job_id, skw),
                        daemon=True,
                        name=f"scan-{job_id}",
                    )
                    _bind_scan_worker(thread)
                    started = True
            if started:
                thread.start()
            snapshot = _scan_snapshot()
            return _ok({"started": started, **snapshot})

        scan_out = run_scan(skill_dir=SKILL_DIR, **skw)
        signals = scan_out.signals
        diagnostics = scan_out.diagnostics
        shortlist_signals = scan_out.shortlist_signals
        diagnostics_summary = _diagnostics_summary(diagnostics, signals)
        strategy_summary = _strategy_summary(signals)
        now_iso = datetime.now(timezone.utc).isoformat()
        sync_job_id = uuid.uuid4().hex[:10]
        _persist_scan_results_local(db, sync_job_id, signals)
        _enrich_signals_with_flagged_days(db, signals)
        _enrich_signals_with_flagged_days(db, shortlist_signals)
        _prune_local_scan_results(db)
        signals_persist = signals[:_LAST_SCAN_SIGNALS_CAP]
        shortlist_persist = shortlist_signals[:_LAST_SCAN_SIGNALS_CAP]
        last_scan = {
            "at": now_iso,
            "signals_found": len(signals),
            "signals": signals_persist,
            "shortlist_signals": shortlist_persist,
            "diagnostics": diagnostics,
            "diagnostics_summary": diagnostics_summary,
            "strategy_summary": strategy_summary,
        }
        _save_last_scan(db, last_scan)
        _harvest_experiment_scan(diagnostics, signals, now_iso)
        _record_scan_outcomes(signals, diagnostics)
        return _ok(
            {
                "signals_found": len(signals),
                "signals": signals,
                "shortlist_signals": shortlist_signals,
                "diagnostics": diagnostics,
                "diagnostics_summary": diagnostics_summary,
                "strategy_summary": strategy_summary,
            }
        )
    except Exception as e:
        return _err("scan", e)


@app.get("/api/scan/status", response_model=ApiResponse)
def scan_status(db: Session = Depends(get_db)) -> ApiResponse:
    snapshot = _scan_snapshot()
    if snapshot.get("status") == "idle":
        last_scan = _load_state(
            db,
            key="last_scan",
            default={
                "at": None,
                "signals_found": None,
                "signals": [],
                "diagnostics": None,
                "diagnostics_summary": None,
                "strategy_summary": None,
            },
        )
        return _ok({"status": "idle", "last_scan": last_scan})
    return _ok(snapshot)


@app.get("/api/scan-lifecycle", response_model=ApiResponse)
def scan_lifecycle(db: Session = Depends(get_db)) -> ApiResponse:
    snapshot = _scan_snapshot()
    last_scan = None
    if snapshot.get("status") == "idle":
        last_scan = _load_state(
            db,
            key="last_scan",
            default={
                "at": None,
                "signals_found": None,
                "signals": [],
                "diagnostics": None,
                "diagnostics_summary": None,
                "strategy_summary": None,
            },
        )
    return _ok(_scan_lifecycle_payload(snapshot, last_scan=last_scan))


def _queued_backtest_count(db: Session) -> int:
    return (
        db.query(BacktestRun)
        .filter(
            BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID,
            BacktestRun.status == "queued",
        )
        .count()
    )


def _oldest_queued_backtest(db: Session) -> BacktestRun | None:
    return (
        db.query(BacktestRun)
        .filter(
            BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID,
            BacktestRun.status == "queued",
        )
        .order_by(BacktestRun.created_at.asc())
        .first()
    )


def _queue_position_map(db: Session) -> dict[str, int]:
    rows = (
        db.query(BacktestRun)
        .filter(
            BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID,
            BacktestRun.status.in_(("queued", "running")),
        )
        .order_by(BacktestRun.created_at.asc())
        .all()
    )
    return {str(row.id): idx + 1 for idx, row in enumerate(rows)}


def _previous_summary_map(rows: list[Any]) -> dict[str, dict[str, Any]]:
    return previous_summary_map(rows, parse_result=_coerce_json_dict)


def _kick_next_local_backtest_locked(db: Session) -> None:
    """Start the oldest queued run if the local worker is idle. Caller holds the lock."""
    global _local_backtest_thread
    if _local_backtest_thread is not None and _local_backtest_thread.is_alive():
        return
    row = _oldest_queued_backtest(db)
    if row is None:
        return
    thread = threading.Thread(
        target=_local_backtest_worker,
        args=(row.id,),
        daemon=True,
        name=f"backtest-{str(row.id)[:8]}",
    )
    _local_backtest_thread = thread
    thread.start()


def _continue_auto_study(db: Session, finished_run_id: str) -> str | None:
    """Queue one next study spec after a finished trial.

    The study policy still decides. A stop, a hold, or a test already in
    flight does not add another run.
    """
    from .backtest_spec import parse_strategy_spec
    from .backtest_study_store import (
        append_trial,
        load_active_study,
        refresh_proposal,
        stamp_theory,
    )

    with _study_queue_lock:
        study = load_active_study(SKILL_DIR)
        if not isinstance(study, dict) or not study.get("auto"):
            return None
        if str(study.get("status") or "") != "active":
            return None
        trials = study.get("trials") or []
        if not any(
            isinstance(row, dict) and str(row.get("run_id") or "") == finished_run_id for row in trials
        ):
            return None
        proposal = refresh_proposal(study, _study_runs_by_id(db, study), skill_dir=SKILL_DIR)
        if str(proposal.get("action") or "") != "queue" or not isinstance(proposal.get("spec"), dict):
            return None
        stamped = stamp_theory(
            proposal["spec"],
            str(study.get("study_id") or ""),
            str(proposal.get("hypothesis") or "next test"),
        )
        parsed = parse_strategy_spec(stamped)
        run_id = _enqueue_local_backtest(db, parsed)
        append_trial(study, proposal, run_id, parsed.model_dump(exclude_none=True))
        refresh_proposal(study, _study_runs_by_id(db, study), skill_dir=SKILL_DIR)
        return run_id


def _local_backtest_worker(run_id: str) -> None:
    """Run one queued backtest in-process and persist the outcome on its row."""
    global _local_backtest_thread
    from .backtest_spec import parse_strategy_spec, run_strategy_backtest

    db = SessionLocal()
    try:
        row = (
            db.query(BacktestRun)
            .filter(BacktestRun.id == run_id, BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID)
            .first()
        )
        if row is None:
            return
        if row.status == "cancelled":
            return
        row.status = "running"
        db.commit()
        try:
            spec = parse_strategy_spec(_coerce_json_dict(row.spec_json))

            def _on_progress(snapshot: dict[str, Any]) -> None:
                try:
                    db.refresh(row)
                    if row.status != "running":
                        return
                    row.result_json = json.dumps(snapshot, default=_json_default)
                    db.commit()
                except Exception:
                    db.rollback()
                    LOG.warning("Could not save live backtest tape for %s", run_id)

            result = run_strategy_backtest(SKILL_DIR, spec, on_progress=_on_progress)
            row.status = "success"
            row.result_json = json.dumps(result, default=_json_default)
            row.error_message = None
            db.commit()
        except Exception as exc:
            LOG.warning("Local backtest %s failed: %s", run_id, exc)
            row.status = "failed"
            row.error_message = str(exc)[:2000] or "backtest_failed"
            row.result_json = None
            db.commit()
            _record_endpoint_error("backtest_worker")
    finally:
        db.close()
        follow = SessionLocal()
        try:
            _continue_auto_study(follow, run_id)
            with _local_backtest_lock:
                _local_backtest_thread = None
                _kick_next_local_backtest_locked(follow)
        finally:
            follow.close()


def _backtest_run_result_summary(result: Any) -> dict[str, Any] | None:
    """Short metrics for the run-list UI; mirrors the SaaS list shape."""
    return result_metrics_summary(result)


def _enqueue_local_backtest(db: Session, spec: Any) -> str:
    """Insert one queued run and wake the local worker. Raises 409 when the wait cap is full."""
    waiting = _queued_backtest_count(db)
    if waiting >= LOCAL_BACKTEST_MAX_WAITING:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Already {LOCAL_BACKTEST_MAX_WAITING} backtests waiting. "
                "Wait for one to start, or cancel a queued job."
            ),
        )
    run_id = uuid.uuid4().hex
    row = BacktestRun(
        id=run_id,
        user_id=LOCAL_DASHBOARD_USER_ID,
        celery_task_id=run_id,
        status="queued",
        spec_json=json.dumps(spec.model_dump(), default=_json_default),
    )
    db.add(row)
    db.commit()
    with _local_backtest_lock:
        _kick_next_local_backtest_locked(db)
    return run_id


@app.post("/api/backtest-runs", response_model=ApiResponse)
def queue_local_backtest_run(
    payload: QueueUserBacktestRequest,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Queue a backtest on the local single-user server (thread transport).

    Same URL and response contract as the SaaS Celery queue so the dashboard
    backtest panel works identically in both deploy modes. One worker runs at
    a time; extra jobs stay queued and drain FIFO.
    """
    from .backtest_spec import parse_strategy_spec, spec_preview_dict

    try:
        spec = parse_strategy_spec(payload.spec)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid strategy spec: {exc}") from exc
    run_id = _enqueue_local_backtest(db, spec)
    return _ok({"ok": True, "run_id": run_id, "task_id": run_id, "preview": spec_preview_dict(spec)})


@app.get("/api/backtest-runs", response_model=ApiResponse)
def list_local_backtest_runs(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ApiResponse:
    rows = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID)
        .order_by(BacktestRun.created_at.desc())
        .limit(limit)
        .all()
    )
    payload: list[dict[str, Any]] = []
    for row in rows:
        parsed_result = _coerce_json_dict(row.result_json) if row.result_json else {}
        item: dict[str, Any] = {
            "id": row.id,
            "celery_task_id": row.celery_task_id,
            "status": row.status,
            "spec": _coerce_json_dict(row.spec_json),
            "error_message": row.error_message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        annotate_run_list_item(item, parsed_result)
        payload.append(attach_run_labels(item))
    return _ok(payload)


@app.get("/api/backtest-runs/tasks/{task_id}", response_model=ApiResponse)
def local_backtest_run_task_status(
    task_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse:
    row = (
        db.query(BacktestRun)
        .filter(
            BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID,
            BacktestRun.celery_task_id == task_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    payload: dict[str, Any] = {
        "task_id": task_id,
        "celery_status": _LOCAL_BACKTEST_STATUS_TO_CELERY.get(row.status, "pending"),
        "run_id": row.id,
        "db_status": row.status,
        "error_message": row.error_message,
    }
    parsed = _coerce_json_dict(row.result_json) if row.result_json else {}
    final, _progress = stored_result_parts(parsed)
    if final is not None:
        payload["result"] = final
    return _ok(payload)


def _local_queue_busy() -> bool:
    with _local_backtest_lock:
        return _local_backtest_thread is not None and _local_backtest_thread.is_alive()


def _serialize_local_run(
    row: Any,
    *,
    include_result: bool = False,
    queue_position: int | None = None,
    previous_summary: dict[str, Any] | None = None,
    queue_busy: bool | None = None,
) -> dict[str, Any]:
    raw = _coerce_json_dict(row.result_json) if row.result_json else {}
    final, progress = stored_result_parts(raw)
    spec = _coerce_json_dict(row.spec_json)
    busy = _local_queue_busy() if queue_busy is None else queue_busy
    item = serialize_run(
        row,
        result=final,
        spec=spec,
        include_result=include_result,
        queue_busy=busy and row.status in {"queued", "running"},
        queue_position=queue_position,
        previous_summary=previous_summary,
    )
    if row.status == "running":
        tape = public_trade_progress(progress)
        if tape is not None:
            item["progress"] = tape
    return item


@app.get("/api/backtest-hub", response_model=ApiResponse)
def local_backtest_hub(
    limit: int = Query(default=15, ge=1, le=50),
    db: Session = Depends(get_db),
) -> ApiResponse:
    # Take the worker lock once, before the read, so serializing each row
    # cannot wait on the backtest thread while this session holds SQLite.
    busy = _local_queue_busy()
    rows = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID)
        .order_by(BacktestRun.created_at.desc())
        .limit(limit)
        .all()
    )
    positions = _queue_position_map(db)
    prev_map = _previous_summary_map(rows)
    runs = [
        _serialize_local_run(
            row,
            queue_position=positions.get(str(row.id)),
            previous_summary=prev_map.get(str(row.id)),
            queue_busy=busy,
        )
        for row in rows
    ]
    last_scan = _load_state(db, key="last_scan", default={})
    board_rows = (
        db.query(BacktestRun)
        .filter(
            BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID,
            BacktestRun.status == "success",
            BacktestRun.result_json.isnot(None),
        )
        .order_by(BacktestRun.created_at.desc())
        .limit(SCAN_CAP)
        .all()
    )
    leaderboard_runs = [
        leaderboard_item_from_parts(
            run_id=row.id,
            status=row.status,
            spec=_coerce_json_dict(row.spec_json),
            summary=_backtest_run_result_summary(_coerce_json_dict(row.result_json) if row.result_json else {}),
            created_at=row.created_at.isoformat() if row.created_at else None,
        )
        for row in board_rows
    ]
    # File assembly does not need the connection. End the read now so the
    # rest of the dashboard is not stuck behind this snapshot.
    db.rollback()
    return _ok(
        build_hub(
            skill_dir=SKILL_DIR,
            runs=runs,
            queue_busy=busy,
            concurrency_note="This computer runs one backtest at a time. Extra jobs wait in line.",
            last_scan=last_scan if isinstance(last_scan, dict) else {},
            leaderboard_runs=leaderboard_runs,
        )
    )


def _study_runs_by_id(db: Session, study: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not study:
        return {}
    ids = [str(row.get("run_id")) for row in study.get("trials") or [] if isinstance(row, dict) and row.get("run_id")]
    if not ids:
        return {}
    found = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID, BacktestRun.id.in_(ids))
        .all()
    )
    payload: dict[str, dict[str, Any]] = {}
    for row in found:
        result = _coerce_json_dict(row.result_json) if row.result_json else {}
        payload[str(row.id)] = {
            "status": row.status,
            "result": result if row.status == "success" and result else None,
        }
    return payload


@app.get("/api/backtest-study", response_model=ApiResponse)
def local_backtest_study(db: Session = Depends(get_db)) -> ApiResponse:
    """Current adaptive study: hypothesis, factor effects, and the unqueued next spec."""
    from .backtest_study_store import build_view, idle_view, load_active_study, refresh_proposal

    study = load_active_study(SKILL_DIR)
    if study is None:
        return _ok(idle_view())
    proposal = refresh_proposal(study, _study_runs_by_id(db, study), skill_dir=SKILL_DIR)
    return _ok(build_view(study, proposal))


@app.post("/api/backtest-study/next", response_model=ApiResponse)
def local_backtest_study_next(
    payload: BacktestStudyNextRequest,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Queue the single next informed spec. Does not stack tests ahead of a result."""
    from .backtest_spec import parse_strategy_spec
    from .backtest_study_store import (
        append_trial,
        build_view,
        load_active_study,
        new_study,
        refresh_proposal,
        save_study,
        stamp_theory,
    )

    with _study_queue_lock:
        study = load_active_study(SKILL_DIR)
        if payload.restart or study is None or str(study.get("status") or "") != "active":
            if study is not None and str(study.get("status") or "") != "active" and not payload.restart:
                proposal = refresh_proposal(study, _study_runs_by_id(db, study), skill_dir=SKILL_DIR)
                return _ok(build_view(study, proposal))
            if not isinstance(payload.spec, dict):
                raise HTTPException(status_code=400, detail="spec is required to start a study")
            try:
                parse_strategy_spec(payload.spec)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid strategy spec: {exc}") from exc
            study = new_study(payload.spec, auto=bool(payload.auto))
            save_study(SKILL_DIR, study)
        elif payload.auto:
            study["auto"] = True
            save_study(SKILL_DIR, study)

        proposal = refresh_proposal(study, _study_runs_by_id(db, study), skill_dir=SKILL_DIR)
        if str(proposal.get("action") or "") != "queue" or not isinstance(proposal.get("spec"), dict):
            return _ok(build_view(study, proposal))

        stamped = stamp_theory(
            proposal["spec"],
            str(study.get("study_id") or ""),
            str(proposal.get("hypothesis") or "next test"),
        )
        try:
            parsed = parse_strategy_spec(stamped)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid strategy spec: {exc}") from exc
        run_id = _enqueue_local_backtest(db, parsed)
        append_trial(study, proposal, run_id, parsed.model_dump(exclude_none=True))
        waiting = refresh_proposal(study, _study_runs_by_id(db, study), skill_dir=SKILL_DIR)
        view = build_view(study, waiting)
        view["queued_run_id"] = run_id
        return _ok(view)


@app.get("/api/backtest-runs/{run_id}", response_model=ApiResponse)
def local_backtest_run_detail(
    run_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse:
    row = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID, BacktestRun.id == run_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest run not found.")
    older = (
        db.query(BacktestRun)
        .filter(
            BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID,
            BacktestRun.status == "success",
            BacktestRun.created_at < row.created_at,
        )
        .order_by(BacktestRun.created_at.desc())
        .first()
    )
    prev = None
    if older is not None:
        prev = _backtest_run_result_summary(_coerce_json_dict(older.result_json) if older.result_json else {})
    positions = _queue_position_map(db)
    return _ok(
        _serialize_local_run(
            row,
            include_result=True,
            queue_position=positions.get(str(row.id)),
            previous_summary=prev,
        )
    )


@app.post("/api/backtest-runs/{run_id}/cancel", response_model=ApiResponse)
def cancel_local_backtest_run(
    run_id: str,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    row = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == LOCAL_DASHBOARD_USER_ID, BacktestRun.id == run_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Backtest run not found.")
    if row.status != "queued":
        raise HTTPException(
            status_code=409,
            detail="Only waiting jobs can be cancelled. A running replay cannot be stopped mid-flight.",
        )
    row.status = "cancelled"
    row.error_message = "Cancelled before start."
    db.commit()
    return _ok({"id": row.id, "status": row.status})


@app.get("/api/backtest-artifacts", response_model=ApiResponse)
def local_backtest_artifacts() -> ApiResponse:
    return _ok({"items": list_artifact_catalog(SKILL_DIR)})


@app.get("/api/backtest-artifacts/{name}", response_model=ApiResponse)
def local_backtest_artifact_detail(name: str) -> ApiResponse:
    if resolve_artifact_path(SKILL_DIR, name) is None:
        raise HTTPException(status_code=404, detail="Artifact is not on the allowlist.")
    detail = artifact_detail(SKILL_DIR, name)
    if detail is None:
        raise HTTPException(status_code=404, detail="Artifact is not on the allowlist.")
    return _ok(detail)


@app.get("/api/portfolio", response_model=ApiResponse)
def portfolio(db: Session = Depends(get_db)) -> ApiResponse:
    try:
        auth = get_shared_auth()
        status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
        if isinstance(status_data, str):
            _record_endpoint_error("portfolio")
            return ApiResponse(ok=False, error=status_data)
        try:
            from core.portfolio_equity_snapshot import record_equity_snapshot
            from core.providers import PortfolioProvider

            # Skip live sector lookups here — yfinance on OCC option symbols can
            # stall GET /api/portfolio before the positions table is returned.
            state = PortfolioProvider.normalize_account(status_data)
            record_equity_snapshot(db, state, user_id=LOCAL_DASHBOARD_USER_ID)
        except Exception as snap_exc:
            LOG.debug("Portfolio snapshot skipped: %s", snap_exc)
        summary = _build_portfolio_summary(status_data)
        from core.protective_orders import annotate_portfolio_protection

        annotate_portfolio_protection(summary, SKILL_DIR)
        return _ok(summary)
    except Exception as e:
        return _err("portfolio", e)


@app.post("/api/execution/ambiguity/resolve", response_model=ApiResponse)
def resolve_execution_ambiguity(
    body: dict[str, Any] = Body(...),
    _auth: dict[str, str] = Depends(require_trade_api_key),
) -> ApiResponse:
    """Clear a symbol lock after the operator checks the order at Schwab."""
    from execution import resolve_order_ambiguity

    result = resolve_order_ambiguity(
        SKILL_DIR,
        str(body.get("ticker") or ""),
        str(body.get("outcome") or ""),
    )
    if not result.get("ok"):
        return ApiResponse(ok=False, error=str(result.get("error") or "resolve failed"), data=result)
    return _ok(result)


@app.get("/api/portfolio/risk", response_model=ApiResponse)
def portfolio_risk() -> ApiResponse:
    """Portfolio risk analytics: sector allocation, concentration, and exposure metrics."""
    try:
        auth = get_shared_auth()
        status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
        if isinstance(status_data, str):
            _record_endpoint_error("portfolio_risk")
            return ApiResponse(ok=False, error=status_data)
        summary = _build_portfolio_summary(status_data)
        return _ok(_build_portfolio_risk_analytics(summary, skill_dir=SKILL_DIR))
    except Exception as e:
        return _err("portfolio_risk", e)


@app.get("/api/portfolio/analytics", response_model=ApiResponse)
def portfolio_analytics(
    lookback_days: int | None = Query(default=None, ge=20, le=756),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """PM-grade portfolio metrics: volatility, Sharpe, beta, correlation, and closed-trade stats."""
    try:
        from config import get_portfolio_equity_snapshot_enabled
        from core.portfolio_analytics_service import build_portfolio_analytics
        from core.portfolio_equity_snapshot import load_equity_curve, record_equity_snapshot
        from core.providers import PortfolioProvider
        from sector_strength import get_ticker_sector_etf

        auth = get_shared_auth()
        status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
        if isinstance(status_data, str):
            _record_endpoint_error("portfolio_analytics")
            return ApiResponse(ok=False, error=status_data)
        state = PortfolioProvider.normalize_account(
            status_data,
            sector_lookup=lambda t: get_ticker_sector_etf(t, skill_dir=SKILL_DIR),
        )
        snapshots: list[dict[str, Any]] = []
        if get_portfolio_equity_snapshot_enabled(SKILL_DIR):
            record_equity_snapshot(db, state, user_id=LOCAL_DASHBOARD_USER_ID)
            snapshots = load_equity_curve(db, user_id=LOCAL_DASHBOARD_USER_ID, limit=252)
        pack = build_portfolio_analytics(
            state,
            skill_dir=SKILL_DIR,
            auth=auth,
            lookback_days=lookback_days,
            equity_curve=snapshots,
        )
        return _ok(pack.model_dump(mode="json"))
    except Exception as e:
        return _err("portfolio_analytics", e)


# Risk-dashboard pack cache: the build fetches a year of history per ticker
# (~40-70s cold), so tab re-entry within the TTL serves the cached pack.
_RISK_DASHBOARD_CACHE_TTL_SEC = 300.0
_risk_dashboard_cache: dict[str, Any] = {"key": None, "at": 0.0, "payload": None}
_risk_dashboard_cache_lock = threading.Lock()


@app.get("/api/portfolio/risk-dashboard", response_model=ApiResponse)
def portfolio_risk_dashboard(
    lookback_days: int | None = Query(default=None, ge=20, le=756),
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Unified risk dashboard: metrics, correlation, risk contribution, concentration limits, and stress tests."""
    try:
        from config import get_portfolio_equity_snapshot_enabled
        from core.portfolio_analytics_service import build_portfolio_risk_dashboard
        from core.portfolio_equity_snapshot import load_equity_curve, record_equity_snapshot
        from core.providers import PortfolioProvider
        from sector_strength import get_ticker_sector_etf

        cache_key = f"lookback={lookback_days or 'default'}"
        if not force:
            with _risk_dashboard_cache_lock:
                cached = _risk_dashboard_cache
                if (
                    cached["key"] == cache_key
                    and cached["payload"] is not None
                    and (time.time() - cached["at"]) < _RISK_DASHBOARD_CACHE_TTL_SEC
                ):
                    payload = dict(cached["payload"])
                    payload["cache_hit"] = True
                    return _ok(payload)

        auth = get_shared_auth()
        status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
        if isinstance(status_data, str):
            _record_endpoint_error("portfolio_risk_dashboard")
            return ApiResponse(ok=False, error=status_data)
        state = PortfolioProvider.normalize_account(
            status_data,
            sector_lookup=lambda t: get_ticker_sector_etf(t, skill_dir=SKILL_DIR),
        )
        summary = _build_portfolio_summary(status_data)
        static_risk = _build_portfolio_risk_analytics(summary, skill_dir=SKILL_DIR)
        snapshots: list[dict[str, Any]] = []
        if get_portfolio_equity_snapshot_enabled(SKILL_DIR):
            record_equity_snapshot(db, state, user_id=LOCAL_DASHBOARD_USER_ID)
            snapshots = load_equity_curve(db, user_id=LOCAL_DASHBOARD_USER_ID, limit=252)
        pack = build_portfolio_risk_dashboard(
            state,
            summary,
            static_risk=static_risk,
            skill_dir=SKILL_DIR,
            auth=auth,
            lookback_days=lookback_days,
            equity_curve=snapshots,
        )
        payload = pack.model_dump(mode="json")
        payload["cached_at"] = datetime.now(timezone.utc).isoformat()
        payload["cache_hit"] = False
        with _risk_dashboard_cache_lock:
            _risk_dashboard_cache.update({"key": cache_key, "at": time.time(), "payload": payload})
        return _ok(payload)
    except Exception as e:
        return _err("portfolio_risk_dashboard", e)


# --- Manual (non-Schwab) portfolio risk -------------------------------------
# Public ingestion adapter: anonymous users enter ticker/qty rows and reuse
# the same risk pack a Schwab-linked account gets. These endpoints are
# compute-only (no orders, no account access, no persistence) which is why
# they intentionally carry no API-key dependency; abuse is bounded by the
# position cap (schema), a per-IP build interval, and a shared payload cache.
_MANUAL_RISK_CACHE_TTL_SEC = 300.0
_MANUAL_BUILD_MIN_INTERVAL_SEC = 60.0
_manual_risk_cache: dict[str, Any] = {"key": None, "at": 0.0, "payload": None}
_manual_risk_cache_lock = threading.Lock()
_manual_build_last_by_ip: dict[str, float] = {}
_manual_build_lock = threading.Lock()


def _shared_auth_or_none() -> DualSchwabAuth | None:
    """Best-effort Schwab auth: manual mode must keep working without tokens
    (market_data falls back to yfinance when the Schwab session is unusable)."""
    try:
        return get_shared_auth()
    except Exception:
        return None


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


def _manual_rate_limited(request: Request) -> float:
    """Returns seconds the caller must still wait (0 = allowed) and stamps the slot."""
    ip = (request.client.host if request.client else None) or "unknown"
    now = time.time()
    with _manual_build_lock:
        last = _manual_build_last_by_ip.get(ip, 0.0)
        wait = _MANUAL_BUILD_MIN_INTERVAL_SEC - (now - last)
        if wait > 0:
            return wait
        # Opportunistic pruning keeps the map from growing unbounded.
        if len(_manual_build_last_by_ip) > 1000:
            cutoff = now - _MANUAL_BUILD_MIN_INTERVAL_SEC
            for stale_ip in [k for k, v in _manual_build_last_by_ip.items() if v < cutoff]:
                del _manual_build_last_by_ip[stale_ip]
        _manual_build_last_by_ip[ip] = now
        return 0.0


@app.post("/api/portfolio/manual/positions", response_model=ApiResponse)
def manual_portfolio_positions(body: ManualPortfolioBody) -> ApiResponse:
    """Price a manually entered book: last close, market value, and weights.

    Fail-closed: any unpriceable ticker rejects the whole book so weights are
    never silently wrong. Public by design (compute-only; see block comment).
    """
    try:
        from core.providers import ManualPortfolioError, ManualPortfolioProvider

        try:
            state, summary = ManualPortfolioProvider.build(
                [r.model_dump() for r in body.positions],
                cash=body.cash,
                skill_dir=SKILL_DIR,
                auth=_shared_auth_or_none(),
            )
        except ManualPortfolioError as exc:
            _record_endpoint_error("manual_portfolio_positions")
            return ApiResponse(ok=False, error=str(exc), data={"unpriced_tickers": exc.unpriced})
        payload = dict(summary)
        payload["positions"] = [
            {**row, "weight_pct": round(row["market_value"] / state.equity * 100, 2) if state.equity else None}
            for row in summary["positions"]
        ]
        payload["as_of"] = datetime.now(timezone.utc).isoformat()
        return _ok(payload)
    except Exception as e:
        return _err("manual_portfolio_positions", e)


@app.post("/api/portfolio/risk-dashboard/manual", response_model=ApiResponse)
def portfolio_risk_dashboard_manual(body: ManualPortfolioBody, request: Request) -> ApiResponse:
    """Risk dashboard for a manually entered book — same pack as the Schwab route.

    Public by design (compute-only; see block comment). Identical payloads are
    served from a 5-minute cache; fresh builds are limited to one per minute
    per client IP because each build fans out a year of history per ticker.
    """
    try:
        from core.portfolio_analytics_service import build_portfolio_risk_dashboard
        from core.providers import ManualPortfolioError, ManualPortfolioProvider
        from sector_strength import get_ticker_sector_etf

        cache_key = _manual_cache_key(body)
        if not body.force:
            with _manual_risk_cache_lock:
                cached = _manual_risk_cache
                if (
                    cached["key"] == cache_key
                    and cached["payload"] is not None
                    and (time.time() - cached["at"]) < _MANUAL_RISK_CACHE_TTL_SEC
                ):
                    payload = dict(cached["payload"])
                    payload["cache_hit"] = True
                    return _ok(payload)

        wait = _manual_rate_limited(request)
        if wait > 0:
            _record_endpoint_error("portfolio_risk_dashboard_manual")
            return ApiResponse(
                ok=False,
                error=f"Rate limited: manual risk builds are capped at one per minute. Retry in {int(wait) + 1}s.",
                data={"retry_after_sec": int(wait) + 1},
            )

        auth = _shared_auth_or_none()
        try:
            state, summary = ManualPortfolioProvider.build(
                [r.model_dump() for r in body.positions],
                cash=body.cash,
                skill_dir=SKILL_DIR,
                auth=auth,
                sector_lookup=lambda t: get_ticker_sector_etf(t, skill_dir=SKILL_DIR),
            )
        except ManualPortfolioError as exc:
            _record_endpoint_error("portfolio_risk_dashboard_manual")
            return ApiResponse(ok=False, error=str(exc), data={"unpriced_tickers": exc.unpriced})

        static_risk = _build_portfolio_risk_analytics(summary, skill_dir=SKILL_DIR)
        pack = build_portfolio_risk_dashboard(
            state,
            summary,
            static_risk=static_risk,
            skill_dir=SKILL_DIR,
            auth=auth,
            lookback_days=body.lookback_days,
            equity_curve=[],
        )
        payload = pack.model_dump(mode="json")
        payload["source"] = "manual"
        payload["cached_at"] = datetime.now(timezone.utc).isoformat()
        payload["cache_hit"] = False
        with _manual_risk_cache_lock:
            _manual_risk_cache.update({"key": cache_key, "at": time.time(), "payload": payload})
        return _ok(payload)
    except Exception as e:
        return _err("portfolio_risk_dashboard_manual", e)


@app.get("/api/sectors", response_model=ApiResponse)
def sectors() -> ApiResponse:
    try:
        global _sectors_future
        with _sectors_lock:
            fut = _sectors_future
            if fut is None:
                fut = _sectors_executor.submit(_compute_sector_heatmap)
                fut.add_done_callback(_on_sectors_done)
                _sectors_future = fut
        try:
            data = fut.result(timeout=_SECTORS_TIME_BUDGET_SEC)
            return _ok(data)
        except FutureTimeoutError:
            # Don't hang the request. Serve last-good data (flagged stale) if we
            # have it; otherwise return an honest, actionable degraded error.
            cached = _sectors_cache.get("data")
            if isinstance(cached, dict):
                payload = dict(cached)
                payload["stale"] = True
                payload["as_of"] = _sectors_cache.get("at")
                payload["degraded_reason"] = (
                    "Live sector data is slow to refresh (Schwab market data may be "
                    "degraded). Showing the last known values; it will update automatically."
                )
                return _ok(payload)
            return ApiResponse(
                ok=False,
                error=(
                    "Sector data is taking too long to load, which usually means Schwab "
                    "market-data quotes are unavailable (often a Market Data entitlement "
                    "issue). It will refresh automatically once market data recovers."
                ),
            )
    except Exception as e:
        return _err("sectors", e)


def _cockpit_sector_lookup() -> Any:
    try:
        from sector_strength import get_ticker_sector_etf

        return lambda t: get_ticker_sector_etf(t, skill_dir=SKILL_DIR)
    except Exception:
        return None


def _cockpit_stop_lookup() -> Any:
    """Return ticker -> bool(has registered stop) from exit-manager state."""
    try:
        from execution_persistence import _load_exit_manager_state

        state = _load_exit_manager_state(SKILL_DIR)
        positions = state.get("positions", {}) if isinstance(state, dict) else {}
        registered = {str(k).upper() for k in positions.keys()}
        return lambda t: str(t).upper() in registered
    except Exception:
        return None


def _extract_bid_ask(quote: Any) -> tuple[float | None, float | None]:
    if not isinstance(quote, dict):
        return None, None
    inner = quote.get("quote") if isinstance(quote.get("quote"), dict) else quote
    bid = inner.get("bidPrice", inner.get("bid"))
    ask = inner.get("askPrice", inner.get("ask"))
    try:
        bid = float(bid) if bid is not None else None
    except (TypeError, ValueError):
        bid = None
    try:
        ask = float(ask) if ask is not None else None
    except (TypeError, ValueError):
        ask = None
    return bid, ask


@app.get("/cockpit")
def cockpit_page() -> RedirectResponse:
    """Legacy standalone cockpit page, now a screen of the main dashboard."""
    return RedirectResponse("/?screen=research#cockpitSection", status_code=302)


@app.get("/api/cockpit/market", response_model=ApiResponse)
def cockpit_market(db: Session = Depends(get_db)) -> ApiResponse:
    try:
        from core import cockpit_service

        snapshot = _scan_snapshot()
        diagnostics = snapshot.get("diagnostics")
        if not isinstance(diagnostics, dict):
            last_scan = _load_state(db, "last_scan", {})
            diagnostics = (last_scan or {}).get("diagnostics") if isinstance(last_scan, dict) else None
        diag = diagnostics if isinstance(diagnostics, dict) else {}
        try:
            from core.hmm_regime import load_spy_hmm_view

            hmm = load_spy_hmm_view(SKILL_DIR, diag)
        except Exception as exc:
            LOG.warning("HMM regime view failed: %s", exc)
            hmm = None
        return _ok(cockpit_service.build_market(diag, hmm_regime=hmm))
    except Exception as e:
        return _err("cockpit_market", e)


@app.get("/api/cockpit/opportunities", response_model=ApiResponse)
def cockpit_opportunities(
    include_filtered: bool = True,
    limit: int | None = None,
) -> ApiResponse:
    try:
        from core import cockpit_service

        snapshot = _scan_snapshot()
        cards = cockpit_service.build_opportunities(
            snapshot.get("signals"),
            shortlist=snapshot.get("shortlist_signals"),
            skill_dir=SKILL_DIR,
            include_filtered=include_filtered,
            limit=limit,
        )
        return _ok({"opportunities": cards, "count": len(cards)})
    except Exception as e:
        return _err("cockpit_opportunities", e)


@app.get("/api/cockpit/portfolio", response_model=ApiResponse)
def cockpit_portfolio() -> ApiResponse:
    try:
        from core import cockpit_service

        auth = get_shared_auth()
        status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
        if isinstance(status_data, str):
            _record_endpoint_error("cockpit_portfolio")
            return ApiResponse(ok=False, error=status_data)
        return _ok(
            cockpit_service.build_portfolio(
                status_data,
                sector_lookup=_cockpit_sector_lookup(),
                stop_lookup=_cockpit_stop_lookup(),
                skill_dir=SKILL_DIR,
            )
        )
    except Exception as e:
        return _err("cockpit_portfolio", e)


@app.get("/api/cockpit/blotter", response_model=ApiResponse)
def cockpit_blotter(db: Session = Depends(get_db)) -> ApiResponse:
    try:
        from core import cockpit_service

        rows = (
            db.query(PendingTrade)
            .filter(PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
            .order_by(PendingTrade.created_at.desc())
            .limit(100)
            .all()
        )
        blotter = cockpit_service.build_blotter([_trade_to_dict(r) for r in rows])
        return _ok({"blotter": blotter, "count": len(blotter)})
    except Exception as e:
        return _err("cockpit_blotter", e)


@app.get("/api/cockpit/decision-packets", response_model=ApiResponse)
def cockpit_decision_packets(limit: int = 50) -> ApiResponse:
    """Recent decision packets (the unit of post-trade evaluation)."""
    try:
        from core import decision_packet

        packets = decision_packet.load_packets(SKILL_DIR, limit=max(1, min(500, int(limit))))
        return _ok({"packets": packets, "count": len(packets)})
    except Exception as e:
        return _err("cockpit_decision_packets", e)


@app.get("/api/cockpit/review", response_model=ApiResponse)
def cockpit_review() -> ApiResponse:
    """Weekly learning diagnostics + advisory tuning proposals."""
    try:
        from core import decision_packet, packet_feature_analysis, trade_review, weight_feedback

        packets = decision_packet.load_packets(SKILL_DIR)
        report = trade_review.weekly_report(packets)
        report["feature_lift"] = packet_feature_analysis.feature_lift_report(packets)
        report["tuning_proposals"] = weight_feedback.propose(report)
        return _ok(report)
    except Exception as e:
        return _err("cockpit_review", e)


@app.post("/api/cockpit/review/backfill", response_model=ApiResponse)
def cockpit_review_backfill(
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse:
    """Resolve matured decision packets at the configured trading-day horizon (default 10)."""
    try:
        from core import outcome_backfill

        return _ok(outcome_backfill.run_local_backfill(SKILL_DIR, force=True))
    except Exception as e:
        return _err("cockpit_review_backfill", e)


@app.get("/api/cockpit/execution/quality", response_model=ApiResponse)
def cockpit_execution_quality(db: Session = Depends(get_db)) -> ApiResponse:
    """Execution-quality attribution: lifecycle counts, slippage, policy events."""
    try:
        from core import cockpit_service
        from execution_persistence import get_execution_safety_summary

        rows = (
            db.query(PendingTrade)
            .filter(PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
            .order_by(PendingTrade.created_at.desc())
            .limit(100)
            .all()
        )
        blotter = cockpit_service.build_blotter([_trade_to_dict(r) for r in rows])
        summary = get_execution_safety_summary(skill_dir=SKILL_DIR, days=7)
        return _ok(cockpit_service.build_execution_quality(summary, blotter))
    except Exception as e:
        return _err("cockpit_execution_quality", e)


@app.get("/api/cockpit/shadow-scoreboard", response_model=ApiResponse)
def cockpit_shadow_scoreboard(db: Session = Depends(get_db)) -> ApiResponse:
    """Would-have counters + plugin mode workbench (roster, gaps, write flags)."""
    try:
        snapshot = _scan_snapshot()
        diagnostics = snapshot.get("diagnostics")
        scan_at = None
        if not isinstance(diagnostics, dict) or not diagnostics:
            last_scan = _load_state(db, "last_scan", {})
            if isinstance(last_scan, dict):
                diagnostics = last_scan.get("diagnostics")
                scan_at = last_scan.get("at")
        payload = build_shadow_scoreboard_payload(
            skill_dir=SKILL_DIR,
            diagnostics=diagnostics if isinstance(diagnostics, dict) else {},
            scan_at=scan_at,
            writes_enabled=True,
        )
        return _ok(payload)
    except Exception as e:
        return _err("cockpit_shadow_scoreboard", e)


@app.post("/api/cockpit/shadow-scoreboard/mode", response_model=ApiResponse)
def cockpit_shadow_scoreboard_set_mode(
    body: PluginModeWriteRequest,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse:
    """Local-only: upsert plugin mode in .env, soft-reload, verify; LIVE mints ledger."""
    try:
        from core.plugin_mode_workbench import set_plugin_mode

        result = set_plugin_mode(
            skill_dir=SKILL_DIR,
            plugin_id=body.plugin_id,
            mode=body.mode,
            reason=body.reason,
            confirm_phrase=body.confirm_phrase,
            api_key=body.api_key,
            confirm_demote=bool(body.confirm_demote),
            writes_enabled=True,
        )
        return _ok(result)
    except PermissionError as e:
        return ApiResponse(ok=False, data=None, error=str(e))
    except KeyError as e:
        return ApiResponse(ok=False, data=None, error=str(e))
    except ValueError as e:
        return ApiResponse(ok=False, data=None, error=str(e))
    except RuntimeError as e:
        return ApiResponse(ok=False, data=None, error=str(e))
    except Exception as e:
        return _err("cockpit_shadow_scoreboard_set_mode", e)


@app.get("/api/cockpit/deltas", response_model=ApiResponse)
def cockpit_deltas(db: Session = Depends(get_db)) -> ApiResponse:
    """What changed since the previous scan cycle."""
    try:
        from core import cockpit_service

        snapshot = _scan_snapshot()
        curr = {"signals": snapshot.get("signals")} if snapshot.get("signals") else _load_state(db, "last_scan", {})
        prev = _load_state(db, "prev_scan", {})
        return _ok(cockpit_service.build_deltas(prev, curr))
    except Exception as e:
        return _err("cockpit_deltas", e)


@app.get("/api/cockpit/watchlists", response_model=ApiResponse)
def cockpit_watchlists(db: Session = Depends(get_db)) -> ApiResponse:
    """Adaptive watchlists: breaking out now / setup improving / risk rising."""
    try:
        from core import cockpit_service

        snapshot = _scan_snapshot()
        curr = {"signals": snapshot.get("signals")} if snapshot.get("signals") else _load_state(db, "last_scan", {})
        prev = _load_state(db, "prev_scan", {})
        return _ok(cockpit_service.build_watchlists(prev, curr, skill_dir=SKILL_DIR))
    except Exception as e:
        return _err("cockpit_watchlists", e)


@app.get("/api/cockpit/movers", response_model=ApiResponse)
def cockpit_movers(index: str = "$SPX") -> ApiResponse:
    """Market movers / internals (Schwab /movers). Flag-gated: MARKET_MOVERS_MODE."""
    try:
        from core import cockpit_service
        from market_data import get_market_movers_with_status

        auth = get_shared_auth()
        payload, meta = get_market_movers_with_status(index, auth=auth, skill_dir=SKILL_DIR)
        if payload is None:
            return _ok({"movers": {"gainers": [], "losers": [], "most_active": []}, "meta": meta})
        return _ok({"movers": cockpit_service.build_movers(payload), "meta": meta})
    except Exception as e:
        return _err("cockpit_movers", e)


@app.get("/api/cockpit/symbol/{ticker}/options", response_model=ApiResponse)
def cockpit_symbol_options(ticker: str) -> ApiResponse:
    """Options-chain intelligence for one symbol. Flag-gated: OPTIONS_INTEL_MODE."""
    try:
        from core import cockpit_service
        from market_data import get_options_chain_with_status

        symbol = ticker.upper().strip()
        auth = get_shared_auth()
        chain, meta = get_options_chain_with_status(symbol, auth=auth, skill_dir=SKILL_DIR)
        if chain is None:
            return _ok({"ticker": symbol, "options_intel": None, "meta": meta})
        return _ok({"ticker": symbol, "options_intel": cockpit_service.build_symbol_options(chain), "meta": meta})
    except Exception as e:
        return _err("cockpit_symbol_options", e)


@app.post("/api/cockpit/order-intent/preview", response_model=ApiResponse)
def cockpit_order_intent_preview(payload: CreatePendingTrade) -> ApiResponse:
    """Read-only order-intent preview (no broker POST). Approval still goes
    through /api/pending-trades + /api/trades/{id}/approve."""
    try:
        from core import cockpit_service

        symbol = payload.ticker.upper().strip()
        signal = payload.signal or {}
        if not signal:
            db = SessionLocal()
            try:
                resolved = _resolve_local_scan_signal(db, symbol)
            finally:
                db.close()
            if resolved:
                signal = resolved

        bid = ask = None
        quote_age_sec = None
        try:
            auth = get_shared_auth()
            quote = get_current_quote(symbol, auth=auth, skill_dir=SKILL_DIR)
            bid, ask = _extract_bid_ask(quote)
        except Exception:
            quote = None

        preview = cockpit_service.build_order_intent_preview(
            ticker=symbol,
            qty=payload.qty,
            price=payload.price,
            signal=signal,
            bid=bid,
            ask=ask,
            quote_age_sec=quote_age_sec,
            skill_dir=SKILL_DIR,
        )
        return _ok(preview)
    except Exception as e:
        return _err("cockpit_order_intent_preview", e)


@app.get("/api/pending-trades", response_model=ApiResponse)
def list_pending_trades(
    status: str | None = None,
    sort: str = "newest",
    db: Session = Depends(get_db),
) -> ApiResponse:
    rows_query = db.query(PendingTrade).filter(PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
    if status and status.lower() != "all":
        rows_query = rows_query.filter(PendingTrade.status == status.lower().strip())
    if sort == "oldest":
        rows_query = rows_query.order_by(PendingTrade.created_at.asc())
    else:
        rows_query = rows_query.order_by(PendingTrade.created_at.desc())
    rows = rows_query.all()
    return _ok([_trade_to_dict(r) for r in rows])


@app.post("/api/pending-trades", response_model=ApiResponse)
def create_pending_trade(
    payload: CreatePendingTrade,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    try:
        ticker = payload.ticker.upper().strip()
        signal = payload.signal or {}

        auth = get_shared_auth()
        quote = get_current_quote(ticker, auth=auth, skill_dir=SKILL_DIR)
        last_price = payload.price or extract_schwab_last_price(quote) or float(signal.get("price", 0) or 0)

        qty = payload.qty
        if qty is None:
            usd_size = get_position_size_usd(
                ticker=ticker,
                price=last_price if last_price > 0 else None,
                skill_dir=SKILL_DIR,
            )
            try:
                pm_mult = float(signal.get("prediction_market_size_multiplier") or 1.0)
            except (TypeError, ValueError):
                pm_mult = 1.0
            pm_mult = max(0.85, min(1.15, pm_mult))
            usd_size = int(round(float(usd_size) * pm_mult))
            qty = max(1, int(usd_size / last_price)) if last_price > 0 else 1

        trade_id = uuid.uuid4().hex[:8]
        row = PendingTrade(
            id=trade_id,
            user_id=LOCAL_DASHBOARD_USER_ID,
            ticker=ticker,
            qty=qty,
            price=last_price if last_price > 0 else None,
            status="pending",
            signal_json=json.dumps(signal, default=_json_default),
            note=payload.note,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        _audit_event("pending_trade_created", "system", {"trade_id": trade_id, "ticker": ticker, "qty": qty})
        _sse_publish("trade_created", {"trade_id": trade_id, "ticker": ticker, "qty": qty, "status": "pending"})
        return _ok(_trade_to_dict(row))
    except Exception as e:
        return _err("create_pending_trade", e)


@app.post("/api/pending-trades/clear-pending", response_model=ApiResponse)
def clear_all_pending_trades(
    auth_ctx: dict[str, str] = Depends(require_trade_api_key),
    db: Session = Depends(get_db),
) -> ApiResponse:
    rows = (
        db.query(PendingTrade)
        .filter(PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID, PendingTrade.status == "pending")
        .all()
    )
    cleared_ids = [r.id for r in rows]
    for row in rows:
        row.status = "rejected"
    db.commit()
    actor = auth_ctx.get("actor", "web-user")
    if cleared_ids:
        _audit_event(
            "pending_trades_cleared",
            actor,
            {"cleared": len(cleared_ids), "trade_ids": cleared_ids},
        )
    return _ok({"cleared": len(cleared_ids)})


@app.post("/api/pending-trades/delete-all", response_model=ApiResponse)
def delete_all_pending_trades(
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    """Permanently remove all trades (executed/rejected/failed/pending) from the history."""
    rows = db.query(PendingTrade).filter(PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID).all()
    deleted_ids = [r.id for r in rows]
    status_breakdown: dict[str, int] = {}
    for row in rows:
        status_breakdown[row.status] = status_breakdown.get(row.status, 0) + 1
        db.delete(row)
    db.commit()
    if deleted_ids:
        _audit_event(
            "pending_trades_deleted_all",
            "web-user",
            {
                "deleted": len(deleted_ids),
                "trade_ids": deleted_ids,
                "by_status": status_breakdown,
            },
        )
    return _ok({"deleted": len(deleted_ids), "by_status": status_breakdown})


@app.post("/api/trades/{trade_id}/delete", response_model=ApiResponse)
def delete_trade(
    trade_id: str,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    row = (
        db.query(PendingTrade)
        .filter(PendingTrade.id == trade_id, PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
        .first()
    )
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    db.delete(row)
    db.commit()
    _audit_event("trade_deleted", "web-user", {"trade_id": trade_id})
    return _ok({"deleted": trade_id})


@app.post("/api/trades/{trade_id}/approve", response_model=ApiResponse)
def approve_trade(
    trade_id: str,
    payload: ApproveTradeRequest,
    confirm_live: bool = False,
    auth_ctx: dict[str, str] = Depends(require_trade_api_key),
    db: Session = Depends(get_db),
) -> ApiResponse:
    if (os.getenv("LIVE_TRADING_KILL_SWITCH") or "").strip().lower() in ("1", "true", "yes", "on"):
        return ApiResponse(
            ok=False,
            error="Platform kill switch is enabled. New live orders are blocked until cleared.",
        )
    row = (
        db.query(PendingTrade)
        .filter(PendingTrade.id == trade_id, PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
        .first()
    )
    if not row:
        _record_endpoint_error("approve_trade")
        return ApiResponse(ok=False, error="Trade not found.")
    if row.status != "pending":
        _record_endpoint_error("approve_trade")
        return ApiResponse(ok=False, error=f"Trade already {row.status}.")

    typed = (payload.typed_ticker or "").strip().upper()
    if typed != row.ticker.upper():
        _record_endpoint_error("approve_trade")
        return ApiResponse(
            ok=False,
            error="typed_ticker must exactly match the staged trade ticker (re-type to confirm the live order).",
        )

    signal = _coerce_json_dict(row.signal_json)
    telemetry = _build_standard_telemetry(signal if isinstance(signal, dict) else {})
    ui_settings = _load_ui_settings(db)
    automation_opt_in = bool(ui_settings.get("automation_opt_in", DEFAULT_AUTOMATION_OPT_IN))
    if not automation_opt_in and not confirm_live:
        checklist = _build_pretrade_checklist(row, signal)
        return ApiResponse(
            ok=False,
            error="Explicit live confirmation required. Review checklist and retry with confirm_live=true.",
            data={"checklist": checklist, "automation_opt_in": automation_opt_in},
        )

    result = place_order(
        ticker=row.ticker,
        qty=row.qty,
        side="BUY",
        order_type="MARKET",
        price_hint=row.price,
        mirofish_conviction=signal.get("mirofish_conviction"),
        advisory_prob=(signal.get("advisory") or {}).get("p_up_10d"),
        agent_uncertainty=(signal.get("meta_policy") or {}).get("uncertainty_score"),
        vcp_volume_ratio=(signal.get("score_components") or {}).get("avg_vcp_volume_ratio"),
        sector_rs_rank=signal.get("sector_rs_rank", signal.get("sector_relative_strength_rank")),
        sector_etf=signal.get("sector_etf"),
        skill_dir=SKILL_DIR,
    )

    actor = auth_ctx.get("actor", "web-user")
    if isinstance(result, str):
        row.status = "failed"
        row.note = (row.note or "") + f" | {result}" if row.note else result
        db.commit()
        db.refresh(row)
        _audit_event(
            "trade_approve_failed",
            actor,
            {"trade": _trade_to_dict(row), "error": result},
        )
        _record_endpoint_error("approve_trade")
        _sse_publish("trade_failed", {"trade_id": trade_id, "ticker": row.ticker, "error": result})
        _save_state(
            db,
            "last_trade_approval",
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "trade_id": trade_id,
                "ticker": row.ticker,
                "status": "failed",
                "telemetry": telemetry,
                "error": result,
            },
        )
        return ApiResponse(
            ok=False,
            error=result,
            data={
                "trade": _trade_to_dict(row),
                "recovery": _map_failure(result, source="execution"),
            },
        )

    row.status = "executed"
    # Phase 4 learning loop: snapshot this decision into a packet for later
    # outcome attribution. Additive + guarded — never affects the trade.
    try:
        from core import cockpit_service, decision_packet
        from core.providers import ExecutionProvider

        _ls = _load_state(db, "last_scan", {})
        _market = cockpit_service.build_market((_ls or {}).get("diagnostics") or {})
        _execu = ExecutionProvider.from_order_result(result if isinstance(result, dict) else {}).model_dump(mode="json")
        _packet = decision_packet.build_packet(
            ticker=row.ticker,
            kind="approved",
            signal=signal if isinstance(signal, dict) else {},
            market=_market,
            execution=_execu,
        )
        decision_packet.record_packet(SKILL_DIR, _packet)
    except Exception as _pkt_exc:
        logging.getLogger(__name__).debug("decision packet record skipped: %s", _pkt_exc)
    _save_state(
        db,
        "last_trade_approval",
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "trade_id": trade_id,
            "ticker": row.ticker,
            "status": "executed",
            "telemetry": telemetry,
            "result": result,
        },
    )
    db.commit()
    db.refresh(row)
    _audit_event(
        "trade_approved",
        actor,
        {"trade": _trade_to_dict(row), "result": result},
    )
    _sse_publish("trade_approved", {"trade_id": trade_id, "ticker": row.ticker})
    return _ok({"trade": _trade_to_dict(row), "result": result})


@app.post("/api/trades/{trade_id}/reject", response_model=ApiResponse)
def reject_trade(
    trade_id: str,
    auth_ctx: dict[str, str] = Depends(require_trade_api_key),
    db: Session = Depends(get_db),
) -> ApiResponse:
    row = (
        db.query(PendingTrade)
        .filter(PendingTrade.id == trade_id, PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
        .first()
    )
    if not row:
        _record_endpoint_error("reject_trade")
        return ApiResponse(ok=False, error="Trade not found.")
    if row.status != "pending":
        _record_endpoint_error("reject_trade")
        return ApiResponse(ok=False, error=f"Trade already {row.status}.")
    row.status = "rejected"
    db.commit()
    db.refresh(row)
    _audit_event("trade_rejected", auth_ctx.get("actor", "web-user"), {"trade": _trade_to_dict(row)})
    _sse_publish("trade_rejected", {"trade_id": trade_id, "ticker": row.ticker})
    return _ok(_trade_to_dict(row))


@app.get("/api/trades/{trade_id}/preflight", response_model=ApiResponse)
def preflight_trade(trade_id: str, db: Session = Depends(get_db)) -> ApiResponse:
    row = (
        db.query(PendingTrade)
        .filter(PendingTrade.id == trade_id, PendingTrade.user_id == LOCAL_DASHBOARD_USER_ID)
        .first()
    )
    if not row:
        return ApiResponse(ok=False, error="Trade not found.")
    signal = _coerce_json_dict(row.signal_json)
    return _ok(
        {
            "trade": _trade_to_dict(row),
            "checklist": _build_pretrade_checklist(row, signal if isinstance(signal, dict) else {}),
        }
    )


@app.get("/api/recovery/map", response_model=ApiResponse)
def map_recovery(error: str, source: str = "unknown") -> ApiResponse:
    return _ok(_map_failure(error, source=source))


@app.get("/api/settings/profiles", response_model=ApiResponse)
def get_profiles(expert: bool = False, db: Session = Depends(get_db)) -> ApiResponse:
    settings = _load_ui_settings(db)
    profile = str(settings.get("profile", DEFAULT_PROFILE)).strip().lower()
    profile = profile if profile in PRESET_PROFILES else DEFAULT_PROFILE
    active = dict(PRESET_PROFILES.get(profile, {}))
    payload: dict[str, Any] = {
        "mode": settings.get("mode", DEFAULT_UI_MODE),
        "profile": profile,
        "automation_opt_in": bool(settings.get("automation_opt_in", DEFAULT_AUTOMATION_OPT_IN)),
        "profiles": sorted(PRESET_PROFILES.keys()),
        "active_profile_settings": active,
    }
    if expert:
        payload["expert_runtime_overrides"] = {k: os.environ.get(k) for k in sorted(active.keys())}
    payload["preset_catalog"] = build_preset_catalog_payload()
    return _ok(payload)


@app.post("/api/settings/profile", response_model=ApiResponse)
def set_profile(
    profile: str = DEFAULT_PROFILE,
    mode: str = DEFAULT_UI_MODE,
    automation_opt_in: bool = False,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    p = str(profile or DEFAULT_PROFILE).strip().lower()
    if p not in PRESET_PROFILES:
        return ApiResponse(ok=False, error=f"Invalid profile '{profile}'.")
    mode_n = str(mode or DEFAULT_UI_MODE).strip().lower()
    if mode_n not in {"standard", "expert"}:
        return ApiResponse(ok=False, error="Invalid mode. Use standard or expert.")
    runtime = _apply_profile_to_runtime(p)
    settings = {
        "mode": mode_n,
        "profile": p,
        "automation_opt_in": bool(automation_opt_in),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(db, "ui_settings", settings)
    _audit_event(
        "settings_profile_applied",
        "web-user",
        {"profile": p, "mode": mode_n, "automation_opt_in": bool(automation_opt_in)},
    )
    return _ok({"settings": settings, "runtime_overrides": runtime})


@app.post("/api/onboarding/start", response_model=ApiResponse)
def onboarding_start(
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    state = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "target_minutes": ONBOARDING_TARGET_MINUTES,
        "steps": {
            "connect": {"ok": False, "at": None},
            "verify_token_health": {"ok": False, "at": None},
            "test_scan": {"ok": False, "at": None},
            "test_paper_order": {"ok": False, "at": None},
        },
    }
    _save_state(db, "onboarding", state)
    return _ok(state)


@app.post("/api/onboarding/step/{step}", response_model=ApiResponse)
def onboarding_step(
    step: str,
    _auth: dict[str, str] = Depends(require_api_key_if_set),
    db: Session = Depends(get_db),
) -> ApiResponse:
    current = _load_state(
        db,
        key="onboarding",
        default={
            "started_at": datetime.now(timezone.utc).isoformat(),
            "target_minutes": ONBOARDING_TARGET_MINUTES,
            "steps": {},
        },
    )
    steps = current.setdefault("steps", {})
    step_key = str(step or "").strip().lower()
    now_iso = datetime.now(timezone.utc).isoformat()

    if step_key == "connect":
        health = _token_health()
        ok = bool(health["market_token_file"] and health["account_token_file"])
        steps["connect"] = {
            "ok": ok,
            "at": now_iso,
            "details": health,
            "fix_path": "Run `python run_auth.py` (or dual auth flow), then rerun this step.",
        }
    elif step_key == "verify_token_health":
        try:
            auth = get_shared_auth()
            market_ok = bool(auth.get_market_token())
            account_ok = bool(auth.get_account_token())
            quote = get_current_quote("AAPL", auth=auth, skill_dir=SKILL_DIR)
            quote_ok = extract_schwab_last_price(quote) is not None
            ok = market_ok and account_ok and quote_ok
            steps["verify_token_health"] = {
                "ok": ok,
                "at": now_iso,
                "details": {
                    "market_token_ok": market_ok,
                    "account_token_ok": account_ok,
                    "quote_ok": quote_ok,
                },
                "fix_path": "Run `python healthcheck.py` and follow repair steps if checks fail.",
            }
        except Exception as e:
            steps["verify_token_health"] = {
                "ok": False,
                "at": now_iso,
                "details": {"error": str(e)},
                "recovery": _map_failure(str(e), source="schwab_auth"),
            }
    elif step_key == "test_scan":
        try:
            scan_out = run_scan(skill_dir=SKILL_DIR)
            signals = scan_out.signals
            diagnostics = scan_out.diagnostics
            ok = diagnostics.get("scan_blocked", 0) == 0 and diagnostics.get("exceptions", 0) == 0
            steps["test_scan"] = {
                "ok": bool(ok),
                "at": now_iso,
                "details": {
                    "signals_found": len(signals),
                    "diagnostics_summary": _diagnostics_summary(diagnostics, signals),
                },
                "fix_path": "Retry scan and review blockers list if no signals are produced.",
            }
        except Exception as e:
            steps["test_scan"] = {
                "ok": False,
                "at": now_iso,
                "details": {"error": str(e)},
                "recovery": _map_failure(str(e), source="signal_scanner"),
            }
    elif step_key == "test_paper_order":
        previous_shadow = os.environ.get("EXECUTION_SHADOW_MODE")
        os.environ["EXECUTION_SHADOW_MODE"] = "1"
        try:
            auth = get_shared_auth()
            quote = get_current_quote("AAPL", auth=auth, skill_dir=SKILL_DIR)
            price = extract_schwab_last_price(quote) or 100.0
            result = place_order(
                ticker="AAPL",
                qty=1,
                side="BUY",
                order_type="MARKET",
                price_hint=price,
                skill_dir=SKILL_DIR,
            )
            ok = isinstance(result, dict) and bool(result.get("shadow_mode"))
            steps["test_paper_order"] = {
                "ok": ok,
                "at": now_iso,
                "details": result if isinstance(result, dict) else {"result": result},
                "fix_path": "Keep execution in shadow mode and retry the paper-order test.",
            }
        except Exception as e:
            steps["test_paper_order"] = {
                "ok": False,
                "at": now_iso,
                "details": {"error": str(e)},
                "recovery": _map_failure(str(e), source="execution"),
            }
        finally:
            if previous_shadow is None:
                os.environ.pop("EXECUTION_SHADOW_MODE", None)
            else:
                os.environ["EXECUTION_SHADOW_MODE"] = previous_shadow
    else:
        return ApiResponse(ok=False, error="Unknown onboarding step.")

    _save_state(db, "onboarding", current)
    return _ok(current)


def _local_api_health_snapshot() -> dict[str, Any]:
    """Schwab connectivity snapshot for onboarding (mirrors SaaS api_health shape)."""
    auth = get_shared_auth()
    try:
        schwab_token_health = auth.get_token_health()
    except Exception as exc:
        schwab_token_health = {
            "status": "unknown",
            "market": {"status": "unknown"},
            "account": {"status": "unknown"},
            "error": str(exc)[:200],
        }
    market_health = str((schwab_token_health.get("market") or {}).get("status") or "")
    account_health = str((schwab_token_health.get("account") or {}).get("status") or "")
    market_token_ok, market_state = _session_connection_state(_session_token_ok(auth.market_session), market_health)
    account_token_ok, account_state = _session_connection_state(_session_token_ok(auth.account_session), account_health)
    linked = bool(market_token_ok or account_token_ok)
    quote_ok = False
    if linked:
        try:
            quote, _qmeta = get_current_quote_with_status("AAPL", auth=auth, skill_dir=SKILL_DIR)
            quote_ok = extract_schwab_last_price(quote) is not None
        except Exception:
            quote_ok = False
    return {
        "schwab_linked": linked,
        "market_token_ok": market_token_ok,
        "account_token_ok": account_token_ok,
        "market_state": market_state,
        "account_state": account_state,
        "quote_ok": quote_ok,
        "token_health": schwab_token_health,
    }


@app.get("/api/onboarding/status", response_model=ApiResponse)
def onboarding_status(db: Session = Depends(get_db)) -> ApiResponse:
    current = _load_state(
        db,
        key="onboarding",
        default={
            "started_at": None,
            "target_minutes": ONBOARDING_TARGET_MINUTES,
            "steps": {
                "connect": {"ok": False},
                "verify_token_health": {"ok": False},
                "test_scan": {"ok": False},
                "test_paper_order": {"ok": False},
            },
        },
    )
    started_at = current.get("started_at")
    elapsed_minutes = None
    if isinstance(started_at, str) and started_at:
        try:
            dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            elapsed_minutes = round((datetime.now(timezone.utc) - dt).total_seconds() / 60.0, 1)
        except Exception:
            elapsed_minutes = None
    _st = current.get("steps")
    steps: dict[str, Any] = _st if isinstance(_st, dict) else {}
    completion = (
        bool((steps.get("connect") or {}).get("ok"))
        and bool((steps.get("verify_token_health") or {}).get("ok"))
        and bool((steps.get("test_scan") or {}).get("ok"))
        and bool((steps.get("test_paper_order") or {}).get("ok"))
    )
    api_health = _local_api_health_snapshot()
    return _ok(
        {
            **current,
            "elapsed_minutes": elapsed_minutes,
            "target_minutes": current.get("target_minutes", ONBOARDING_TARGET_MINUTES),
            "completed_under_target": bool(
                completion and elapsed_minutes is not None and elapsed_minutes <= ONBOARDING_TARGET_MINUTES
            ),
            "api_health": api_health,
            "connection_status": "connected" if api_health.get("schwab_linked") else "disconnected",
            "schwab_linked": bool(api_health.get("schwab_linked")),
        }
    )


@app.get("/api/decision-card/{ticker}", response_model=ApiResponse)
def decision_card(ticker: str, db: Session = Depends(get_db)) -> ApiResponse:
    symbol = ticker.upper().strip()
    # Prefer kept/live-ranked signals, then shortlist, then last_scan / ScanResult
    # so Evaluate setup still works after a dashboard restart (table hydrates
    # from last_scan while ``_scan_job`` is idle).
    signal = _resolve_local_scan_signal(db, symbol)
    if signal is None:
        return ApiResponse(ok=False, error=f"{symbol} is not in current scan results. Run scan first.")

    from .decision_card_service import build_decision_card_payload

    price = float(signal.get("price", 0) or 0)
    size_usd = get_position_size_usd(ticker=symbol, price=price if price > 0 else None, skill_dir=SKILL_DIR)
    qty = max(1, int(size_usd / price)) if price > 0 else 1
    mock_trade = PendingTrade(
        id="preview", ticker=symbol, qty=qty, price=price, status="pending", signal_json=json.dumps(signal), note=None
    )
    checklist = _build_pretrade_checklist(mock_trade, signal)
    return _ok(
        build_decision_card_payload(
            signal,
            ticker=symbol,
            skill_dir=SKILL_DIR,
            checklist=checklist,
            size_usd=size_usd,
        )
    )
