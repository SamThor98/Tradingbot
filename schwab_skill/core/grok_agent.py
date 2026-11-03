"""Grok agent adapter for the xAI Chat Completions API.

Wire format follows the published grok-4.7 examples and the REST reference
fetched 2026-10-15:

- ``POST https://api.x.ai/v1/chat/completions``
- function tools and ``response_format.type=json_schema``
- ``search_parameters`` for live web/X retrieval
- ``stream=true`` server-sent events, terminated by ``data: [DONE]``
- deferred execution: ``deferred=true``, then
  ``GET /v1/chat/deferred-completion/{request_id}`` (200 ready, 202 pending)

``POST /v1/responses`` is the documented successor. Its ``background`` field is
unsupported, and the published grok-4.7 tool plus JSON-schema examples use
Chat Completions, so this adapter stays on that surface.

Grok 4.7 card (docs.x.ai model page, rate-limit page): 500,000 token context,
knowledge cutoff May 2026, input $2.00 / 1M, output $6.00 / 1M, reasoning
low|medium|high|xhigh, no realtime knowledge unless search is enabled.
US regional host ``us.api.x.ai`` is a 10% premium on token rates.
``usage.cost_in_usd_ticks`` is 10_000_000_000 ticks per dollar.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping
from urllib.parse import urlparse

LOG = logging.getLogger(__name__)

CHAT_COMPLETIONS_PATH = "/chat/completions"
RESPONSES_PATH = "/responses"
ALLOWED_HOSTS = frozenset({"api.x.ai", "us.api.x.ai"})
ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
ROLES = frozenset({"research", "reasoning", "code_review", "quant_analysis", "adversarial_review"})
USD_TICKS_PER_DOLLAR = 10_000_000_000
GROK_47_INPUT_USD_PER_MILLION = 2.0
GROK_47_OUTPUT_USD_PER_MILLION = 6.0
US_REGION_PREMIUM = 1.10
MAX_TOOL_ROUNDS = 4
SECRET_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "xai_api_key",
        "openai_api_key",
        "mirofish_api_key",
    }
)

GROK_47_CARD: dict[str, Any] = {
    "model": "grok-4.7",
    "context_window_tokens": 500_000,
    "knowledge_cutoff": "May 2026",
    "input_usd_per_million": GROK_47_INPUT_USD_PER_MILLION,
    "output_usd_per_million": GROK_47_OUTPUT_USD_PER_MILLION,
    "us_region_premium": US_REGION_PREMIUM,
    "reasoning_efforts": sorted(ALLOWED_EFFORTS),
    "realtime_without_search": False,
    "rate_limits": {
        "unit": "requests per second and tokens per minute",
        "tiers": {
            "T0": {"rps": 150, "tpm": "50M"},
            "T1": {"rps": 172, "tpm": "53M"},
            "T2": {"rps": 208, "tpm": "60M"},
            "T3": {"rps": 312, "tpm": "74M"},
            "T4": {"rps": 500, "tpm": "100M"},
        },
    },
    "sources": [
        "https://docs.x.ai/developers/grok-4-7",
        "https://docs.x.ai/developers/models",
        "https://docs.x.ai/developers/rate-limits",
        "https://docs.x.ai/developers/model-capabilities/text/structured-outputs",
        "https://docs.x.ai/developers/rest-api-reference/inference/chat",
    ],
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["proceed", "revise", "reject"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["claim", "evidence", "confidence"],
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "handoff": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cursor_task": {"type": "string"},
                "github_intent": {"type": "string"},
                "validation_commands": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["cursor_task", "github_intent", "validation_commands"],
        },
    },
    "required": ["verdict", "summary", "findings", "risks", "handoff"],
}

RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "grok_agent_review",
        "strict": True,
        "schema": REVIEW_SCHEMA,
    },
}

HANDOFF_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "record_cursor_task",
            "description": "Record a local coding task for Cursor. Does not edit files.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "acceptance": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "files", "acceptance"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_github_intent",
            "description": "Record a pull-request intent. Does not call GitHub.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "head_branch": {"type": "string"},
                },
                "required": ["title", "body", "head_branch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_validation_plan",
            "description": "Record validation commands. Does not run them.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"commands": {"type": "array", "items": {"type": "string"}}},
                "required": ["commands"],
            },
        },
    },
]

_ROLE_PROMPTS: dict[str, str] = {
    "research": "You are the research agent. Generate testable hypotheses from the payload.",
    "reasoning": "You are the reasoning agent. Make the argument and its weak points explicit.",
    "code_review": "You are the code-review agent. Review the described change for defects and missing tests.",
    "quant_analysis": "You are the quantitative analyst. Use only numbers present in the payload.",
    "adversarial_review": "You are the adversarial reviewer. Try to falsify the specialist brief.",
}
_SHARED_RULES = (
    " Use only the user payload and search citations. Do not invent prices, filings, or backtest results."
    " Do not place orders or claim a plugin is LIVE. Summary is at most two sentences and names the decision."
    " Each finding evidence is one sentence. Each risk is one short clause. Round prices to cents and rates"
    " to one decimal percent. cursor_task and github_intent must be empty strings unless the user asked for"
    " a code change. The final message must match the JSON schema."
)


class GrokAgentError(RuntimeError):
    """The xAI call failed or the deferred job did not finish."""


@dataclass
class PreparedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: dict[str, Any] | None = None


@dataclass
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    lines: Iterable[bytes]
    close: Callable[[], None] = field(default=lambda: None)

    def read(self) -> bytes:
        try:
            return b"".join(self.lines)
        finally:
            self.close()


Transport = Callable[[PreparedRequest, float], TransportResponse]
Sleeper = Callable[[float], None]


def urllib_transport(request: PreparedRequest, timeout: float) -> TransportResponse:
    """POST or GET JSON against an allowlisted xAI host."""
    data = None if request.body is None else json.dumps(request.body).encode("utf-8")
    req = urllib.request.Request(request.url, data=data, headers=request.headers, method=request.method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - host checked by caller
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        exc.close()
        return TransportResponse(exc.code, dict(exc.headers.items()), [raw])

    def _lines() -> Iterator[bytes]:
        try:
            yield from resp
        finally:
            resp.close()

    return TransportResponse(resp.status, dict(resp.headers.items()), _lines(), resp.close)


def normalize_base_url(url: str) -> str:
    """Return ``https://{host}/v1`` or raise if the host is not xAI."""
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if parsed.scheme != "https" or host not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise GrokAgentError("Grok base URL must be https://api.x.ai/v1 or https://us.api.x.ai/v1")
    if path not in {"", "/v1"} or parsed.query or parsed.fragment:
        raise GrokAgentError("Grok base URL path must be /v1")
    return f"https://{host}/v1"


