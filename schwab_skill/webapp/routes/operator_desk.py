"""On-demand operator desk. The worker calls the dashboard over HTTP."""

from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from operator_desk import DashboardTools, run_operator_desk

from ..route_helpers import ok as _ok
from ..route_helpers import require_api_key_if_set as _require_api_key_if_set
from ..schemas import ApiResponse, OperatorDeskRunRequest

LOG = logging.getLogger(__name__)
router = APIRouter(tags=["operator-desk"], dependencies=[Depends(_require_api_key_if_set)])
SKILL_DIR = Path(__file__).resolve().parent.parent.parent

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_LATEST_ID: str | None = None


def _snapshot(job_id: str | None = None) -> dict[str, Any] | None:
    with _LOCK:
        key = job_id or _LATEST_ID
        if not key:
            return None
        row = _JOBS.get(key)
        return dict(row) if isinstance(row, dict) else None


def _run_job(job_id: str, body: OperatorDeskRunRequest, base_url: str, api_key: str) -> None:
    tools = DashboardTools(base_url, api_key=api_key)
    try:
        from config import get_operator_desk_scan_timeout_sec

        brief = run_operator_desk(
            tools,
            skill_dir=SKILL_DIR,
            run_scan=body.run_scan,
            queue_backtest=body.queue_backtest,
            notify_discord=body.notify_discord,
            ticker=body.ticker,
            scan_timeout_s=float(get_operator_desk_scan_timeout_sec(SKILL_DIR)),
        )
        status = "completed"
        error = None
    except Exception as exc:
        LOG.warning("Operator desk %s failed: %s", job_id, exc)
        brief = None
        status = "failed"
        error = str(exc)
    with _LOCK:
        row = _JOBS.get(job_id)
        if row is not None:
            row["status"] = status
            row["finished_at"] = datetime.now(timezone.utc).isoformat()
            row["brief"] = brief
            row["error"] = error


@router.get("/api/operator-desk/status", response_model=ApiResponse)
def operator_desk_status(job_id: str | None = None) -> ApiResponse:
    row = _snapshot(job_id)
    if row is None:
        return _ok({"status": "idle"})
    return _ok(row)


@router.post("/api/operator-desk/run", response_model=ApiResponse)
def operator_desk_run(body: OperatorDeskRunRequest, request: Request) -> ApiResponse:
    from config import get_operator_desk_base_url

    job_id = uuid.uuid4().hex[:10]
    base_url = str(request.base_url).rstrip("/")
    configured = get_operator_desk_base_url(SKILL_DIR).strip()
    if configured:
        base_url = configured.rstrip("/")
    api_key = (request.headers.get("x-api-key") or os.environ.get("WEB_API_KEY") or "").strip()
    row = {
        "job_id": job_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "brief": None,
        "error": None,
    }
    global _LATEST_ID
    with _LOCK:
        _JOBS[job_id] = row
        _LATEST_ID = job_id
        if len(_JOBS) > 8:
            oldest = sorted(_JOBS.values(), key=lambda item: str(item.get("started_at") or ""))[:-8]
            for item in oldest:
                _JOBS.pop(str(item.get("job_id") or ""), None)
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, body, base_url, api_key),
        daemon=True,
        name=f"operator-desk-{job_id}",
    )
    thread.start()
    return _ok({"started": True, "job_id": job_id, "status": "running"})
