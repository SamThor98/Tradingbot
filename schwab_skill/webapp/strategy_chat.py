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
                            "StrategySpec: schema_version=1, universe_mode watchlist|tickers, "
                            "tickers (when universe_mode is tickers), start_date, end_date (YYYY-MM-DD), "
                            "optional theory_name, cost fields, overrides (quality_gates_mode, "
                            "breakout_confirm_enabled, forensic_*, pead_enabled, skip_mirofish)."
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

STRATEGY_CHAT_SYSTEM = """You help users design and run historical backtests for a Stage 2 + VCP breakout-style scanner.

The engine is fixed; users tune universe, date range, friction (slippage/fees), and a small set of
gate overrides (quality gates, breakout confirmation, forensic filter, PEAD, optional skip for LLM/MiroFish during backtest).

Workflow:
1) Infer dates, universe, and overrides from the user. Prefer validate_strategy_spec before queue_backtest.
2) If data is missing (dates, tickers vs watchlist), ask concise follow-ups.
3) After queue_backtest returns task_id, tell the user to poll GET /api/backtest-runs/tasks/{task_id} for results.

Never claim live trading execution from this chat; backtests are historical simulation only."""


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
    return {"ok": False, "error": f"unknown tool: {name}"}


def run_strategy_chat(db: Session, user_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("MIROFISH_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY (or MIROFISH_API_KEY) is not set.")

    model = (os.getenv("STRATEGY_CHAT_MODEL") or os.getenv("LLM_MODEL_NAME") or "gpt-4o-mini").strip()
    client = OpenAI(api_key=api_key)

    recent = _recent_backtests_summary(db, user_id)
    openai_messages: list[dict[str, Any]] = [
        {"role": "system", "content": STRATEGY_CHAT_SYSTEM + "\n\n" + recent},
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
            tools=STRATEGY_CHAT_TOOLS,
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