def public_capabilities(skill_dir: Path | None = None) -> dict[str, Any]:
    """Non-secret description of the adapter and the published grok-4.7 card."""
    from config import (
        get_grok_agent_api_key,
        get_grok_agent_base_url,
        get_grok_agent_mode,
        get_grok_agent_model,
        get_grok_agent_search,
    )

    model = get_grok_agent_model(skill_dir)
    try:
        base = normalize_base_url(get_grok_agent_base_url(skill_dir))
        host = urlparse(base).hostname or ""
        base_error = ""
    except GrokAgentError as exc:
        host = ""
        base_error = str(exc)
    return {
        "mode": get_grok_agent_mode(skill_dir),
        "model": model,
        "host": host,
        "base_url_error": base_error,
        "key_configured": bool(get_grok_agent_api_key(skill_dir)),
        "search_default": get_grok_agent_search(skill_dir),
        "roles": sorted(ROLES),
        "wire": {
            "api": "responses",
            "create": "POST /v1/responses",
            "structured_output": "text.format.type=json_schema",
            "tools": "functions, plus web_search and x_search when search is on",
            "search": "server-side web_search and x_search; search_parameters is not sent",
            "streaming": "stream=true SSE",
            "responses_background": "unsupported; deferred dashboard calls use the synchronous Responses API",
        },
        "grok_4_7": GROK_47_CARD,
        "configured_model_priced_here": model == "grok-4.7",
    }


def redact(value: Any, depth: int = 0) -> Any:
    """Drop secret-shaped dict keys before a payload leaves the process."""
    if depth > 8:
        return "[truncated]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name.lower() in SECRET_KEY_NAMES or name.lower().endswith("_key") or name.lower().endswith("_token"):
                out[name] = "[redacted]"
            else:
                out[name] = redact(item, depth + 1)
        return out
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value[:50]]
    if isinstance(value, str) and len(value) > 4000:
        return value[:4000] + "...[truncated]"
    return value


