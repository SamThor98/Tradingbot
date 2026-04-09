from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import stripe
from celery.result import AsyncResult
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from execution import get_account_status

from .audit import log_audit
from .billing_stripe import (
    billing_enforcement_enabled,
    create_billing_portal_session,
    create_subscription_checkout_session,
    handle_stripe_event,
    stripe_event_id,
    stripe_event_type,
    try_claim_stripe_webhook_event,
    user_has_paid_entitlement,
)
from .db import DATABASE_URL, Base, SessionLocal, engine
from .backtest_queue import create_and_queue_backtest
from .models import AppState, BacktestRun, Order, Position, ScanResult, User, UserCredential
from .saas_redis import (
    acquire_scan_cooldown,
    fixed_window_rate_limit,
    order_idempotency_existing_task,
    order_idempotency_record_task,
    redis_ping,
)
from .schemas import (
    ApiResponse,
    BillingCheckoutRequest,
    ExecuteOrderRequest,
    QueueUserBacktestRequest,
    SchwabCredentialUpsert,
    StrategyChatRequest,
)
from .security import (
    encrypt_secret,
    get_current_user,
    parse_json,
    parse_scopes,
    parse_token_expiry,
    require_paid_entitlement,
    utcnow_iso,
)
from .strategy_chat import run_strategy_chat
from .tasks import celery_app, execute_order_for_user, scan_for_user
from .tenant_runtime import tenant_skill_dir, user_can_materialize_for_scan, user_has_account_session

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
_ALEMBIC_INI = APP_DIR.parent / "alembic.ini"

if os.getenv("SAAS_BOOTSTRAP_SCHEMA", "").lower() in ("1", "true", "yes"):
    Base.metadata.create_all(bind=engine)
    if _ALEMBIC_INI.is_file():
        from alembic.config import Config

        from alembic import command

        command.stamp(Config(str(_ALEMBIC_INI)), "saas003")
elif os.getenv("SAAS_RUN_ALEMBIC", "").lower() in ("1", "true", "yes"):
    if _ALEMBIC_INI.is_file():
        from alembic.config import Config

        from alembic import command

        command.upgrade(Config(str(_ALEMBIC_INI)), "head")
