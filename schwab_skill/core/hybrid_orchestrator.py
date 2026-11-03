"""Handoff from an OpenAI orchestrator through Grok to recorded downstream work.

Stages, in order:

user → openai_orchestrator → specialist → grok → cursor → github → validation

Cursor and GitHub are not called unless the caller injects a sink, the agent
mode is ``live``, and Grok's verdict is ``proceed``. This process never runs
validation commands and never places orders.

Register ``CONSULT_GROK_TOOL`` with the OpenAI Agents SDK (or any Chat
Completions tool list) and execute it with ``dispatch_orchestrator_tool``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol

from core.grok_agent import GrokAgent, GrokAgentError

LOG = logging.getLogger(__name__)

STAGES = (
    "user",
    "openai_orchestrator",
    "specialist",
    "grok",
    "cursor",
    "github",
    "validation",
)

ALLOWED_VALIDATION = (
    "python -m ruff check .",
    "python -m ruff format --check .",
    "python -m pytest -q",
    "python scripts/validate_all.py --profile local --strict --skip-backtest",
)
_ALLOWED_VALIDATION = frozenset(ALLOWED_VALIDATION)

CONSULT_GROK_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "consult_grok",
        "description": (
            "Ask the Grok research, reasoning, code-review, quantitative, or adversarial agent. "
            "Returns a structured verdict and recorded handoffs. Does not place orders or open pull requests."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "prompt": {"type": "string"},
                "role": {
                    "type": "string",
                    "enum": ["research", "reasoning", "code_review", "quant_analysis", "adversarial_review"],
                },
                "context": {"type": "object"},
                "task_id": {"type": "string"},
                "search": {"type": "boolean"},
            },
            "required": ["prompt", "role"],
        },
    },
}


def attach_consult_grok_tool(tools: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Append the Grok tool when the agent mode is shadow or live."""
    if str(mode or "").strip().lower() == "off":
        return list(tools)
    return [*list(tools), CONSULT_GROK_TOOL]


class HandoffSink(Protocol):
    """Optional Cursor and GitHub clients. The default orchestrator has neither."""

    def submit_cursor(self, task: dict[str, Any]) -> dict[str, Any]:
        """Accept a coding task and return a receipt."""

    def submit_github(self, intent: dict[str, Any]) -> dict[str, Any]:
        """Accept a pull-request intent and return a receipt."""


def dispatch_orchestrator_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Execute the ``consult_grok`` tool call from an OpenAI orchestrator."""
    if name != "consult_grok":
        return {"ok": False, "error": f"unknown tool: {name}"}
    args = arguments if isinstance(arguments, dict) else {}
    try:
        handoff = run_handoff(
            prompt=str(args.get("prompt") or ""),
            role=str(args.get("role") or "research"),
            context=args.get("context") if isinstance(args.get("context"), dict) else {},
            task_id=str(args.get("task_id") or "") or None,
            search=args.get("search") if isinstance(args.get("search"), bool) else None,
        )
    except (GrokAgentError, ValueError) as exc:
        LOG.warning("consult_grok failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "handoff": handoff}


def run_handoff(
    *,
    prompt: str,
    role: str = "research",
    context: dict[str, Any] | None = None,
    specialist_briefs: list[dict[str, Any]] | None = None,
    openai_plan: dict[str, Any] | None = None,
    task_id: str | None = None,
    search: bool | None = None,
    agent: GrokAgent | None = None,
    sink: HandoffSink | None = None,
    deferred: bool = False,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Walk the seven stages and return a JSON-serializable envelope."""
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("prompt is required")
    task = str(task_id or "").strip() or uuid.uuid4().hex
    briefs = [item for item in (specialist_briefs or []) if isinstance(item, dict)][:8]
    plan, planner = _plan(role, openai_plan)
    resolved_role = str(plan.get("role") or role or "research")
    stages: list[dict[str, Any]] = [
        {"stage": "user", "status": "accepted", "task_id": task},
        {"stage": "openai_orchestrator", "status": "planned", "planner": planner, "plan": plan},
        {
            "stage": "specialist",
            "status": "attached" if briefs else "empty",
            "count": len(briefs),
        },
    ]
    worker = agent or GrokAgent()
    try:
        consultation = worker.consult(
            prompt=text,
            role=resolved_role,
            context=context,
            task_id=task,
            search=search,
            specialist_briefs=briefs,
            deferred=deferred,
            reasoning_effort=reasoning_effort,
        )
    except (GrokAgentError, ValueError) as exc:
        LOG.warning("Grok handoff failed: %s", exc)
        stages.append({"stage": "grok", "status": "error", "error": str(exc)})
        stages.extend(_blocked(reason=str(exc)))
        return {"task_id": task, "mode": "", "stage_order": list(STAGES), "stages": stages, "usage": {}}

    result = consultation.to_dict()
    stages.append({"stage": "grok", "status": consultation.status, "result": result})
    proceed = consultation.status == "completed" and (consultation.review or {}).get("verdict") == "proceed"
    eligible = consultation.mode == "live" and proceed
    cursor_task = _cursor_task(consultation.tool_records, consultation.review)
    github_intent = _github_intent(consultation.tool_records, consultation.review)
    if consultation.status == "skipped":
        stages.extend(_blocked(reason="GROK_AGENT_MODE=off"))
    else:
        cursor_stage = _cursor_stage(cursor_task, eligible=eligible, sink=sink if eligible else None)
        stages.append(cursor_stage)
        github_eligible = eligible and cursor_stage.get("status") != "error"
        stages.append(_github_stage(github_intent, eligible=github_eligible, sink=sink if github_eligible else None))
        stages.append(_validation_stage(consultation.tool_records, consultation.review, proceed=proceed))
    return {
        "task_id": task,
        "mode": consultation.mode,
        "stage_order": list(STAGES),
        "stages": stages,
        "usage": consultation.usage,
    }


