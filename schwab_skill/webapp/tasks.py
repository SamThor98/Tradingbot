from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

from celery import Celery

from execution import place_order
from signal_scanner import scan_for_signals_detailed

from .billing_stripe import user_has_paid_entitlement
from .db import SessionLocal
from .models import BacktestRun, Order, ScanResult, User
from .tenant_runtime import tenant_skill_dir

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "tradingbot_webapp",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_routes={
        "webapp.scan_for_user": {"queue": "scan"},
        "webapp.execute_order_for_user": {"queue": "orders"},
        "webapp.backtest_for_user": {"queue": "scan"},
    },
    task_default_queue="celery",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@celery_app.task(name="webapp.scan_for_user")
def scan_for_user(user_id: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user_has_paid_entitlement(user):
            return {"ok": False, "job_id": job_id, "error": "Active subscription required."}
        with tenant_skill_dir(db, user_id) as skill_dir:
            signals, diagnostics = scan_for_signals_detailed(skill_dir=skill_dir)
            inserted = 0
            for sig in signals:
                row = ScanResult(
                    user_id=user_id,
                    job_id=job_id,
                    ticker=str(sig.get("ticker") or sig.get("symbol") or "").upper(),
                    signal_score=(
                        float(sig.get("signal_score")) if sig.get("signal_score") is not None else None
                    ),
                    payload_json=json.dumps(sig, default=_json_default),
                )
                db.add(row)
                inserted += 1
            db.commit()
            return {
                "ok": True,
                "job_id": job_id,
                "signals_found": inserted,
                "diagnostics": diagnostics,
            }
    except Exception as exc:
        db.rollback()
        return {"ok": False, "job_id": job_id, "error": str(exc)}
    finally:
        db.close()


@celery_app.task(name="webapp.backtest_for_user")
def backtest_for_user(run_id: str, user_id: str) -> dict[str, Any]:
    from .backtest_spec import parse_strategy_spec, run_strategy_backtest

    db = SessionLocal()
    try:
        row = db.query(BacktestRun).filter(BacktestRun.id == run_id, BacktestRun.user_id == user_id).first()
        if not row:
            return {"ok": False, "error": "run not found"}
        row.status = "running"
        db.commit()
        try:
            spec = parse_strategy_spec(json.loads(row.spec_json))
        except Exception as exc:
            row.status = "failed"
            row.error_message = str(exc)
            db.commit()
            return {"ok": False, "error": str(exc), "run_id": run_id}
        try:
            with tenant_skill_dir(db, user_id) as skill_dir:
                result = run_strategy_backtest(skill_dir, spec)
            row.status = "success"
            row.result_json = json.dumps(result, default=_json_default)
            row.error_message = None
            db.commit()
            summary = {
                "total_trades": result.get("total_trades"),
                "win_rate_net": result.get("win_rate_net"),
                "total_return_net_pct": result.get("total_return_net_pct"),
                "cagr_net_pct": result.get("cagr_net_pct"),
                "max_drawdown_net_pct": result.get("max_drawdown_net_pct"),
                "findings": result.get("findings"),
            }
            return {"ok": True, "run_id": run_id, "summary": summary}
        except Exception as exc:
            row.status = "failed"
            row.error_message = str(exc)
            row.result_json = None
            db.commit()
            return {"ok": False, "error": str(exc), "run_id": run_id}
    finally:
        db.close()


@celery_app.task(name="webapp.execute_order_for_user")
def execute_order_for_user(
    user_id: str,
    ticker: str,
    qty: int,
    side: str = "BUY",
    order_type: str = "MARKET",
    price: float | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    user = db.query(User).filter(User.id == user_id).first()
    if not user_has_paid_entitlement(user):
        return {"ok": False, "error": "Active subscription required."}
    order_id = uuid.uuid4().hex[:12]
    row = Order(
        id=order_id,
        user_id=user_id,
        ticker=ticker.upper().strip(),
        qty=qty,
        side=side.upper().strip(),
        order_type=order_type.upper().strip(),
        price=price,
        status="queued",
        result_json="{}",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        with tenant_skill_dir(db, user_id) as skill_dir:
            result = place_order(
                ticker=row.ticker,
                qty=row.qty,
                side=row.side,
                order_type=row.order_type,
                price_hint=row.price,
                skill_dir=skill_dir,
            )
        if isinstance(result, str):
            row.status = "failed"
            row.error_message = result
            row.result_json = json.dumps({"ok": False, "error": result})
            db.commit()
            return {"ok": False, "order_id": row.id, "error": result}

        row.status = "executed"
        row.result_json = json.dumps(result, default=_json_default)
        db.commit()
        return {"ok": True, "order_id": row.id, "result": result}
    except Exception as exc:
        db.rollback()
        row = db.query(Order).filter(Order.id == order_id).first()
        if row:
            row.status = "failed"
            row.error_message = str(exc)
            row.result_json = json.dumps({"ok": False, "error": str(exc)})
            db.commit()
        return {"ok": False, "order_id": order_id, "error": str(exc)}
    finally:
        db.close()