elif DATABASE_URL.startswith("sqlite"):
    Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="TradingBot SaaS API",
    version="1.0.0",
    description="Multi-tenant TradingBot API with JWT auth, encrypted credentials, and async workers.",
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "WEB_ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if origin.strip()
]
if not allowed_origins:
    allowed_origins = ["http://127.0.0.1:8000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "Idempotency-Key"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Any) -> Any:
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _ok(data: Any = None) -> ApiResponse:
    return ApiResponse(ok=True, data=data)


def _err(message: str, data: Any = None) -> ApiResponse:
    return ApiResponse(ok=False, error=message, data=data)


def _db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _save_state(db: Session, user_id: str, key: str, payload: dict[str, Any]) -> None:
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    if not row:
        row = AppState(user_id=user_id, key=key, value_json=json.dumps(payload, default=_json_default))
        db.add(row)
    else:
        row.value_json = json.dumps(payload, default=_json_default)
    db.commit()


def _load_state(db: Session, user_id: str, key: str, default: dict[str, Any]) -> dict[str, Any]:
    row = db.query(AppState).filter(AppState.user_id == user_id, AppState.key == key).first()
    if not row:
        return default
    parsed = parse_json(row.value_json, default)
    return parsed if isinstance(parsed, dict) else default


def _is_schwab_linked(db: Session, user_id: str) -> bool:
    return user_has_account_session(db, user_id)


def _scan_rate_limit(user_id: str) -> None:
    limit = int(os.getenv("SAAS_RATE_SCAN_PER_MIN", "12"))
    window = int(os.getenv("SAAS_RATE_LIMIT_WINDOW_SEC", "60"))
    ok, n = fixed_window_rate_limit(user_id, "scan", limit, window)
    if not ok:
        raise HTTPException(status_code=429, detail=f"Scan rate limit exceeded ({n}/{limit} per {window}s).")


def _order_rate_limit(user_id: str) -> None:
    limit = int(os.getenv("SAAS_RATE_ORDER_PER_MIN", "30"))
    window = int(os.getenv("SAAS_RATE_LIMIT_WINDOW_SEC", "60"))
    ok, n = fixed_window_rate_limit(user_id, "order", limit, window)
    if not ok:
        raise HTTPException(status_code=429, detail=f"Order rate limit exceeded ({n}/{limit} per {window}s).")


def _backtest_rate_limit(user_id: str) -> None:
    limit = int(os.getenv("SAAS_RATE_BACKTEST_PER_HOUR", "6"))
    window = 3600
    ok, n = fixed_window_rate_limit(user_id, "backtest", limit, window)
    if not ok:
        raise HTTPException(status_code=429, detail=f"Backtest rate limit exceeded ({n}/{limit} per hour).")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", response_model=ApiResponse)
def health() -> ApiResponse:
    return _ok(
        {
            "status": "ok",
            "time": datetime.now(timezone.utc).isoformat(),
            "auth_mode": "jwt",
            "queue_backend": celery_app.conf.result_backend,
        }
    )


@app.get("/api/health/live", response_model=ApiResponse)
def health_live() -> ApiResponse:
    return _ok({"status": "live", "time": datetime.now(timezone.utc).isoformat()})


@app.get("/api/health/ready", response_model=ApiResponse)
def health_ready() -> ApiResponse:
    db_ok = False
    try:
        s = SessionLocal()
        try:
            s.execute(text("SELECT 1"))
            db_ok = True
        finally:
            s.close()
    except Exception:
        db_ok = False
    redis_ok = redis_ping()
    require_redis = os.getenv("SAAS_HEALTH_REQUIRE_REDIS", "1").lower() in ("1", "true", "yes")
    ready = db_ok and (redis_ok if require_redis else True)
    return _ok(
        {
            "status": "ready" if ready else "not_ready",
            "database": db_ok,
            "redis": redis_ok,
            "time": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/api/me", response_model=ApiResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    linked = _is_schwab_linked(db, user.id)
    period_end = (
        user.subscription_current_period_end.isoformat()
        if user.subscription_current_period_end
        else None
    )
    return _ok(
        {
            "id": user.id,
            "email": user.email,
            "provider": user.auth_provider,
            "schwab_linked": linked,
            "onboarding_required": not linked,
            "subscription_status": user.subscription_status,
            "subscription_current_period_end": period_end,
            "has_stripe_customer": bool(user.stripe_customer_id),
            "billing_enforced": billing_enforcement_enabled(),
            "subscription_active": user_has_paid_entitlement(user),
        }
    )


@app.post("/api/billing/checkout-session", response_model=ApiResponse)
def billing_checkout_session(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
    payload: BillingCheckoutRequest | None = Body(default=None),
) -> ApiResponse:
    body = payload if payload is not None else BillingCheckoutRequest()
    success = str(body.success_url) if body.success_url else (os.getenv("STRIPE_CHECKOUT_SUCCESS_URL") or "").strip()
    cancel = str(body.cancel_url) if body.cancel_url else (os.getenv("STRIPE_CHECKOUT_CANCEL_URL") or "").strip()
    if not success or not cancel:
        raise HTTPException(
            status_code=503,
            detail="Set STRIPE_CHECKOUT_SUCCESS_URL and STRIPE_CHECKOUT_CANCEL_URL or pass success_url and cancel_url in the request body.",
        )
    try:
        url = create_subscription_checkout_session(user, success, cancel)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    log_audit(
        db,
        action="billing_checkout_session_created",
        user_id=user.id,
        detail={},
        request_id=_request_id(request),
    )
    return _ok({"url": url})


@app.post("/api/billing/portal-session", response_model=ApiResponse)
def billing_portal_session(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    return_url = (os.getenv("STRIPE_PORTAL_RETURN_URL") or "").strip()
    if not return_url:
        raise HTTPException(
            status_code=503,
            detail="STRIPE_PORTAL_RETURN_URL is not configured.",
        )
    if not user.stripe_customer_id:
        raise HTTPException(
            status_code=409,
            detail="No Stripe customer on file. Complete checkout first.",
        )
    try:
        url = create_billing_portal_session(user, return_url)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    log_audit(
        db,
        action="billing_portal_session_created",
        user_id=user.id,
        detail={},
        request_id=_request_id(request),
    )
    return _ok({"url": url})


@app.post("/api/billing/webhook/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(_db)) -> Response:
    wh_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not wh_secret:
        raise HTTPException(status_code=503, detail="STRIPE_WEBHOOK_SECRET is not configured.")

    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header.")

    try:
        event = stripe.Webhook.construct_event(payload, sig, wh_secret)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload.") from exc
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.") from exc

    eid = stripe_event_id(event)
    if not eid:
        raise HTTPException(status_code=400, detail="Event missing id.")

    if not try_claim_stripe_webhook_event(db, eid):
        return Response(status_code=200, content="duplicate")

    etype = stripe_event_type(event)
    try:
        handle_stripe_event(db, event)
        db.commit()
    except Exception:
        db.rollback()
        raise
    log_audit(
        db,
        action="billing_stripe_webhook",
        user_id=None,
        detail={"event_id": eid, "type": etype},
        request_id=_request_id(request),
    )
    return Response(status_code=200, content="ok")


@app.post("/api/credentials/schwab", response_model=ApiResponse)
def upsert_schwab_credentials(
    request: Request,
    payload: SchwabCredentialUpsert,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    row = db.query(UserCredential).filter(UserCredential.user_id == user.id).first()
    if not row:
        row = UserCredential(user_id=user.id)
        db.add(row)

    if payload.access_token:
        row.access_token_enc = encrypt_secret(payload.access_token)
    if payload.refresh_token:
        row.refresh_token_enc = encrypt_secret(payload.refresh_token)
    if payload.token_type is not None:
        row.token_type = payload.token_type
    row.expires_at = parse_token_expiry(payload.expires_at)
    row.scopes = parse_scopes(payload.scopes)

    if payload.account_oauth_json and payload.account_oauth_json.strip():
        row.account_token_payload_enc = encrypt_secret(payload.account_oauth_json.strip())
    if payload.market_oauth_json and payload.market_oauth_json.strip():
        row.market_token_payload_enc = encrypt_secret(payload.market_oauth_json.strip())

    if payload.access_token and payload.refresh_token and not (payload.account_oauth_json or "").strip():
        row.account_token_payload_enc = encrypt_secret(
            json.dumps(
                {
                    "access_token": payload.access_token,
                    "refresh_token": payload.refresh_token,
                    "token_type": (payload.token_type or "Bearer").strip() or "Bearer",
                }
            )
        )

    db.commit()
    db.refresh(row)

    _save_state(
        db,
        user.id,
        "onboarding",
        {
            "linked_at": utcnow_iso(),
            "schwab_linked": True,
            "wizard_required": False,
        },
    )
    log_audit(
        db,
        action="credentials_schwab_upsert",
        user_id=user.id,
        detail={"has_market_blob": bool(row.market_token_payload_enc)},
        request_id=_request_id(request),
    )
    return _ok({"schwab_linked": True, "updated_at": row.updated_at.isoformat() if row.updated_at else None})


@app.get("/api/credentials/status", response_model=ApiResponse)
def credential_status(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    row = db.query(UserCredential).filter(UserCredential.user_id == user.id).first()
    linked = _is_schwab_linked(db, user.id)
    expires_at = row.expires_at.isoformat() if row and row.expires_at else None
    return _ok({"schwab_linked": linked, "expires_at": expires_at, "onboarding_required": not linked})


@app.get("/api/onboarding/status", response_model=ApiResponse)
def onboarding_status(user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    linked = _is_schwab_linked(db, user.id)
    state = _load_state(
        db,
        user.id,
        "onboarding",
        default={
            "linked_at": None,
            "schwab_linked": linked,
            "wizard_required": not linked,
        },
    )
    state["schwab_linked"] = linked
    state["onboarding_required"] = not linked
    return _ok(state)


@app.post("/api/scan", response_model=ApiResponse)
def run_scan(
    request: Request,
    user: User = Depends(require_paid_entitlement),
    db: Session = Depends(_db),
) -> ApiResponse:
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before running scans.")
    ok_scan, reason = user_can_materialize_for_scan(db, user.id)
    if not ok_scan:
        raise HTTPException(status_code=409, detail=reason)
    _scan_rate_limit(user.id)
    cooldown = int(os.getenv("SAAS_SCAN_COOLDOWN_SEC", "60"))
    if not acquire_scan_cooldown(user.id, cooldown):
        raise HTTPException(
            status_code=409,
            detail=f"A scan was started recently; wait up to {cooldown}s before retrying.",
        )
    task = scan_for_user.apply_async(args=[user.id], queue="scan")
    log_audit(
        db,
        action="scan_queued",
        user_id=user.id,
        detail={"task_id": task.id},
        request_id=_request_id(request),
    )
    return _ok({"task_id": task.id, "status": "queued"})


@app.get("/api/scan/{task_id}", response_model=ApiResponse)
def scan_task_status(
    task_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    task = AsyncResult(task_id, app=celery_app)
    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": task.status.lower(),
    }
    if task.ready():
        result = task.result if isinstance(task.result, dict) else {"raw_result": str(task.result)}
        payload["result"] = result
    recent = (
        db.query(ScanResult)
        .filter(ScanResult.user_id == user.id)
        .order_by(ScanResult.created_at.desc())
        .limit(25)
        .all()
    )
    payload["recent_results"] = [
        {
            "id": row.id,
            "job_id": row.job_id,
            "ticker": row.ticker,
            "signal_score": row.signal_score,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "payload": parse_json(row.payload_json, {}),
        }
        for row in recent
    ]
    return _ok(payload)


@app.get("/api/scan-results", response_model=ApiResponse)
def list_scan_results(
    limit: int = Query(default=100, ge=1, le=500),
    job_id: str | None = Query(default=None, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    q = db.query(ScanResult).filter(ScanResult.user_id == user.id)
    jid = (job_id or "").strip()
    if jid:
        q = q.filter(ScanResult.job_id == jid)
    rows = q.order_by(ScanResult.created_at.desc()).limit(limit).all()
    return _ok(
        [
            {
                "id": row.id,
                "job_id": row.job_id,
                "ticker": row.ticker,
                "signal_score": row.signal_score,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "payload": parse_json(row.payload_json, {}),
            }
            for row in rows
        ]
    )


@app.post("/api/orders/execute", response_model=ApiResponse)
def execute_order(
    request: Request,
    payload: ExecuteOrderRequest,
    user: User = Depends(require_paid_entitlement),
    db: Session = Depends(_db),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ApiResponse:
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before executing orders.")
    idem = (idempotency_key_header or payload.idempotency_key or "").strip()
    if idem:
        existing = order_idempotency_existing_task(user.id, idem)
        if existing:
            return _ok({"task_id": existing, "status": "deduplicated"})
    _order_rate_limit(user.id)
    task = execute_order_for_user.apply_async(
        args=[user.id, payload.ticker, payload.qty, payload.side, payload.order_type, payload.price],
        queue="orders",
    )
    if idem:
        order_idempotency_record_task(user.id, idem, task.id)
    log_audit(
        db,
        action="order_queued",
        user_id=user.id,
        detail={"task_id": task.id, "ticker": payload.ticker, "qty": payload.qty, "side": payload.side},
        request_id=_request_id(request),
    )
    return _ok({"task_id": task.id, "status": "queued"})


@app.get("/api/orders/{task_id}", response_model=ApiResponse)
def order_task_status(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(_db)) -> ApiResponse:
    task = AsyncResult(task_id, app=celery_app)
    rows = (
        db.query(Order)
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(25)
        .all()
    )
    return _ok(
        {
            "task_id": task_id,
            "task_status": task.status.lower(),
            "task_result": task.result if task.ready() else None,
            "orders": [
                {
                    "id": row.id,
                    "ticker": row.ticker,
                    "qty": row.qty,
                    "side": row.side,
                    "order_type": row.order_type,
                    "status": row.status,
                    "broker_order_id": row.broker_order_id,
                    "result": parse_json(row.result_json, {}),
                    "error_message": row.error_message,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }
    )


@app.get("/api/orders", response_model=ApiResponse)
def list_orders(
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    rows = (
        db.query(Order)
        .filter(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )
    return _ok(
        [
            {
                "id": row.id,
                "ticker": row.ticker,
                "qty": row.qty,
                "side": row.side,
                "order_type": row.order_type,
                "status": row.status,
                "result": parse_json(row.result_json, {}),
                "error_message": row.error_message,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@app.get("/api/positions/sync", response_model=ApiResponse)
def sync_positions(
    user: User = Depends(require_paid_entitlement),
    db: Session = Depends(_db),
) -> ApiResponse:
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before syncing positions.")
    try:
        with tenant_skill_dir(db, user.id) as skill_dir:
            status_data = get_account_status(skill_dir=skill_dir)
    except Exception as exc:
        return _err(str(exc))
    if isinstance(status_data, str):
        return _err(status_data)

    inserted = 0
    accounts = status_data.get("accounts", [])
    for acc in accounts:
        sec = acc.get("securitiesAccount", acc)
        for pos in sec.get("positions", []):
            inst = pos.get("instrument", {})
            symbol = str(inst.get("symbol") or "").upper()
            if not symbol:
                continue
            qty = float(pos.get("longQuantity", 0) or pos.get("shortQuantity", 0) or 0)
            row = Position(
                user_id=user.id,
                symbol=symbol,
                qty=qty,
                avg_cost=float(pos.get("averagePrice", 0) or 0),
                market_value=float(pos.get("marketValue", 0) or 0),
            )
            db.add(row)
            inserted += 1
    db.commit()
    return _ok({"synced_positions": inserted})


@app.get("/api/positions", response_model=ApiResponse)
def list_positions(
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    rows = (
        db.query(Position)
        .filter(Position.user_id == user.id)
        .order_by(Position.as_of.desc(), Position.id.desc())
        .limit(limit)
        .all()
    )
    return _ok(
        [
            {
                "id": row.id,
                "symbol": row.symbol,
                "qty": row.qty,
                "avg_cost": row.avg_cost,
                "market_value": row.market_value,
                "as_of": row.as_of.isoformat() if row.as_of else None,
            }
            for row in rows
        ]
    )


@app.post("/api/backtest-runs", response_model=ApiResponse)
def queue_backtest_run(
    request: Request,
    payload: QueueUserBacktestRequest,
    user: User = Depends(require_paid_entitlement),
    db: Session = Depends(_db),
) -> ApiResponse:
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before running backtests.")
    ok_scan, reason = user_can_materialize_for_scan(db, user.id)
    if not ok_scan:
        raise HTTPException(status_code=409, detail=reason)
    _backtest_rate_limit(user.id)
    try:
        out = create_and_queue_backtest(db, user.id, payload.spec)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_audit(
        db,
        action="backtest_queued",
        user_id=user.id,
        detail={"task_id": out.get("task_id"), "run_id": out.get("run_id")},
        request_id=_request_id(request),
    )
    return _ok(out)


@app.get("/api/backtest-runs", response_model=ApiResponse)
def list_backtest_runs(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    rows = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == user.id)
        .order_by(BacktestRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return _ok(
        [
            {
                "id": row.id,
                "celery_task_id": row.celery_task_id,
                "status": row.status,
                "spec": parse_json(row.spec_json, {}),
                "error_message": row.error_message,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "has_result": bool(row.result_json),
            }
            for row in rows
        ]
    )


@app.get("/api/backtest-runs/tasks/{task_id}", response_model=ApiResponse)
def backtest_run_task_status(
    task_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(_db),
) -> ApiResponse:
    row = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == user.id, BacktestRun.celery_task_id == task_id)
        .first()
    )
    task = AsyncResult(task_id, app=celery_app)
    payload: dict[str, Any] = {"task_id": task_id, "celery_status": task.status.lower()}
    if row:
        payload["run_id"] = row.id
        payload["db_status"] = row.status
        payload["error_message"] = row.error_message
        if row.result_json:
            payload["result"] = parse_json(row.result_json, {})
    if task.ready():
        tr = task.result
        payload["task_result"] = tr if isinstance(tr, dict) else {"raw_result": str(tr)}
    return _ok(payload)


@app.post("/api/strategy-chat", response_model=ApiResponse)
def strategy_chat_endpoint(
    request: Request,
    payload: StrategyChatRequest,
    user: User = Depends(require_paid_entitlement),
    db: Session = Depends(_db),
) -> ApiResponse:
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("MIROFISH_API_KEY") or "").strip():
        raise HTTPException(
            status_code=503,
            detail="Strategy chat requires OPENAI_API_KEY or MIROFISH_API_KEY.",
        )
    if not _is_schwab_linked(db, user.id):
        raise HTTPException(status_code=409, detail="Link Schwab account before using strategy chat.")
    ok_scan, reason = user_can_materialize_for_scan(db, user.id)
    if not ok_scan:
        raise HTTPException(status_code=409, detail=reason)
    _backtest_rate_limit(user.id)
    try:
        out = run_strategy_chat(db, user.id, payload.messages)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        return _err(str(exc))
    log_audit(
        db,
        action="strategy_chat",
        user_id=user.id,
        detail={"model": out.get("model")},
        request_id=_request_id(request),
    )
    return _ok(out)
