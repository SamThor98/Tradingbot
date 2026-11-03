from __future__ import annotations

from core.llm_endpoint import (
    DEFAULT_GROK_MODEL,
    XAI_DEFAULT_BASE_URL,
    resolve_llm_endpoint,
)
from engine_analysis import cache_conviction, get_cached_conviction


def test_resolve_grok_model_defaults_xai_base(monkeypatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MIROFISH_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MODEL_NAME", "grok-4.6")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    endpoint = resolve_llm_endpoint({})
    assert endpoint.provider == "xai"
    assert endpoint.base_url == XAI_DEFAULT_BASE_URL
    assert endpoint.model == "grok-4.6"
    assert endpoint.api_key == "xai-test"


def test_resolve_grok_does_not_send_openai_key_to_xai(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODEL_NAME", "grok-4")
    monkeypatch.setenv("LLM_BASE_URL", XAI_DEFAULT_BASE_URL)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("MIROFISH_API_KEY", raising=False)
    endpoint = resolve_llm_endpoint({})
    assert endpoint.provider == "none"
    assert endpoint.api_key == ""


def test_resolve_openai_default(monkeypatch) -> None:
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    endpoint = resolve_llm_endpoint({})
    assert endpoint.provider == "openai"
    assert endpoint.model == "gpt-4o-mini"
    assert endpoint.api_key == "sk-openai"


def test_xai_key_alone_selects_grok(monkeypatch) -> None:
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MIROFISH_API_KEY", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    endpoint = resolve_llm_endpoint({})
    assert endpoint.provider == "xai"
    assert endpoint.model == DEFAULT_GROK_MODEL


def test_cache_rejects_other_llm_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MIROFISH_CACHE_DIR", str(tmp_path))
    result = {
        "conviction_score": 40,
        "summary": "Neutral / Mixed",
        "agent_votes": [],
        "simulation_id": "sim_test",
        "seed_fingerprint": "abc",
        "llm_model": "grok-4-fast",
        "llm_provider": "xai",
    }
    cache_conviction("AAA", result, skill_dir=tmp_path)
    hit = get_cached_conviction("AAA", skill_dir=tmp_path, llm_model="grok-4-fast")
    assert hit is not None
    assert hit["conviction_score"] == 40
    miss = get_cached_conviction("AAA", skill_dir=tmp_path, llm_model="gpt-4o-mini")
    assert miss is None