def usage_from_payload(payload: Mapping[str, Any], *, model: str, host: str) -> dict[str, Any]:
    """Token counts plus billed ticks, or the grok-4.7 list price when ticks are absent."""
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt_details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    if not prompt_details and isinstance(usage.get("prompt_tokens_details"), dict):
        prompt_details = usage["prompt_tokens_details"]
    completion_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    if not completion_details and isinstance(usage.get("completion_tokens_details"), dict):
        completion_details = usage["completion_tokens_details"]
    prompt = _as_int(usage.get("input_tokens") if usage.get("input_tokens") is not None else usage.get("prompt_tokens"))
    completion = _as_int(
        usage.get("output_tokens") if usage.get("output_tokens") is not None else usage.get("completion_tokens")
    )
    ticks = usage.get("cost_in_usd_ticks")
    cost: float | None = None
    source = ""
    if isinstance(ticks, int) and not isinstance(ticks, bool):
        cost = ticks / USD_TICKS_PER_DOLLAR
        source = "cost_in_usd_ticks"
    elif model == "grok-4.7":
        cost = (prompt * GROK_47_INPUT_USD_PER_MILLION + completion * GROK_47_OUTPUT_USD_PER_MILLION) / 1_000_000
        if host == "us.api.x.ai":
            cost *= US_REGION_PREMIUM
        source = "list_price"
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": _as_int(usage.get("total_tokens")) or (prompt + completion),
        "reasoning_tokens": _as_int(completion_details.get("reasoning_tokens")),
        "cached_tokens": _as_int(prompt_details.get("cached_tokens")),
        "num_sources_used": _as_int(usage.get("num_sources_used")),
        "cost_usd": None if cost is None else round(cost, 8),
        "cost_source": source,
    }


def add_usage(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    if _as_int(left.get("total_tokens")) == 0 and left.get("cost_usd") is None:
        return dict(right)
    numeric = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "num_sources_used",
    )
    merged: dict[str, Any] = {key: _as_int(left.get(key)) + _as_int(right.get(key)) for key in numeric}
    if left.get("cost_usd") is None or right.get("cost_usd") is None:
        merged["cost_usd"] = None
        merged["cost_source"] = ""
    else:
        merged["cost_usd"] = round(float(left["cost_usd"]) + float(right["cost_usd"]), 8)
        merged["cost_source"] = (
            str(left.get("cost_source") or "") if left.get("cost_source") == right.get("cost_source") else "mixed"
        )
    return merged


def empty_usage() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "num_sources_used": 0,
        "cost_usd": None,
        "cost_source": "",
    }


def iter_chat_sse_text(lines: Iterable[str]) -> Iterator[str]:
    """Yield visible content deltas from a Chat Completions SSE body."""
    for line in lines:
        text = line.strip()
        if not text.startswith("data:"):
            continue
        data = text[5:].strip()
        if not data or data == "[DONE]":
            if data == "[DONE]":
                return
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                yield delta
            continue
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            content = delta.get("content")
            if isinstance(content, str) and content:
                yield content


@dataclass
class GrokConsultation:
    status: str
    mode: str
    model: str
    role: str
    text: str = ""
    review: dict[str, Any] | None = None
    citations: list[Any] = field(default_factory=list)
    tool_records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=empty_usage)
    response_id: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "model": self.model,
            "role": self.role,
            "text": self.text,
            "review": self.review,
            "citations": self.citations,
            "tool_records": self.tool_records,
            "usage": self.usage,
            "response_id": self.response_id,
            "error": self.error,
        }


