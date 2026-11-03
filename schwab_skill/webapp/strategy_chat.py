"""
OpenAI tool loop for strategy design: validate StrategySpec and queue backtests.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI
from sqlalchemy.orm import Session

from .backtest_queue import create_and_queue_backtest
from .backtest_spec import parse_strategy_spec, spec_preview_dict
from .models import BacktestRun

STRATEGY_CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "validate_strategy_spec",
            "description": (
                "Validate a StrategySpec v1 object for the Stage 2 + VCP historical backtest. "
                "Returns a normalized preview or validation errors. Call this before queue_backtest."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "object",
                        "description": (
                            "StrategySpec: schema_version=1, universe_preset (sp1500|sp500|nasdaq100|sector_etfs|focused|custom), "
                            "universe_mode watchlist|tickers, tickers (when custom), scan_timeframe daily|weekly|monthly, "
                            "strategy_ids from the Scan studio catalog (trend_breakout, pullback, pead_primary, donchian_20, …), "
                            "start_date, end_date (YYYY-MM-DD), optional theory_name, cost fields, overrides "
                            "(stage2_52w_pct, stage2_sma_upward_days, vcp_days, quality_min_signal_score, "
                            "quality_gates_mode, breakout_confirm_enabled, forensic_*, pead_enabled, skip_mirofish)."
                        ),
                    },
                },
                "required": ["spec"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "queue_backtest",
            "description": (
                "Queue a validated historical backtest for the signed-in user. "
                "Only call after validate_strategy_spec succeeds for the same spec."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {"type": "object", "description": "Full StrategySpec v1 object."},
                },
                "required": ["spec"],
            },
        },
    },
]

STRATEGY_CHAT_SYSTEM = """You help users design and run historical backtests that match Scan studio.

The replay engine uses the same named indexes, catalog strategy_ids, and scanner tunables as a live scan.
Live order routing is still Stage 2 / VCP (`trend_breakout`). Shadow and paper sleeves (Donchian, weekly, PEAD, …)
replay the Scan studio evaluators historically; they do not promote plugins to LIVE.

Users can:
- pick universe_preset: sp1500, sp500, nasdaq100, sector_etfs, focused, or custom tickers
- pick strategy_ids from the catalog (one primary sleeve per horizon; paper rows opt-in)
- change Stage 2 / VCP / quality-score variables (stage2_52w_pct, vcp_days, quality_min_signal_score, …)
- set dates, friction (slippage/fees), and gate overrides
- label a hypothesis with theory_name

Do not invent a second engine or claim a new sleeve is LIVE.

Workflow:
1) Infer index, horizon, strategy_ids, dates, and overrides. Prefer validate_strategy_spec before queue_backtest.
2) If data is missing (dates, custom tickers), ask concise follow-ups.
3) After queue_backtest returns task_id, tell the user to poll GET /api/backtest-runs/tasks/{task_id} for results.

Never claim live trading execution from this chat; backtests are historical simulation only."""

_GROK_TOOL_HINT = (
    "\n\nWhen the user wants an adversarial review, a quantitative critique, or research that may need "
    "current public information, call consult_grok. Use role adversarial_review, quant_analysis, or research. "
    "Its verdict does not place an order and does not promote a plugin to LIVE."
)


def _recent_backtests_summary(db: Session, user_id: str) -> str:
    rows = (
        db.query(BacktestRun)
        .filter(BacktestRun.user_id == user_id)
        .order_by(BacktestRun.created_at.desc())
        .limit(5)
        .all()
    )
    if not rows:
        return "No prior backtests for this user."
    lines = []
    for r in rows:
        ca = r.created_at.isoformat() if r.created_at else ""
        lines.append(f"- run {r.id[:10]}… status={r.status} created={ca}")
    return "Recent backtests:\n" + "\n".join(lines)


def execute_strategy_tool(
    name: str,
    arguments: dict[str, Any],
    db: Session,
    user_id: str,
) -> dict[str, Any]:
    if name == "validate_strategy_spec":
        spec_obj = arguments.get("spec")
        if not isinstance(spec_obj, dict):
            return {"ok": False, "error": "spec must be an object"}
        try:
            spec = parse_strategy_spec(spec_obj)
            return {"ok": True, "preview": spec_preview_dict(spec)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if name == "queue_backtest":
        spec_obj = arguments.get("spec")
        if not isinstance(spec_obj, dict):
            return {"ok": False, "error": "spec must be an object"}
        try:
            return create_and_queue_backtest(db, user_id, spec_obj)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if name == "consult_grok":
        from core.hybrid_orchestrator import dispatch_orchestrator_tool

        return dispatch_orchestrator_tool(name, arguments if isinstance(arguments, dict) else {})
    return {"ok": False, "error": f"unknown tool: {name}"}


def strategy_chat_tools() -> list[dict[str, Any]]:
    """Tool list for one chat turn. Grok is included only when its mode is not off."""
    from config import get_grok_agent_mode
    from core.hybrid_orchestrator import attach_consult_grok_tool

    return attach_consult_grok_tool(STRATEGY_CHAT_TOOLS, get_grok_agent_mode())


def strategy_chat_system(recent: str) -> str:
    """System prompt. The Grok hint is present only when the tool is available."""
    from config import get_grok_agent_mode

    base = STRATEGY_CHAT_SYSTEM + "\n\n" + recent
    if get_grok_agent_mode() == "off":
        return base
    return base + _GROK_TOOL_HINT


def run_strategy_chat(db: Session, user_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    from core.llm_endpoint import resolve_llm_endpoint

    endpoint = resolve_llm_endpoint()
    api_key = endpoint.api_key
    if not api_key:
        raise RuntimeError("XAI_API_KEY, MIROFISH_API_KEY, or OPENAI_API_KEY is not set.")

    model = (os.getenv("STRATEGY_CHAT_MODEL") or endpoint.model or "gpt-4o-mini").strip()
    client = OpenAI(api_key=api_key, base_url=endpoint.base_url or None)

    recent = _recent_backtests_summary(db, user_id)
    openai_messages: list[dict[str, Any]] = [
        {"role": "system", "content": strategy_chat_system(recent)},
    ]
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if content is None:
            continue
        openai_messages.append({"role": role, "content": str(content)})

    tool_results_log: list[dict[str, Any]] = []
    max_rounds = 6
    for _ in range(max_rounds):
        completion = client.chat.completions.create(
            model=model,
            messages=openai_messages,
            tools=strategy_chat_tools(),
            tool_choice="auto",
        )
        msg = completion.choices[0].message
        if not msg.tool_calls:
            return {
                "message": msg.content or "",
                "tool_results": tool_results_log,
                "model": model,
            }

        openai_messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            }
        )
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            result = execute_strategy_tool(tc.function.name, args, db, user_id)
            tool_results_log.append({"tool": tc.function.name, "result": result})
            openai_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                }
            )

    return {
        "message": "Reached maximum tool-call rounds; see tool_results.",
        "tool_results": tool_results_log,
        "model": model,
    }
