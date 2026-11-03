"""Grok agent adapter and hybrid handoff. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.grok_agent import (
    GrokAgent,
    GrokAgentError,
    TransportResponse,
    iter_chat_sse_text,
    public_capabilities,
    usage_from_payload,
)
from core.hybrid_orchestrator import dispatch_orchestrator_tool, run_handoff

_ENV_KEYS = (
    "GROK_AGENT_MODE",
    "GROK_AGENT_MODEL",
    "GROK_AGENT_BASE_URL",
    "GROK_AGENT_SEARCH",
    "GROK_AGENT_REASONING_EFFORT",
    "GROK_AGENT_MAX_RETRIES",
    "GROK_AGENT_DEFERRED_POLLS",
    "XAI_API_KEY",
    "MIROFISH_API_KEY",
    "OPENAI_API_KEY",
    "WEB_API_KEY",
    "RENDER",
    "ENV",
    "APP_ENV",
)


@pytest.fixture(autouse=True)
def _clear_grok_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _review(verdict: str = "proceed") -> dict:
    return {
        "verdict": verdict,
        "summary": "The sample is too small to promote.",
        "findings": [{"claim": "n is 12", "evidence": "payload n=12", "confidence": 0.4}],
        "risks": ["overfit"],
        "handoff": {
            "cursor_task": "Add a regression test",
            "github_intent": "Test-only change",
            "validation_commands": ["python -m pytest -q", "curl https://evil.example"],
        },
    }


def _completion(content: str | None, *, tool_calls: list | None = None, usage: dict | None = None) -> dict:
    output: list[dict] = []
    if tool_calls:
        for call in tool_calls:
            function = call["function"]
            output.append(
                {
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": function["name"],
                    "arguments": function["arguments"],
                }
            )
    else:
        output.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": content or "",
                        "annotations": [{"type": "url_citation", "url": "https://docs.x.ai/developers/models"}],
                    }
                ],
            }
        )
    return {
        "id": "resp-1",
        "status": "completed",
        "output": output,
        "usage": usage
        or {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "cost_in_usd_ticks": 10_000_000_000,
            "input_tokens_details": {"cached_tokens": 4},
            "output_tokens_details": {"reasoning_tokens": 6},
            "num_sources_used": 1,
        },
    }


def _script(items: list):
    calls = []
    queue = list(items)

    def transport(request, timeout):
        status, body, *rest = queue.pop(0)
        headers = rest[0] if rest else {}
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        calls.append(request)
        return TransportResponse(status, headers, [raw])

    return transport, calls


def _agent(tmp_path, transport, monkeypatch, **env: str) -> GrokAgent:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return GrokAgent(transport=transport, sleeper=lambda _seconds: None, skill_dir=tmp_path)


def test_mode_off_does_not_call_transport(tmp_path) -> None:
    def boom(request, timeout):
        raise AssertionError(request.url)

    result = GrokAgent(transport=boom, skill_dir=tmp_path).consult(prompt="Review the gate.")
    assert result.status == "skipped"
    assert result.mode == "off"
    assert public_capabilities(tmp_path)["key_configured"] is False
    assert public_capabilities(tmp_path)["grok_4_7"]["context_window_tokens"] == 500_000


def test_openai_key_is_not_sent_to_xai(tmp_path, monkeypatch) -> None:
    transport, calls = _script([])
    agent = _agent(tmp_path, transport, monkeypatch, GROK_AGENT_MODE="shadow", OPENAI_API_KEY="sk-openai")
    with pytest.raises(GrokAgentError, match="XAI_API_KEY"):
        agent.consult(prompt="Review the gate.")
    assert calls == []


def test_rejects_non_xai_host(tmp_path, monkeypatch) -> None:
    transport, calls = _script([])
    agent = _agent(
        tmp_path,
        transport,
        monkeypatch,
        GROK_AGENT_MODE="shadow",
        XAI_API_KEY="xai-test",
        GROK_AGENT_BASE_URL="https://api.openai.com/v1",
    )
    with pytest.raises(GrokAgentError, match="api.x.ai"):
        agent.consult(prompt="Review the gate.")
    assert calls == []


def test_chat_completion_body_search_and_schema(tmp_path, monkeypatch) -> None:
    review = json.dumps(_review())
    transport, calls = _script([(200, _completion(review))])
    agent = _agent(
        tmp_path,
        transport,
        monkeypatch,
        GROK_AGENT_MODE="shadow",
        XAI_API_KEY="xai-test",
        OPENAI_API_KEY="sk-openai",
        GROK_AGENT_REASONING_EFFORT="high",
        GROK_AGENT_SEARCH="true",
    )
    result = agent.consult(
        prompt="What changed in the model card?",
        role="research",
        context={"api_key": "super-secret", "note": "payload"},
        task_id="task_123",
    )
    assert result.status == "completed"
    assert result.review["verdict"] == "proceed"
    assert result.usage["cost_usd"] == 1.0
    assert result.usage["cost_source"] == "cost_in_usd_ticks"
    assert result.usage["reasoning_tokens"] == 6
    assert result.citations == ["https://docs.x.ai/developers/models"]
    request = calls[0]
    assert request.method == "POST"
    assert request.url == "https://api.x.ai/v1/responses"
    assert request.headers["Authorization"] == "Bearer xai-test"
    assert "sk-openai" not in json.dumps(request.body)
    assert "super-secret" not in json.dumps(request.body)
    assert request.body["model"] == "grok-4.7"
    assert request.body["reasoning"]["effort"] == "high"
    assert request.body["prompt_cache_key"] == "task_123"
    assert "search_parameters" not in request.body
    tool_types = [tool["type"] for tool in request.body["tools"]]
    assert "web_search" in tool_types
    assert "x_search" in tool_types
    assert request.body["text"]["format"]["type"] == "json_schema"
    assert request.body["text"]["format"]["name"] == "grok_agent_review"


def test_retries_429_then_records_tool_call(tmp_path, monkeypatch) -> None:
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "record_cursor_task",
            "arguments": json.dumps(
                {"summary": "Add a test", "files": ["tests/test_grok_agent.py"], "acceptance": ["pytest"]}
            ),
        },
    }
    transport, calls = _script(
        [
            (429, {"error": {"message": "slow down"}}, {"Retry-After": "0"}),
            (200, _completion(None, tool_calls=[tool_call])),
            (200, _completion(json.dumps(_review("revise")))),
        ]
    )
    waits: list[float] = []
    monkeypatch.setenv("GROK_AGENT_MODE", "shadow")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("GROK_AGENT_SEARCH", "false")
    agent = GrokAgent(transport=transport, sleeper=waits.append, skill_dir=tmp_path)
    result = agent.consult(prompt="Review the diff.", role="code_review", task_id="task_123")
    assert waits == [0.0]
    assert result.status == "completed"
    assert result.review["verdict"] == "revise"
    assert result.tool_records["cursor"][0]["summary"] == "Add a test"
    assert calls[2].body["input"][-1]["type"] == "function_call_output"
    assert calls[2].body["input"][-1]["call_id"] == "call_1"
    assert "search_parameters" not in calls[0].body
    assert "web_search" not in [tool["type"] for tool in calls[0].body["tools"]]
    assert "reasoning" not in calls[0].body


def test_deferred_uses_synchronous_responses(tmp_path, monkeypatch) -> None:
    transport, calls = _script([(200, _completion(json.dumps(_review())))])
    agent = _agent(
        tmp_path,
        transport,
        monkeypatch,
        GROK_AGENT_MODE="live",
        XAI_API_KEY="xai-test",
    )
    result = agent.consult_deferred(prompt="Check the cutoff.", role="reasoning")
    assert result.status == "completed"
    assert calls[0].url == "https://api.x.ai/v1/responses"
    assert "deferred" not in calls[0].body
    assert "search_parameters" not in calls[0].body


def test_stream_yields_deltas_and_off_yields_nothing(tmp_path, monkeypatch) -> None:
    assert list(iter_chat_sse_text(["data: [DONE]"])) == []
    raw = b'data: {"choices":[{"delta":{"content":"ab"}}]}\n\ndata: {"choices":[{"delta":{"content":"c"}}]}\n\ndata: [DONE]\n'
    transport, _calls = _script([(200, raw)])
    agent = _agent(tmp_path, transport, monkeypatch, GROK_AGENT_MODE="shadow", XAI_API_KEY="xai-test")
    assert "".join(agent.consult_stream(prompt="Stream the note.", role="reasoning")) == "abc"
    monkeypatch.setenv("GROK_AGENT_MODE", "off")
    quiet = GrokAgent(transport=transport, skill_dir=tmp_path)
    assert list(quiet.consult_stream(prompt="Stream the note.")) == []


def test_usage_list_price_is_grok_47_only() -> None:
    usage = {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000, "total_tokens": 2_000_000}}
    listed = usage_from_payload(usage, model="grok-4.7", host="api.x.ai")
    regional = usage_from_payload(usage, model="grok-4.7", host="us.api.x.ai")
    other = usage_from_payload(usage, model="grok-4.6", host="api.x.ai")
    assert listed["cost_usd"] == 8.0
    assert listed["cost_source"] == "list_price"
    assert regional["cost_usd"] == 8.8
    assert other["cost_usd"] is None


class _Sink:
    def __init__(self) -> None:
        self.cursor: list[object] = []
        self.github: list[object] = []

    def submit_cursor(self, task):
        self.cursor.append(task)
        return {"executed": True, "id": "cursor-1"}

    def submit_github(self, intent):
        self.github.append(intent)
        return {"executed": True, "id": "gh-1"}


def _handoff_agent(tmp_path, monkeypatch, verdict: str = "proceed"):
    transport, calls = _script([(200, _completion(json.dumps(_review(verdict))))])
    agent = _agent(tmp_path, transport, monkeypatch, GROK_AGENT_MODE="live", XAI_API_KEY="xai-test")
    return agent, calls


def test_live_proceed_calls_sink_and_filters_validation(tmp_path, monkeypatch) -> None:
    agent, calls = _handoff_agent(tmp_path, monkeypatch)
    sink = _Sink()
    envelope = run_handoff(
        prompt="Should this hypothesis stay shadow?",
        role="adversarial_review",
        openai_plan={"role": "quant_analysis", "goal": "falsify"},
        specialist_briefs=[{"name": "scanner", "note": "n=12"}],
        agent=agent,
        sink=sink,
        task_id="edge_1",
    )
    assert [stage["stage"] for stage in envelope["stages"]] == list(envelope["stage_order"])
    assert envelope["stages"][1]["planner"] == "openai"
    assert envelope["stages"][2]["status"] == "attached"
    assert sink.cursor and sink.github
    assert envelope["stages"][4]["executed"] is True
    assert envelope["stages"][5]["executed"] is True
    validation = envelope["stages"][6]
    assert validation["executed"] is False
    assert validation["accepted_commands"] == ["python -m pytest -q"]
    assert validation["rejected_commands"] == ["curl https://evil.example"]
    assert "python -m ruff check ." in validation["recommended_commands"]
    assert calls[0].body["instructions"].startswith("You are the quantitative analyst")


def test_shadow_and_reject_do_not_call_sink(tmp_path, monkeypatch) -> None:
    transport, _calls = _script([(200, _completion(json.dumps(_review("reject"))))])
    agent = _agent(tmp_path, transport, monkeypatch, GROK_AGENT_MODE="shadow", XAI_API_KEY="xai-test")
    sink = _Sink()
    envelope = run_handoff(prompt="Review the sleeve.", agent=agent, sink=sink)
    assert envelope["mode"] == "shadow"
    assert envelope["stages"][1]["planner"] == "local"
    assert sink.cursor == []
    assert sink.github == []
    assert envelope["stages"][4]["eligible"] is False
    assert envelope["stages"][6]["recommended_commands"] == []


def test_missing_key_blocks_downstream(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GROK_AGENT_MODE", "shadow")
    envelope = run_handoff(prompt="Review the sleeve.", agent=GrokAgent(skill_dir=tmp_path))
    assert envelope["stages"][3]["status"] == "error"
    assert "XAI_API_KEY" in envelope["stages"][3]["error"]
    assert envelope["stages"][4]["status"] == "blocked"


def test_off_blocks_downstream_stages(tmp_path) -> None:
    sink = _Sink()
    envelope = run_handoff(prompt="Review the sleeve.", agent=GrokAgent(skill_dir=tmp_path), sink=sink)
    assert envelope["stages"][3]["status"] == "skipped"
    assert envelope["stages"][4]["status"] == "blocked"
    assert sink.cursor == []


def test_dispatch_tool_unknown_and_missing_prompt() -> None:
    unknown = dispatch_orchestrator_tool("place_order", {"ticker": "AAA"})
    assert unknown["ok"] is False
    missing = dispatch_orchestrator_tool("consult_grok", {"role": "research"})
    assert missing["ok"] is False


def test_request_reasoning_effort_overrides_env(tmp_path, monkeypatch) -> None:
    transport, calls = _script([(200, _completion(json.dumps(_review())))])
    agent = _agent(
        tmp_path,
        transport,
        monkeypatch,
        GROK_AGENT_MODE="shadow",
        XAI_API_KEY="xai-test",
        GROK_AGENT_REASONING_EFFORT="low",
    )
    result = agent.consult(prompt="Review the gate.", reasoning_effort="xhigh")
    assert result.status == "completed"
    assert calls[0].body["reasoning"]["effort"] == "xhigh"


def test_consult_grok_tool_is_omitted_when_off() -> None:
    from core.hybrid_orchestrator import CONSULT_GROK_TOOL, attach_consult_grok_tool

    base = [{"type": "function", "function": {"name": "validate_strategy_spec"}}]
    assert attach_consult_grok_tool(base, "off") == base
    attached = attach_consult_grok_tool(base, "shadow")
    assert attached[-1] == CONSULT_GROK_TOOL
    assert attached[0] == base[0]
    text = (Path(__file__).resolve().parent.parent / "webapp" / "strategy_chat.py").read_text(encoding="utf-8")
    assert 'if name == "consult_grok"' in text
    assert 'get_grok_agent_mode() == "off"' in text
    assert "attach_consult_grok_tool" in text
    assert "Its verdict does not place an order" in text


def test_today_screen_hosts_grok_review() -> None:
    root = Path(__file__).resolve().parent.parent / "webapp" / "static"
    html = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "styles.css").read_text(encoding="utf-8")
    assert 'id="grokReviewSection"' in html
    assert 'id="scanDetailGrokBtn"' in html
    assert "body.ui-screen-research #grokReviewSection" in css


def test_capabilities_and_consult_route(monkeypatch) -> None:
    monkeypatch.setenv("GROK_AGENT_MODE", "off")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")

    def boom(request, timeout):
        raise AssertionError(request.url)

    monkeypatch.setattr("core.grok_agent.urllib_transport", boom)
    from webapp.routes.grok_agent import GrokConsultBody, grok_capabilities, grok_consult, router

    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/api/agents/grok/capabilities" in paths
    assert "/api/agents/grok" in paths
    caps = grok_capabilities()
    consult = grok_consult(GrokConsultBody(prompt="Review the gate.", role="research"))
    assert caps.ok is True
    assert caps.data["mode"] == "off"
    assert caps.data["key_configured"] is True
    assert "xai-test" not in json.dumps(caps.data)
    assert consult.ok is True
    grok = next(stage for stage in consult.data["stages"] if stage["stage"] == "grok")
    assert grok["status"] == "skipped"
