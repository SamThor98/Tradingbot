"""Resolve OpenAI-compatible LLM routing for MiroFish, SEC summaries, and chat.

xAI Grok is OpenAI-compatible at ``https://api.x.ai/v1``. Prefer ``XAI_API_KEY``
(or ``MIROFISH_API_KEY``) when the model is Grok — never send an OpenAI key to
xAI. Process env overrides file env, matching ``config.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

XAI_DEFAULT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GROK_MODEL = "grok-4.6"


@dataclass(frozen=True)
class LlmEndpoint:
    api_key: str
    base_url: str
    model: str
    provider: str  # xai | openai | custom | none

    def public_dict(self) -> dict[str, str]:
        return {
            "llm_provider": self.provider,
            "llm_model": self.model,
            "llm_base_url": self.base_url,
        }


def _first(*vals: str) -> str:
    for val in vals:
        text = str(val or "").strip()
        if text:
            return text
    return ""


def _lookup(key: str, file_env: Mapping[str, str]) -> str:
    raw = os.environ.get(key)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    return str(file_env.get(key, "") or "").strip()


def resolve_llm_endpoint(file_env: Mapping[str, str] | None = None) -> LlmEndpoint:
    """Pick provider, model, base URL, and key from file env + process env."""
    env = file_env or {}
    model = _lookup("LLM_MODEL_NAME", env)
    base_url = _lookup("LLM_BASE_URL", env)
    xai_key = _lookup("XAI_API_KEY", env)
    miro_key = _lookup("MIROFISH_API_KEY", env)
    openai_key = _first(_lookup("OPENAI_API_KEY", env), _lookup("OPENAI_KEY", env))

    grok_model = bool(model) and model.lower().startswith("grok")
    xai_host = "x.ai" in base_url.lower()
    if not model and (xai_key or xai_host):
        model = DEFAULT_GROK_MODEL
        grok_model = True

    if grok_model or xai_host:
        if not base_url:
            base_url = XAI_DEFAULT_BASE_URL
        if not model:
            model = DEFAULT_GROK_MODEL
        api_key = _first(xai_key, miro_key)
        provider = "xai"
    else:
        if not model:
            model = DEFAULT_OPENAI_MODEL
        api_key = _first(miro_key, openai_key, xai_key)
        provider = "custom" if base_url else "openai"

    if not api_key:
        provider = "none"
    return LlmEndpoint(api_key=api_key, base_url=base_url, model=model, provider=provider)


def endpoint_from_result(result: Mapping[str, Any] | None) -> dict[str, str]:
    """Public llm_* fields from a simulation payload (never includes the key)."""
    if not isinstance(result, dict):
        return {"llm_provider": "", "llm_model": "", "llm_base_url": ""}
    return {
        "llm_provider": str(result.get("llm_provider") or ""),
        "llm_model": str(result.get("llm_model") or ""),
        "llm_base_url": str(result.get("llm_base_url") or ""),
    }