def _plan(role: str, openai_plan: dict[str, Any] | None) -> tuple[dict[str, Any], str]:
    if isinstance(openai_plan, dict) and openai_plan:
        plan = dict(openai_plan)
        plan.setdefault("role", role)
        return plan, "openai"
    return {
        "role": role,
        "reason": "No OpenAI plan was attached. The local planner assigned the Grok role from the request.",
    }, "local"


def _blocked(*, reason: str) -> list[dict[str, Any]]:
    return [
        {"stage": "cursor", "status": "blocked", "executed": False, "eligible": False, "reason": reason},
        {"stage": "github", "status": "blocked", "executed": False, "eligible": False, "reason": reason},
        {"stage": "validation", "status": "blocked", "executed": False, "eligible": False, "reason": reason},
    ]


def _cursor_task(records: dict[str, Any], review: dict[str, Any] | None) -> dict[str, Any]:
    recorded = records.get("cursor") if isinstance(records, dict) else None
    if isinstance(recorded, list) and recorded and isinstance(recorded[-1], dict):
        task = dict(recorded[-1])
        task["source"] = "tool"
        return task
    handoff = (review or {}).get("handoff") if isinstance(review, dict) else None
    summary = ""
    if isinstance(handoff, dict):
        summary = str(handoff.get("cursor_task") or "")
    if not summary and isinstance(review, dict):
        summary = str(review.get("summary") or "")
    return {"summary": summary[:2000], "files": [], "acceptance": [], "source": "review"}


def _github_intent(records: dict[str, Any], review: dict[str, Any] | None) -> dict[str, Any]:
    recorded = records.get("github") if isinstance(records, dict) else None
    if isinstance(recorded, list) and recorded and isinstance(recorded[-1], dict):
        intent = dict(recorded[-1])
        intent["source"] = "tool"
        return intent
    handoff = (review or {}).get("handoff") if isinstance(review, dict) else None
    body = str((handoff or {}).get("github_intent") or "") if isinstance(handoff, dict) else ""
    title = str((review or {}).get("summary") or "")[:80] if body else ""
    return {"title": title, "body": body[:4000], "head_branch": "", "source": "review"}


def _cursor_stage(task: dict[str, Any], *, eligible: bool, sink: HandoffSink | None) -> dict[str, Any]:
    stage: dict[str, Any] = {
        "stage": "cursor",
        "status": "recorded",
        "executed": False,
        "eligible": eligible,
        "task": task,
    }
    if sink is None or not task.get("summary"):
        stage["reason"] = "cursor client is not configured; task recorded for the operator"
        return stage
    try:
        receipt = sink.submit_cursor(task)
    except Exception as exc:
        LOG.warning("Cursor handoff failed: %s", exc)
        stage["status"] = "error"
        stage["error"] = str(exc)
        return stage
    stage["receipt"] = receipt if isinstance(receipt, dict) else {"ok": True}
    stage["executed"] = bool((stage["receipt"] or {}).get("executed"))
    return stage


def _github_stage(intent: dict[str, Any], *, eligible: bool, sink: HandoffSink | None) -> dict[str, Any]:
    stage: dict[str, Any] = {
        "stage": "github",
        "status": "empty" if not (intent.get("title") or intent.get("body")) else "recorded",
        "executed": False,
        "eligible": eligible and bool(intent.get("title") or intent.get("body")),
        "intent": intent,
    }
    if sink is None or stage["status"] == "empty":
        stage["reason"] = "github client is not configured; intent recorded for the operator"
        return stage
    try:
        receipt = sink.submit_github(intent)
    except Exception as exc:
        LOG.warning("GitHub handoff failed: %s", exc)
        stage["status"] = "error"
        stage["error"] = str(exc)
        return stage
    stage["receipt"] = receipt if isinstance(receipt, dict) else {"ok": True}
    stage["executed"] = bool((stage["receipt"] or {}).get("executed"))
    return stage


def _validation_stage(
    records: dict[str, Any],
    review: dict[str, Any] | None,
    *,
    proceed: bool,
) -> dict[str, Any]:
    proposed: list[str] = []
    recorded = records.get("validation") if isinstance(records, dict) else None
    if isinstance(recorded, list):
        for item in recorded:
            if isinstance(item, dict):
                proposed.extend(str(cmd) for cmd in item.get("commands") or [])
    handoff = (review or {}).get("handoff") if isinstance(review, dict) else None
    if isinstance(handoff, dict):
        proposed.extend(str(cmd) for cmd in handoff.get("validation_commands") or [])
    accepted: list[str] = []
    rejected: list[str] = []
    for command in proposed:
        cleaned = command.strip()
        if not cleaned:
            continue
        if cleaned in _ALLOWED_VALIDATION:
            if cleaned not in accepted:
                accepted.append(cleaned)
        elif cleaned not in rejected:
            rejected.append(cleaned[:200])
    return {
        "stage": "validation",
        "status": "recorded",
        "executed": False,
        "eligible": False,
        "accepted_commands": accepted,
        "rejected_commands": rejected,
        "recommended_commands": list(ALLOWED_VALIDATION) if proceed else [],
        "reason": "validation commands are recorded for the operator; this process does not run them",
    }