class GrokAgent:
    """Callable Grok specialist. ``off`` performs no network I/O."""

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        sleeper: Sleeper | None = None,
        skill_dir: Path | None = None,
    ) -> None:
        self.transport = transport or urllib_transport
        self.sleeper = sleeper or time.sleep
        self.skill_dir = skill_dir

    def consult(
        self,
        *,
        prompt: str,
        role: str = "research",
        context: dict[str, Any] | None = None,
        task_id: str | None = None,
        search: bool | None = None,
        specialist_briefs: list[dict[str, Any]] | None = None,
        deferred: bool = False,
        reasoning_effort: str | None = None,
    ) -> GrokConsultation:
        settings = self._settings(role, search, reasoning_effort)
        if settings["mode"] == "off":
            LOG.info("Grok consultation skipped: mode=off")
            return GrokConsultation(status="skipped", mode="off", model=settings["model"], role=role)
        input_items = [_user_message(prompt, context, specialist_briefs)]
        records: dict[str, list[dict[str, Any]]] = {"cursor": [], "github": [], "validation": []}
        usage = empty_usage()
        citations: list[Any] = []
        response_id = ""
        last_text = ""
        for _ in range(MAX_TOOL_ROUNDS):
            payload = self._complete(settings, input_items, task_id=task_id, tools=True, deferred=False)
            usage = add_usage(usage, usage_from_payload(payload, model=settings["model"], host=settings["host"]))
            response_id = str(payload.get("id") or response_id)
            citations.extend(_citations(payload))
            last_text = _response_text(payload)
            calls = _response_calls(payload)
            if not calls:
                break
            input_items.extend(calls)
            for call in calls:
                name, args, call_id = _parse_response_call(call)
                result = _execute_handoff_tool(name, args, records)
                input_items.append(
                    {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, default=str)}
                )
        else:
            return self._finish(
                settings,
                role,
                "incomplete",
                last_text,
                None,
                citations,
                records,
                usage,
                response_id,
                "tool-call round limit reached",
            )
        review, error = _parse_review(last_text)
        status = "completed" if review is not None else "incomplete"
        return self._finish(settings, role, status, last_text, review, citations, records, usage, response_id, error)

    def consult_deferred(self, **kwargs: Any) -> GrokConsultation:
        """Responses background is unsupported, so this is the synchronous call."""
        kwargs["deferred"] = False
        return self.consult(**kwargs)

    def consult_stream(
        self,
        *,
        prompt: str,
        role: str = "research",
        context: dict[str, Any] | None = None,
        search: bool | None = None,
    ) -> Iterator[str]:
        """Yield text deltas from one streaming completion. Does not run tools."""
        settings = self._settings(role, search)
        if settings["mode"] == "off":
            LOG.info("Grok stream skipped: mode=off")
            return
        if not settings["api_key"]:
            raise GrokAgentError("XAI_API_KEY or MIROFISH_API_KEY is not set")
        body = _responses_body(
            settings,
            [_user_message(prompt, context, None)],
            task_id=None,
            tools=False,
            stream=True,
        )
        raw = self._send("POST", settings["base"] + RESPONSES_PATH, body, settings)
        text = raw.decode("utf-8", "replace")
        yield from iter_chat_sse_text(text.splitlines())

    def _settings(self, role: str, search: bool | None, reasoning_effort: str | None = None) -> dict[str, Any]:
        from config import (
            get_grok_agent_api_key,
            get_grok_agent_base_url,
            get_grok_agent_deferred_polls,
            get_grok_agent_max_completion_tokens,
            get_grok_agent_max_retries,
            get_grok_agent_mode,
            get_grok_agent_model,
            get_grok_agent_reasoning_effort,
            get_grok_agent_search,
            get_grok_agent_timeout_sec,
        )

        if role not in ROLES:
            raise ValueError(f"unknown grok role: {role}")
        effort = (reasoning_effort if reasoning_effort is not None else get_grok_agent_reasoning_effort(self.skill_dir))
        effort = str(effort or "").strip().lower()
        if effort and effort not in ALLOWED_EFFORTS:
            raise ValueError("GROK_AGENT_REASONING_EFFORT must be low, medium, high, or xhigh")
        base = normalize_base_url(get_grok_agent_base_url(self.skill_dir))
        return {
            "mode": get_grok_agent_mode(self.skill_dir),
            "model": get_grok_agent_model(self.skill_dir),
            "role": role,
            "base": base,
            "host": urlparse(base).hostname or "",
            "api_key": get_grok_agent_api_key(self.skill_dir),
            "search": get_grok_agent_search(self.skill_dir) if search is None else bool(search),
            "effort": effort,
            "timeout": float(get_grok_agent_timeout_sec(self.skill_dir)),
            "max_tokens": get_grok_agent_max_completion_tokens(self.skill_dir),
            "retries": get_grok_agent_max_retries(self.skill_dir),
            "polls": get_grok_agent_deferred_polls(self.skill_dir),
        }

    def _complete(
        self,
        settings: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        task_id: str | None,
        tools: bool,
        deferred: bool,
    ) -> dict[str, Any]:
        if not settings["api_key"]:
            raise GrokAgentError("XAI_API_KEY or MIROFISH_API_KEY is not set")
        body = _responses_body(settings, messages, task_id=task_id, tools=tools, stream=False)
        raw = self._send("POST", settings["base"] + RESPONSES_PATH, body, settings)
        return _json_object(raw)

    def _send(self, method: str, url: str, body: dict[str, Any] | None, settings: dict[str, Any]) -> bytes:
        raw, _status = self._send_status(method, url, body, settings)
        return raw

    def _send_status(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        settings: dict[str, Any],
    ) -> tuple[bytes, int]:
        headers = {
            "Authorization": f"Bearer {settings['api_key']}",
            "Accept": "application/json",
            "User-Agent": "TradingBot-grok-agent",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = PreparedRequest(method=method, url=url, headers=headers, body=body)
        attempts = int(settings["retries"]) + 1
        for attempt in range(attempts):
            response = self.transport(request, float(settings["timeout"]))
            if response.status == 429 and attempt < attempts - 1:
                retry_after = _retry_after(response.headers)
                response.read()
                self.sleeper(retry_after if retry_after is not None else min(30.0, float(2**attempt)))
                continue
            raw = response.read()
            if response.status >= 400:
                LOG.warning("Grok request failed: status=%s path=%s", response.status, urlparse(url).path)
                raise _http_error(response.status, raw)
            return raw, response.status
        raise GrokAgentError("xAI HTTP 429")

    def _finish(
        self,
        settings: dict[str, Any],
        role: str,
        status: str,
        text: str,
        review: dict[str, Any] | None,
        citations: list[Any],
        records: dict[str, list[dict[str, Any]]],
        usage: dict[str, Any],
        response_id: str,
        error: str,
    ) -> GrokConsultation:
        LOG.info(
            "Grok consultation finished: model=%s role=%s status=%s prompt_tokens=%s",
            settings["model"],
            role,
            status,
            usage.get("prompt_tokens"),
        )
        return GrokConsultation(
            status=status,
            mode=str(settings["mode"]),
            model=str(settings["model"]),
            role=role,
            text=text,
            review=review,
            citations=citations,
            tool_records=records,
            usage=usage,
            response_id=response_id,
            error=error,
        )


def _responses_tools(search: bool) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in HANDOFF_TOOLS:
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        tools.append(
            {
                "type": "function",
                "name": function.get("name"),
                "description": function.get("description"),
                "parameters": function.get("parameters"),
            }
        )
    if search:
        tools.extend([{"type": "web_search"}, {"type": "x_search"}])
    return tools


def _responses_body(
    settings: dict[str, Any],
    input_items: list[dict[str, Any]],
    *,
    task_id: str | None,
    tools: bool,
    stream: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": settings["model"],
        "instructions": _system_message(str(settings.get("role") or "research"))["content"],
        "input": input_items,
        "max_output_tokens": settings["max_tokens"],
        "store": False,
    }
    if tools and not stream:
        body["tools"] = _responses_tools(bool(settings["search"]))
        body["tool_choice"] = "auto"
        body["text"] = {
            "format": {
                "type": "json_schema",
                "name": "grok_agent_review",
                "strict": True,
                "schema": REVIEW_SCHEMA,
            }
        }
    if settings["effort"]:
        body["reasoning"] = {"effort": settings["effort"]}
    cache_key = _cache_key(task_id)
    if cache_key:
        body["prompt_cache_key"] = cache_key
    if stream:
        body["stream"] = True
    return body


def _response_text(payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "output_text" and block.get("text"):
                parts.append(str(block["text"]))
    return "".join(parts)


def _response_calls(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in payload.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "function_call":
            calls.append(item)
    return calls


def _parse_response_call(call: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    raw_args = call.get("arguments")
    if isinstance(raw_args, dict):
        args = raw_args
    else:
        try:
            parsed = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            parsed = {}
        args = parsed if isinstance(parsed, dict) else {}
    return str(call.get("name") or ""), args, str(call.get("call_id") or call.get("id") or "")


def _system_message(role: str) -> dict[str, str]:
    return {"role": "system", "content": _ROLE_PROMPTS[role] + _SHARED_RULES}


def _user_message(
    prompt: str,
    context: dict[str, Any] | None,
    briefs: list[dict[str, Any]] | None,
) -> dict[str, str]:
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("prompt is required")
    payload = {
        "task": text[:20000],
        "context": redact(context or {}),
        "specialist_briefs": redact(list(briefs or [])[:8]),
    }
    encoded = json.dumps(payload, default=str)
    if len(encoded) > 60000:
        payload["context"] = {"truncated": True}
        encoded = json.dumps(payload, default=str)
    return {"role": "user", "content": encoded}


def _assistant_message(payload: Mapping[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise GrokAgentError("chat completion did not include choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise GrokAgentError("chat completion did not include a message")
    refusal = message.get("refusal")
    if refusal:
        raise GrokAgentError(f"model refusal: {str(refusal)[:300]}")
    return message


def _assistant_wire(message: Mapping[str, Any]) -> dict[str, Any]:
    calls = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {}, default=str)
        calls.append(
            {
                "id": str(call.get("id") or ""),
                "type": "function",
                "function": {"name": str(function.get("name") or ""), "arguments": arguments},
            }
        )
    return {"role": "assistant", "content": message.get("content") or "", "tool_calls": calls}


def _parse_tool_call(call: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(function.get("name") or "")
    raw_args = function.get("arguments")
    if isinstance(raw_args, dict):
        args = raw_args
    else:
        try:
            parsed = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            parsed = {}
        args = parsed if isinstance(parsed, dict) else {}
    return name, args, str(call.get("id") or "")


def _execute_handoff_tool(
    name: str, args: Mapping[str, Any], records: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    if name == "record_cursor_task":
        item = {
            "summary": str(args.get("summary") or "")[:2000],
            "files": _str_list(args.get("files"), 20),
            "acceptance": _str_list(args.get("acceptance"), 20),
        }
        records["cursor"].append(item)
        return {"ok": True, "recorded": True}
    if name == "record_github_intent":
        item = {
            "title": str(args.get("title") or "")[:200],
            "body": str(args.get("body") or "")[:4000],
            "head_branch": str(args.get("head_branch") or "")[:80],
        }
        records["github"].append(item)
        return {"ok": True, "recorded": True}
    if name == "record_validation_plan":
        item = {"commands": _str_list(args.get("commands"), 20)}
        records["validation"].append(item)
        return {"ok": True, "recorded": True}
    return {"ok": False, "error": "unknown tool"}


def _parse_review(text: str) -> tuple[dict[str, Any] | None, str]:
    raw = str(text or "").strip()
    if not raw:
        return None, "empty completion"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None, "completion was not JSON"
        try:
            payload = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None, "completion was not JSON"
    if not isinstance(payload, dict):
        return None, "completion JSON was not an object"
    verdict = payload.get("verdict")
    if verdict not in {"proceed", "revise", "reject"}:
        return None, "verdict was not proceed, revise, or reject"
    return payload, ""


def _citations(payload: Mapping[str, Any]) -> list[Any]:
    found: list[Any] = []
    top = payload.get("citations")
    if isinstance(top, list):
        found.extend(top)
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("citations"), list):
            found.extend(message["citations"])
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            for note in block.get("annotations") or []:
                if isinstance(note, dict) and note.get("url"):
                    found.append(note["url"])
    return found


def _cache_key(task_id: str | None) -> str:
    text = str(task_id or "").strip()
    if text and len(text) <= 64 and all(ch.isalnum() or ch in "-_" for ch in text):
        return text
    return ""


def _str_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:500] for item in value[:limit]]


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw = ""
    for key, value in headers.items():
        if str(key).lower() == "retry-after":
            raw = str(value).strip()
            break
    if not raw:
        return None
    try:
        return min(30.0, max(0.0, float(raw)))
    except ValueError:
        return None


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GrokAgentError("xAI response was not JSON") from exc
    if not isinstance(payload, dict):
        raise GrokAgentError("xAI response JSON was not an object")
    return payload


def _http_error(status: int, raw: bytes) -> GrokAgentError:
    text = raw[:300].decode("utf-8", "replace")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            text = str(err["message"])[:300]
        elif isinstance(err, str):
            text = err[:300]
    return GrokAgentError(f"xAI HTTP {status}: {text}")
