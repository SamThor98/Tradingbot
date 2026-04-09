"""Create BacktestRun rows and queue Celery workers (shared by REST and strategy chat)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .backtest_spec import parse_strategy_spec, spec_preview_dict
from .models import BacktestRun
from .tasks import backtest_for_user


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def create_and_queue_backtest(db: Session, user_id: str, spec_dict: dict[str, Any]) -> dict[str, Any]:
    spec = parse_strategy_spec(spec_dict)
    run_id = uuid.uuid4().hex
    row = BacktestRun(
        id=run_id,
        user_id=user_id,
        status="queued",
        spec_json=json.dumps(spec.model_dump(), default=_json_default),
    )
    db.add(row)
    db.commit()
    task = backtest_for_user.apply_async(args=[run_id, user_id], queue="scan")
    row.celery_task_id = task.id
    db.commit()
    return {
        "ok": True,
        "run_id": run_id,
        "task_id": task.id,
        "preview": spec_preview_dict(spec),
    }
