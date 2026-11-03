"""Operator control for the Grok research agent.

``GET  /api/agents/grok/capabilities``  non-secret mode, model, and published card
``POST /api/agents/grok``               one orchestrated handoff

Default mode is off, so POST records a skipped handoff and does not call xAI.
The route does not place orders.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.grok_agent import public_capabilities
from core.hybrid_orchestrator import run_handoff

from ..recovery_map import map_failure as _map_failure
from ..route_helpers import require_api_key_if_set as _require_api_key_if_set
from ..schemas import ApiResponse

LOG = logging.getLogger(__name__)

router = APIRouter(tags=["grok-agent"])


class GrokConsultBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20000)
    role: Literal["research", "reasoning", "code_review", "quant_analysis", "adversarial_review"] = "research"
    context: dict[str, Any] = Field(default_factory=dict)
    specialist_briefs: list[dict[str, Any]] = Field(default_factory=list)
    openai_plan: dict[str, Any] | None = None
    task_id: str | None = Field(default=None, max_length=64)
    search: bool | None = None
    execution: Literal["sync", "deferred"] = "sync"
    reasoning_effort: Literal["", "low", "medium", "high", "xhigh"] = ""


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


@router.get("/api/agents/grok/capabilities", response_model=ApiResponse)
def grok_capabilities() -> ApiResponse:
    return _ok(public_capabilities())


@router.post("/api/agents/grok", response_model=ApiResponse, dependencies=[Depends(_require_api_key_if_set)])
def grok_consult(body: GrokConsultBody) -> ApiResponse:
    try:
        from core.grok_agent import GrokAgent

        envelope = run_handoff(
            prompt=body.prompt,
            role=body.role,
            context=body.context,
            specialist_briefs=body.specialist_briefs,
            openai_plan=body.openai_plan,
            task_id=body.task_id,
            search=body.search,
            agent=GrokAgent(),
            deferred=body.execution == "deferred",
            reasoning_effort=body.reasoning_effort or None,
        )
        return _ok(envelope)
    except Exception as exc:
        LOG.warning("Grok consult failed: %s", exc)
        return _err("grok_consult", exc)
