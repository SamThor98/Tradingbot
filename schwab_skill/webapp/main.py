# ruff: noqa: E402
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
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

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
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
from .checklist_language import with_plain_language
from .cors_config import build_allowed_origins
from .db import DATABASE_URL, Base, SessionLocal, engine
from .models import AppState, PendingTrade, ScanResult, User
from .oauth_schwab import exchange_schwab_code_for_tokens, schwab_authorize_url
from .preset_catalog import PRESET_PROFILES, build_preset_catalog_payload
from .recovery_map import map_failure as _map_failure
from .response_helpers import api_err, json_default
from .route_helpers import (
    apply_profile_to_runtime as _shared_apply_profile_to_runtime,
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
from .routes.learning import router as learning_router
from .routes.research import router as research_router
from .scan_payload import parse_scan_run_body, scan_runtime_kwargs
from .schemas import ApiResponse, ApproveTradeRequest, CreatePendingTrade
from .security_headers import SecurityHeadersMiddleware
from .static_assets import NoCacheStaticFiles, render_versioned_html

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
    lifetime of the process. Never call ``.close()`` on the returned instance
    from a request handler — that would stop the shared refresh threads.
    """
    global _shared_auth
    with _shared_auth_lock:
        if _shared_auth is None:
            _shared_auth = DualSchwabAuth(skill_dir=SKILL_DIR)
        return _shared_auth


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


def _run_alembic_upgrade_head_for_sqlite() -> None:
    """Apply Alembic revisions so existing SQLite files gain new columns (e.g. Stripe billing)."""
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
_validate_startup_configuration()

@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    """App lifespan: stop the shared auth's background refresh threads on shutdown.

    Daemon threads exit with the process, but closing explicitly avoids a brief
    orphan-thread window across ``--reload`` restarts in development.
    """
    yield
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
app.include_router(learning_router)


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
_SCAN_STALE_SECONDS = max(60, int(os.getenv("WEB_SCAN_STALE_SECONDS", "1800") or 1800))
_scan_lock = threading.Lock()
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
        (os.getenv("SAAS_FRONTEND_URL") or "").strip()
        or (os.getenv("WEB_PUBLIC_ORIGIN") or "").strip()
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


def _parse_iso_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _expire_stale_scan_job_locked() -> bool:
    """Fail scans that exceeded watchdog runtime to avoid permanent 'running' state."""
    if str(_scan_job.get("status") or "") != "running":
        return False
    started_dt = _parse_iso_datetime(_scan_job.get("started_at"))
    if started_dt is None:
        return False
    elapsed = (datetime.now(timezone.utc) - started_dt).total_seconds()
    if elapsed <= float(_SCAN_STALE_SECONDS):
        return False
    _scan_job.update(
        {
            "status": "failed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": (
                "Scan watchdog timeout: exceeded "
                f"{_SCAN_STALE_SECONDS}s runtime ({int(elapsed)}s elapsed)."
            ),
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
    return out


def _latest_validation_status() -> dict[str, Any]:
    status_file = VALIDATION_ARTIFACT_DIR / "continuous_validation_status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                latest_artifacts = data.get("latest_artifacts") or {}
                return {
                    "source": "continuous_validation_status",
                    "exists": True,
                    "run_status": data.get("run_status"),
                    "passed": bool(data.get("passed")) if data.get("passed") is not None else None,
                    "started_at": data.get("started_at"),
                    "finished_at": data.get("finished_at"),
                    "generated_at": data.get("generated_at"),
                    "current_step": data.get("current_step"),
                    "current_step_index": data.get("current_step_index"),
                    "completed_steps": data.get("completed_steps"),
                    "total_steps": data.get("total_steps"),
                    "progress_pct": data.get("progress_pct"),
                    "failed_steps": list(data.get("failed_steps") or []),
                    "latest_artifacts": latest_artifacts if isinstance(latest_artifacts, dict) else {},
                }
        except Exception:
            pass

    validate_runs = sorted(VALIDATION_ARTIFACT_DIR.glob("validate_all_*.json"))
    if not validate_runs:
        return {
            "source": "none",
            "exists": False,
            "run_status": "idle",
            "passed": None,
            "started_at": None,
            "finished_at": None,
            "generated_at": None,
            "current_step": None,
            "current_step_index": 0,
            "completed_steps": 0,
            "total_steps": 0,
            "progress_pct": 0,
            "failed_steps": [],
            "latest_artifacts": {},
        }
    latest = validate_runs[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    failed_steps = list(payload.get("failed_steps") or [])
    generated_at = payload.get("generated_at")
    if not generated_at:
        try:
            generated_at = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            generated_at = None
    try:
        rel_path = str(latest.relative_to(SKILL_DIR))
    except ValueError:
        rel_path = str(latest)
    return {
        "source": "validate_all_summary",
        "exists": True,
        "run_status": "completed",
        "passed": bool(payload.get("passed")) if "passed" in payload else None,
        "started_at": None,
        "finished_at": generated_at,
        "generated_at": generated_at,
        "current_step": None,
        "current_step_index": 0,
        "completed_steps": 0,
        "total_steps": 0,
        "progress_pct": 100,
        "failed_steps": failed_steps,
        "latest_artifacts": {"validate_all": rel_path},
    }


def _latest_ablation_status() -> dict[str, Any]:
    latest_report = VALIDATION_ARTIFACT_DIR / "latest_ablation_report.json"
    report_path: Path | None = latest_report if latest_report.exists() else None
    source = "latest_ablation_report"
    if report_path is None:
        runs = sorted(VALIDATION_ARTIFACT_DIR.glob("ablation_report_*.json"))
        if runs:
            report_path = runs[-1]
            source = "ablation_report_summary"
    if report_path is None:
        return {
            "source": "none",
            "exists": False,
            "generated_at": None,
            "summary": {"variant_count": 0, "pass_count": 0, "fail_count": 0},
            "best": None,
            "top_variants": [],
            "latest_artifacts": {},
        }
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    generated_at = payload.get("generated_at")
    if not generated_at:
        try:
            generated_at = datetime.fromtimestamp(report_path.stat().st_mtime, tz=timezone.utc).isoformat()
        except Exception:
            generated_at = None
    leaderboard = payload.get("leaderboard") if isinstance(payload.get("leaderboard"), list) else []
    summary_raw = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    variant_count = int(summary_raw.get("variant_count") or len(leaderboard))
    pass_count = int(summary_raw.get("pass_count") or 0)
    fail_count = int(summary_raw.get("fail_count") or max(0, variant_count - pass_count))
    best = leaderboard[0] if leaderboard else None
    if not isinstance(best, dict):
        best = None
    top_variants: list[dict[str, Any]] = []
    for row in leaderboard[:5]:
        if not isinstance(row, dict):
            continue
        top_variants.append(
            {
                "variant_id": row.get("variant_id"),
                "pass": row.get("pass"),
                "relative_lift_vs_baseline": row.get("relative_lift_vs_baseline"),
                "ci_relative_lift_lower": row.get("ci_relative_lift_lower"),
                "ci_relative_lift_upper": row.get("ci_relative_lift_upper"),
                "regression_flags": list(row.get("regression_flags") or []),
            }
        )
    try:
        rel_path = str(report_path.relative_to(SKILL_DIR))
    except ValueError:
        rel_path = str(report_path)
    return {
        "source": source,
        "exists": True,
        "generated_at": generated_at,
        "summary": {
            "variant_count": variant_count,
            "pass_count": pass_count,
            "fail_count": fail_count,
        },
        "best": best,
        "top_variants": top_variants,
        "latest_artifacts": {"ablation_report": rel_path},
    }


def _latest_slo_gate_status() -> dict[str, Any]:
    path = VALIDATION_ARTIFACT_DIR / "latest_slo_gate_status.json"
    payload = _read_json_file(path, {})
    if not isinstance(payload, dict):
        payload = {}
    passed_raw = payload.get("passed")
    passed = bool(passed_raw) if isinstance(passed_raw, bool) else None
    failures = payload.get("failures")
    return {
        "exists": path.exists(),
        "checked_at": payload.get("checked_at"),
        "passed": passed,
        "failures": list(failures) if isinstance(failures, list) else [],
    }


def _latest_registry_decision() -> dict[str, Any] | None:
    if not EXPERIMENT_REGISTRY_PATH.exists():
        return None
    try:
        lines = EXPERIMENT_REGISTRY_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        return {
            "recorded_at": row.get("recorded_at"),
            "event_type": row.get("event_type"),
            "target": row.get("target"),
            "decision": row.get("decision"),
            "rationale": list(row.get("rationale") or []),
        }
    return None


def _decision_dashboard_snapshot(db: Session) -> dict[str, Any]:
    validation = _latest_validation_status()
    ablation = _latest_ablation_status()
    slo = _latest_slo_gate_status()
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
    diagnostics_summary = (
        last_scan.get("diagnostics_summary")
        if isinstance(last_scan, dict) and isinstance(last_scan.get("diagnostics_summary"), dict)
        else {}
    )
    strategy_summary = (
        last_scan.get("strategy_summary")
        if isinstance(last_scan, dict) and isinstance(last_scan.get("strategy_summary"), dict)
        else {}
    )
    validation_passed = True if validation.get("passed") is True else False
    ablation_best = ablation.get("best") if isinstance(ablation, dict) else None
    ablation_exists = bool(isinstance(ablation, dict) and ablation.get("exists") is True)
    ablation_passed = bool(
        ablation_exists
        and isinstance(ablation_best, dict)
        and ablation_best.get("pass") is True
    )
    slo_passed = True if slo.get("passed") is True else False
    # Keep release gates backward-compatible: when no ablation artifact exists yet,
    # do not force reliability to at_risk solely due to missing optional output.
    gate_ready = bool(validation_passed and slo_passed and (ablation_passed if ablation_exists else True))
    readiness_checks = [
        {"name": "validation", "passed": validation.get("passed")},
        {"name": "ablation", "passed": ablation_passed if ablation_exists else None},
        {"name": "slo_gate", "passed": slo.get("passed")},
    ]
    if validation.get("run_status") == "running":
        readiness_checks.append({"name": "validation_running", "passed": False})
    latest_decision = _latest_registry_decision()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reliability": {
            "validation_passed": validation.get("passed"),
            "validation_run_status": validation.get("run_status"),
            "slo_gate_passed": slo.get("passed"),
            "slo_failures": list(slo.get("failures") or []),
            "state": "healthy" if gate_ready else "at_risk",
        },
        "strategy_quality": {
            "last_scan_at": last_scan.get("at") if isinstance(last_scan, dict) else None,
            "signals_found": last_scan.get("signals_found") if isinstance(last_scan, dict) else None,
            "dominant_strategy": strategy_summary.get("dominant_live_strategy"),
            "dominant_count": strategy_summary.get("dominant_count"),
            "data_quality": diagnostics_summary.get("data_quality"),
            "scan_blocked": diagnostics_summary.get("scan_blocked"),
            "top_blocker": (
                ((diagnostics_summary.get("top_blockers") or [{}])[0]).get("key")
                if isinstance(diagnostics_summary.get("top_blockers"), list)
                else None
            ),
        },
        "promotion_readiness": {
            "release_gate_ready": gate_ready,
            "checks": readiness_checks,
            "latest_decision": latest_decision,
        },
        "ablation": ablation,
    }


def _strategy_summary(signals: list[dict[str, Any]] | None) -> dict[str, Any]:
    return summarize_live_strategy(signals)


def _diagnostics_summary(diag: dict[str, Any] | None, signals: list[dict[str, Any]] | None) -> dict[str, Any]:
    diagnostics = diag or {}
    blocked_reason_raw = diagnostics.get("scan_blocked_reason")
    blocked_reason = str(blocked_reason_raw).strip() if blocked_reason_raw else None
    blocked_human = None
    if blocked_reason == "bear_regime_spy_below_200sma":
        blocked_human = "Scan blocked by regime gate: SPY is below 200 SMA."

    ranked: list[dict[str, Any]] = []
    for key, raw in diagnostics.items():
        try:
            value = int(raw)
        except Exception:
            continue
        if value <= 0 or key == "watchlist_size":
            continue
        ranked.append(
            {
                "key": key,
                "value": value,
                "severity": "error" if key in {"exceptions", "df_empty"} else "warn",
            }
        )
    ranked.sort(key=lambda x: int(x.get("value") or 0), reverse=True)
    final_count = len(signals or [])
    funnel = _build_funnel_stages(diagnostics, final_count)
    return {
        "scan_blocked": bool(diagnostics.get("scan_blocked")),
        "scan_blocked_reason": blocked_reason,
        "headline": blocked_human,
        "top_blockers": ranked[:5],
        "data_quality": diagnostics.get("data_quality"),
        "data_quality_reasons": list(diagnostics.get("data_quality_reasons") or []),
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
    exceptions = _safe_int(diagnostics.get("exceptions"))

    stage_a_candidates_raw = _safe_int(diagnostics.get("stage_a_candidates"))
    stage_a_shortlisted_raw = _safe_int(diagnostics.get("stage_a_shortlisted"))
    stage_a_pruned = _safe_int(diagnostics.get("stage_a_pruned"))

    primary_provider_filtered = _safe_int(diagnostics.get("primary_provider_filtered"))
    stage_b_exceptions = _safe_int(diagnostics.get("stage_b_exceptions"))
    stage_b_timeouts = _safe_int(diagnostics.get("stage_b_timeouts"))
    self_study_filtered = _safe_int(diagnostics.get("self_study_filtered"))
    quality_gates_filtered = _safe_int(diagnostics.get("quality_gates_filtered"))

    vcp_would_filter = _safe_int(diagnostics.get("stage_a_vcp_would_filter"))
    sector_would_filter = _safe_int(diagnostics.get("stage_a_sector_would_filter"))
    no_sector_would_filter = _safe_int(diagnostics.get("stage_a_no_sector_would_filter"))

    vcp_gate_mode = str(diagnostics.get("scan_vcp_gate_mode") or "").strip().lower() or None
    sector_gate_mode = str(diagnostics.get("scan_sector_gate_mode") or "").strip().lower() or None
    primary_provider_mode = str(diagnostics.get("scan_primary_provider_mode") or "").strip().lower() or None
    quality_gates_mode = str(diagnostics.get("quality_gates_mode") or "").strip().lower() or None

    n_watchlist = watchlist
    n_stage2 = max(0, n_watchlist - stage2_fail)
    n_vcp = max(0, n_stage2 - vcp_fail)
    sector_filtered = no_sector_etf + sector_not_winning
    n_sector = max(0, n_vcp - sector_filtered)
    n_breakout = max(0, n_sector - breakout_not_confirmed - exceptions)

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
            "key": "stage2",
            "label": "Passed Stage 2",
            "value": n_stage2,
            "filtered": stage2_fail,
            "tooltip": (
                "Tickers in a Stage 2 uptrend (above 30-week SMA, proper trend structure). Failures: stage2_fail."
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
            "tooltip": (
                "Survivors of Stage B exceptions/timeouts, self-study min "
                "conviction, and quality gates (forensic, weak breakout volume, "
                "etc.)."
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
            _save_last_scan(db, last_scan)
        finally:
            db.close()
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
        _sse_publish(
            "scan_completed",
            {
                "job_id": job_id,
                "signals_found": len(signals),
                "diagnostics_summary": diagnostics_summary,
                "strategy_summary": strategy_summary,
            },
        )
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


@app.get("/")
def index() -> HTMLResponse:
    return render_versioned_html(STATIC_DIR / "index.html")


@app.get("/simple")
def simple_dashboard() -> HTMLResponse:
    """Focused scan + diagnostics UI for external users (see also `/`)."""
    return render_versioned_html(STATIC_DIR / "simple.html")


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


@app.get("/api/public-config", response_model=ApiResponse)
def public_config() -> ApiResponse:
    """Non-secret client config (optional Supabase browser sign-in)."""
    url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    anon = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    configured_api_key = (os.getenv("WEB_API_KEY") or "").strip()
    supabase: dict[str, str] | None = None
    if url and anon:
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
    data: dict[str, Any] = {
        "supabase": supabase,
        "saas_mode": False,
        "runtime_mode": "local",
        "ui_contract_version": "2026-04-webapp-stabilization",
        "scan_transport": "local_thread",
        "sse_enabled": True,
        "schwab_oauth": schwab_oauth,
        "schwab_market_oauth": schwab_market_oauth,
        "manual_jwt_entry_enabled": _manual_jwt_entry_enabled(default=True),
        "platform_live_trading_kill_switch": plat_kill,
        "api_key_required": bool(configured_api_key),
    }
    impl = (os.getenv("WEB_IMPLEMENTATION_GUIDE_URL") or "").strip()
    if impl.startswith(("http://", "https://")):
        data["implementation_guide_url"] = impl
    return _ok(data)


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
    if kind not in {"account", "market"} or not code.strip():
        return red("schwab_oauth=error&message=" + urllib.parse.quote("invalid_or_expired_state"))

    if kind == "market":
        client_id = (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
        client_secret = (os.getenv("SCHWAB_MARKET_APP_SECRET") or "").strip()
    else:
        client_id = (os.getenv("SCHWAB_ACCOUNT_APP_KEY") or "").strip()
        client_secret = (os.getenv("SCHWAB_ACCOUNT_APP_SECRET") or "").strip()
    redirect_uri = _resolve_schwab_redirect_uri(request, market=(kind == "market"))
    if not client_id or not client_secret:
        code_name = "server_market_oauth_not_configured" if kind == "market" else "server_oauth_not_configured"
        return red(f"{status_key(kind)}=error&message=" + urllib.parse.quote(code_name))
    try:
        tok = exchange_schwab_code_for_tokens(client_id, client_secret, code, redirect_uri)
    except Exception as e:
        return red(f"{status_key(kind)}=error&message=" + urllib.parse.quote(str(e)[:180]))
    access = str(tok.get("access_token") or "").strip()
    refresh = str(tok.get("refresh_token") or "").strip()
    if not access or not refresh:
        return red(f"{status_key(kind)}=error&message=" + urllib.parse.quote("token_response_missing_tokens"))
    if kind == "market":
        write_encrypted_token_file(TOKENS_MARKET_PATH, tok, client_secret)
        _audit_event("oauth_schwab_market_callback", "local-dashboard", {"saved": "tokens_market.enc"})
    else:
        write_encrypted_token_file(TOKENS_ACCOUNT_PATH, tok, client_secret)
        _audit_event("oauth_schwab_account_callback", "local-dashboard", {"saved": "tokens_account.enc"})
        market_client_id = (os.getenv("SCHWAB_MARKET_APP_KEY") or "").strip()
        market_secret = (os.getenv("SCHWAB_MARKET_APP_SECRET") or "").strip()
        market_missing = not TOKENS_MARKET_PATH.exists()
        if market_client_id and market_secret and market_missing:
            market_state = _new_local_oauth_state("market")
            market_redirect_uri = _resolve_schwab_redirect_uri(request, market=True)
            market_url = schwab_authorize_url(market_client_id, market_redirect_uri, market_state)
            return RedirectResponse(market_url, status_code=302)
    return red(f"{status_key(kind)}=ok")


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
                "kronos": _probe_kronos_health(),
                "metrics": metrics,
            }
        )
    except Exception as e:
        return _err("health_deep", e)


def _probe_kronos_health() -> dict[str, Any]:
    """Lightweight probe of the Kronos inference service for /api/health/deep.

    Only reaches out when the scanner plugin is active (mode != off); otherwise
    reports the configured mode without a network call. Never raises.
    """
    try:
        from config import get_kronos_inference_url, get_kronos_mode

        mode = get_kronos_mode(SKILL_DIR)
    except Exception:
        return {"enabled": False, "mode": "off", "service_ok": None}
    status: dict[str, Any] = {"enabled": mode != "off", "mode": mode, "service_ok": None}
    if mode == "off":
        return status
    try:
        import requests

        url = get_kronos_inference_url(SKILL_DIR)
        resp = requests.get(f"{url}/health", timeout=2.5)
        body = resp.json() if resp.ok else {}
        status["service_ok"] = bool(body.get("ok"))
        status["model_id"] = body.get("model_id")
    except Exception as exc:
        status["service_ok"] = False
        status["error"] = str(exc)[:200]
    return status


@app.get("/api/config", response_model=ApiResponse)
def config() -> ApiResponse:
    return _ok(
        {
            "trade_api_key_required": bool(os.getenv("WEB_API_KEY", "").strip()),
            "allowed_origins": allowed_origins,
        }
    )


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
        market_token_ok, market_state = _session_connection_state(
            _session_token_ok(auth.market_session), market_health
        )
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
                "validation_status": _latest_validation_status(),
                "schwab_token_health": schwab_token_health,
            }
        )
    except Exception as e:
        return _err("status", e)


@app.get("/api/validation/status", response_model=ApiResponse)
def validation_status() -> ApiResponse:
    try:
        return _ok(_latest_validation_status())
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
                    started = True
            if started:
                thread = threading.Thread(
                    target=_scan_worker,
                    args=(job_id, skw),
                    daemon=True,
                    name=f"scan-{job_id}",
                )
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


@app.get("/api/portfolio", response_model=ApiResponse)
def portfolio() -> ApiResponse:
    try:
        auth = get_shared_auth()
        status_data = get_account_status(auth=auth, skill_dir=SKILL_DIR)
        if isinstance(status_data, str):
            _record_endpoint_error("portfolio")
            return ApiResponse(ok=False, error=status_data)
        return _ok(_build_portfolio_summary(status_data))
    except Exception as e:
        return _err("portfolio", e)


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
def cockpit_page() -> HTMLResponse:
    """Trading Cockpit: four always-visible lanes (additive to the main dashboard)."""
    return render_versioned_html(STATIC_DIR / "cockpit.html")


@app.get("/api/cockpit/market", response_model=ApiResponse)
def cockpit_market(db: Session = Depends(get_db)) -> ApiResponse:
    try:
        from core import cockpit_service

        snapshot = _scan_snapshot()
        diagnostics = snapshot.get("diagnostics")
        if not isinstance(diagnostics, dict):
            last_scan = _load_state(db, "last_scan", {})
            diagnostics = (last_scan or {}).get("diagnostics") if isinstance(last_scan, dict) else None
        return _ok(cockpit_service.build_market(diagnostics or {}))
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
        from core import decision_packet, trade_review, weight_feedback

        packets = decision_packet.load_packets(SKILL_DIR)
        report = trade_review.weekly_report(packets)
        report["tuning_proposals"] = weight_feedback.propose(report)
        return _ok(report)
    except Exception as e:
        return _err("cockpit_review", e)


@app.post("/api/cockpit/review/backfill", response_model=ApiResponse)
def cockpit_review_backfill(
    _auth: dict[str, str] = Depends(require_api_key_if_set),
) -> ApiResponse:
    """Resolve matured decision packets with realized 10-day returns."""
    try:
        from core import outcome_backfill

        return _ok(outcome_backfill.run_local_backfill(SKILL_DIR, horizon_days=10))
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


@app.get("/api/cockpit/deltas", response_model=ApiResponse)
def cockpit_deltas(db: Session = Depends(get_db)) -> ApiResponse:
    """What changed since the previous scan cycle."""
    try:
        from core import cockpit_service

        snapshot = _scan_snapshot()
        curr = (
            {"signals": snapshot.get("signals")}
            if snapshot.get("signals")
            else _load_state(db, "last_scan", {})
        )
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
        curr = (
            {"signals": snapshot.get("signals")}
            if snapshot.get("signals")
            else _load_state(db, "last_scan", {})
        )
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
            with _scan_lock:
                rows = list(_scan_job.get("signals") or []) + list(_scan_job.get("shortlist_signals") or [])
            for row in rows:
                if str((row or {}).get("ticker", "")).upper() == symbol:
                    signal = row
                    break

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
        _execu = ExecutionProvider.from_order_result(result if isinstance(result, dict) else {}).model_dump(
            mode="json"
        )
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
    return _ok(
        {
            **current,
            "elapsed_minutes": elapsed_minutes,
            "target_minutes": current.get("target_minutes", ONBOARDING_TARGET_MINUTES),
            "completed_under_target": bool(
                completion and elapsed_minutes is not None and elapsed_minutes <= ONBOARDING_TARGET_MINUTES
            ),
        }
    )


@app.get("/api/decision-card/{ticker}", response_model=ApiResponse)
def decision_card(ticker: str, db: Session = Depends(get_db)) -> ApiResponse:
    symbol = ticker.upper().strip()
    signal = None
    with _scan_lock:
        # Prefer kept/live-ranked signals, then fall back to shortlist rows so
        # near-miss / filtered candidates can still show a decision brief.
        all_rows = list(_scan_job.get("signals") or []) + list(_scan_job.get("shortlist_signals") or [])
        for row in all_rows:
            if str((row or {}).get("ticker", "")).upper() == symbol:
                signal = row
                break
    if signal is None:
        return ApiResponse(ok=False, error=f"{symbol} is not in current scan results. Run scan first.")

    price = float(signal.get("price", 0) or 0)
    size_usd = get_position_size_usd(ticker=symbol, price=price if price > 0 else None, skill_dir=SKILL_DIR)
    qty = max(1, int(size_usd / price)) if price > 0 else 1
    stop_pct = max(0.03, min(0.15, 0.07))
    stop_level = round(price * (1.0 - stop_pct), 2) if price > 0 else None
    entry_zone = (
        {"low": round(price * 0.995, 2), "high": round(price * 1.005, 2)} if price > 0 else {"low": None, "high": None}
    )
    confidence_bucket = str(((signal.get("advisory") or {}).get("confidence_bucket") or "unknown")).lower()
    score = float(signal.get("composite_score", signal.get("signal_score", 0)) or 0)
    reliability = signal.get("reliability_score")
    edge = signal.get("edge_score")
    execution = signal.get("execution_score")
    ev_10d = signal.get("ev_10d")
    rank_score = signal.get("rank_score")
    rank_basis = signal.get("rank_basis")
    conviction = signal.get("mirofish_conviction")
    try:
        reliability_text = f"{float(reliability):.1f}" if reliability is not None else "unknown"
    except (TypeError, ValueError):
        reliability_text = "unknown"
    reasons = [
        f"composite_score={score:.1f}",
        f"reliability={reliability_text}",
        f"confidence={confidence_bucket}",
        f"strategy={((signal.get('strategy_attribution') or {}).get('top_live') or 'unknown')}",
    ]
    if rank_score is not None:
        reasons.append(f"rank_score={rank_score}")
    if rank_basis:
        reasons.append(f"rank_basis={rank_basis}")
    if conviction is not None:
        reasons.append(f"mirofish_conviction={conviction}")
    if signal.get("event_risk", {}).get("flagged"):
        reasons.append(f"event_risk={','.join(signal.get('event_risk', {}).get('reasons', []))}")

    mock_trade = PendingTrade(
        id="preview", ticker=symbol, qty=qty, price=price, status="pending", signal_json=json.dumps(signal), note=None
    )
    checklist = _build_pretrade_checklist(mock_trade, signal)
    advisory = signal.get("advisory") if isinstance(signal.get("advisory"), dict) else {}
    p_up_10d = advisory.get("p_up_10d")
    sec_risk_tag = str(signal.get("sec_risk_tag") or "unknown").lower()
    sec_recency_days = signal.get("filing_recency_days")
    event_risk = signal.get("event_risk") if isinstance(signal.get("event_risk"), dict) else {}
    forensic_flags = [str(v) for v in list(signal.get("forensic_flags") or []) if str(v).strip()]
    setup_summary = (
        f"{symbol} remains a breakout candidate with score {score:.1f}, "
        f"{confidence_bucket} confidence, and strategy "
        f"{((signal.get('strategy_attribution') or {}).get('top_live') or 'unknown')}."
    )
    key_risks: list[str] = []
    for r in list(checklist.get("block_reasons_plain") or []):
        txt = str(r).strip()
        if txt:
            key_risks.append(txt)
    if sec_risk_tag in {"high", "medium"}:
        key_risks.append(f"SEC risk tag: {sec_risk_tag}.")
    if confidence_bucket in {"low", "unknown"}:
        key_risks.append("Advisory confidence is low/unknown.")
    if event_risk.get("flagged"):
        event_reasons = ", ".join([str(x) for x in list(event_risk.get("reasons") or []) if str(x).strip()])
        key_risks.append(f"Event risk flagged{': ' + event_reasons if event_reasons else ''}.")
    catalyst_notes: list[str] = []
    pead_surprise = signal.get("pead_surprise_pct")
    pead_beat = signal.get("pead_beat")
    if pead_surprise is not None:
        try:
            s = float(pead_surprise)
            if pead_beat is True and s >= 0.05:
                catalyst_notes.append(f"Positive earnings surprise: {round(s * 100, 1)}%.")
            elif pead_beat is False and s <= -0.05:
                catalyst_notes.append(f"Negative earnings surprise: {round(s * 100, 1)}%.")
        except (TypeError, ValueError):
            pass
    catalyst_notes.append(
        f"Primary strategy signal: {((signal.get('strategy_attribution') or {}).get('top_live') or 'unknown')}."
    )
    sec_notes: list[str] = []
    sec_notes.append(f"SEC risk tag: {sec_risk_tag}.")
    if isinstance(sec_recency_days, int):
        sec_notes.append(f"Most recent filing context age: {sec_recency_days} day(s).")
    if not sec_notes:
        sec_notes.append("No SEC enrichment notes available.")
    forensic_note_lines: list[str] = []
    if forensic_flags:
        forensic_note_lines.extend(forensic_flags)
    if signal.get("forensic_sloan") is not None:
        forensic_note_lines.append(f"sloan={signal.get('forensic_sloan')}")
    if signal.get("forensic_beneish") is not None:
        forensic_note_lines.append(f"beneish={signal.get('forensic_beneish')}")
    if signal.get("forensic_altman") is not None:
        forensic_note_lines.append(f"altman={signal.get('forensic_altman')}")
    expected_move_window = "10 trading days"
    if p_up_10d is not None:
        try:
            expected_move_window = f"10 trading days (P(up) {round(float(p_up_10d) * 100, 1)}%)"
        except (TypeError, ValueError):
            pass
    entry_stop_ideas = [
        f"Entry zone: {entry_zone.get('low')} to {entry_zone.get('high')}.",
        f"Stop / invalidation: {stop_level}.",
        f"Sizing preview: {qty} shares (~${round(float(size_usd), 2)}).",
    ]

    return _ok(
        {
            "ticker": symbol,
            "entry_zone": entry_zone,
            "stop_invalidation": stop_level,
            "size": {"qty": qty, "usd": size_usd},
            "confidence": {
                "bucket": confidence_bucket,
                "signal_score": score,
                "mirofish_conviction": conviction,
                "edge_score": edge,
                "reliability_score": reliability,
                "execution_score": execution,
                "ev_10d": ev_10d,
                "rank_score": rank_score,
                "rank_basis": rank_basis,
            },
            "key_reasons": reasons[:6],
            "block_reason": (checklist.get("block_reasons") or [None])[0],
            "checklist": checklist,
            "brief": {
                "setup_summary": setup_summary,
                "key_risks": key_risks[:6],
                "catalyst_notes": catalyst_notes[:6],
                "forensic_flags": forensic_note_lines[:6],
                "sec_notes": sec_notes[:6],
                "expected_move_window": expected_move_window,
                "entry_stop_ideas": entry_stop_ideas[:4],
            },
        }
    )