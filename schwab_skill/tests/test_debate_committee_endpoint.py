"""Committee LLM routing stays off the global xAI resolver when an OpenAI key exists."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import core.debate_committee as committee
from config import clear_env_cache, get_debate_committee_timeout_seconds
from core.debate_committee import (
    _default_complete,
    maybe_attach_committee,
    resolve_committee_route,
)

_LLM_KEYS = (
    "DEBATE_COMMITTEE_API_KEY",
    "DEBATE_COMMITTEE_MODEL",
    "DEBATE_COMMITTEE_BASE_URL",
    "DEBATE_COMMITTEE_TIMEOUT_SECONDS",
    "MIROFISH_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_KEY",
    "XAI_API_KEY",
    "LLM_MODEL_NAME",
    "LLM_BASE_URL",
)

_OPENAI_URL = "https://api.openai.com/v1"


class APITimeoutError(Exception):
    """Name matches openai.APITimeoutError. The test does not import the SDK."""


class ReadTimeout(Exception):
    """Name matches httpx.ReadTimeout and httpx2.ReadTimeout."""


def _scrub(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _LLM_KEYS:
        monkeypatch.delenv(key, raising=False)
    committee._FALLBACK_WARNED = False
    clear_env_cache()


def _patch_openai(monkeypatch: pytest.MonkeyPatch, *, content: str = "{}", exc: BaseException | None = None):
    constructed: list[dict] = []
    calls: list[dict] = []

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if exc is not None:
                raise exc
            message = SimpleNamespace(content=content)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class _Client:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr("openai.OpenAI", _Client)
    return constructed, calls


def _enable_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("config.get_debate_committee_mode", lambda skill_dir=None: "shadow")
    monkeypatch.setattr("config.get_debate_committee_max_per_scan", lambda skill_dir=None: 3)
    monkeypatch.setattr("config.get_tab_agent_debate_enabled", lambda skill_dir=None: False)


def _graded(ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "price": 100.0,
        "sma_50": 90.0,
        "stop_pct": 0.08,
        "signal_score": 70.0,
        "debate_grade": {
            "qual_raw": 80.0,
            "qual_abstain": False,
            "qual_trend": 80.0,
            "qual_setup": 70.0,
            "qual_asymmetry": 60.0,
            "qual_crowding": 50.0,
            "qual_business": None,
            "seats": {},
        },
    }


def test_key_model_and_url_resolution_order(tmp_path, monkeypatch) -> None:
    _scrub(monkeypatch)
    monkeypatch.setenv("DEBATE_COMMITTEE_API_KEY", "sk-committee")
    monkeypatch.setenv("MIROFISH_API_KEY", "sk-miro")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("LLM_MODEL_NAME", "grok-4.6")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.x.ai/v1")
    route = resolve_committee_route(tmp_path)
    assert route.fallback is False
    assert route.api_key == "sk-committee"
    assert route.model == "gpt-4o-mini"
    assert route.base_url == _OPENAI_URL
    assert route.timeout_seconds == 30.0

    monkeypatch.delenv("DEBATE_COMMITTEE_API_KEY")
    route = resolve_committee_route(tmp_path)
    assert route.api_key == "sk-miro"
    assert route.model == "gpt-4o-mini"
    assert route.base_url == _OPENAI_URL
    assert route.fallback is False

    monkeypatch.delenv("MIROFISH_API_KEY")
    route = resolve_committee_route(tmp_path)
    assert route.api_key == "sk-openai"
    assert route.fallback is False
    assert route.model == "gpt-4o-mini"
    assert route.base_url == _OPENAI_URL


def test_explicit_committee_model_url_and_timeout(tmp_path, monkeypatch) -> None:
    _scrub(monkeypatch)
    monkeypatch.setenv("MIROFISH_API_KEY", "sk-miro")
    monkeypatch.setenv("DEBATE_COMMITTEE_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("DEBATE_COMMITTEE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEBATE_COMMITTEE_TIMEOUT_SECONDS", "22")
    route = resolve_committee_route(tmp_path)
    assert route.api_key == "sk-miro"
    assert route.model == "gpt-4.1-mini"
    assert route.base_url == "https://example.invalid/v1"
    assert route.timeout_seconds == 22.0
    assert route.fallback is False


def test_timeout_seconds_clamp_and_invalid(tmp_path, monkeypatch) -> None:
    _scrub(monkeypatch)
    monkeypatch.setenv("DEBATE_COMMITTEE_TIMEOUT_SECONDS", "0")
    assert get_debate_committee_timeout_seconds(tmp_path) == 1.0
    monkeypatch.setenv("DEBATE_COMMITTEE_TIMEOUT_SECONDS", "999")
    assert get_debate_committee_timeout_seconds(tmp_path) == 120.0
    monkeypatch.setenv("DEBATE_COMMITTEE_TIMEOUT_SECONDS", "nope")
    assert get_debate_committee_timeout_seconds(tmp_path) == 30.0


def test_dotenv_mirofish_key_ignores_global_grok(tmp_path, monkeypatch) -> None:
    _scrub(monkeypatch)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "MIROFISH_API_KEY=sk-from-file",
                "XAI_API_KEY=xai-file",
                "LLM_MODEL_NAME=grok-4.6",
                "LLM_BASE_URL=https://api.x.ai/v1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    clear_env_cache()
    route = resolve_committee_route(tmp_path)
    assert route.api_key == "sk-from-file"
    assert route.model == "gpt-4o-mini"
    assert route.base_url == _OPENAI_URL
    assert route.timeout_seconds == 30.0
    assert route.fallback is False

    monkeypatch.setenv("DEBATE_COMMITTEE_API_KEY", "sk-process")
    route = resolve_committee_route(tmp_path)
    assert route.api_key == "sk-process"
    assert route.fallback is False


def test_xai_only_falls_back_to_global_resolver(tmp_path, monkeypatch, caplog) -> None:
    _scrub(monkeypatch)
    monkeypatch.setenv("XAI_API_KEY", "xai-only")
    with caplog.at_level(logging.WARNING, logger="core.debate_committee"):
        route = resolve_committee_route(tmp_path)
    assert route.fallback is True
    assert route.api_key == "xai-only"
    assert route.model == "grok-4.6"
    assert route.base_url == "https://api.x.ai/v1"
    assert route.timeout_seconds == 30.0
    assert "falling back to resolve_llm_endpoint" in caplog.text
    assert "provider=xai" in caplog.text
    assert "xai-only" not in caplog.text


def test_no_key_fallback_stays_empty(tmp_path, monkeypatch) -> None:
    _scrub(monkeypatch)
    route = resolve_committee_route(tmp_path)
    assert route.api_key == ""
    assert route.fallback is True
    assert route.model == "gpt-4o-mini"
    assert route.base_url == ""


def test_client_receives_committee_timeout_model_and_key(tmp_path, monkeypatch) -> None:
    _scrub(monkeypatch)
    monkeypatch.setenv("DEBATE_COMMITTEE_API_KEY", "sk-committee")
    monkeypatch.setenv("MIROFISH_API_KEY", "sk-miro")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("LLM_MODEL_NAME", "grok-4.6")
    monkeypatch.setenv("DEBATE_COMMITTEE_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("DEBATE_COMMITTEE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEBATE_COMMITTEE_TIMEOUT_SECONDS", "45")
    constructed, calls = _patch_openai(monkeypatch, content='{"ok":true}')
    text = _default_complete("score this", tmp_path)
    assert text == '{"ok":true}'
    assert constructed[0]["api_key"] == "sk-committee"
    assert constructed[0]["base_url"] == "https://example.invalid/v1"
    assert constructed[0]["timeout"] == 45.0
    assert constructed[0]["max_retries"] == 0
    assert calls[0]["model"] == "gpt-4.1-mini"
    assert calls[0]["max_tokens"] == 600


def test_default_client_uses_openai_mini_and_thirty_seconds(tmp_path, monkeypatch) -> None:
    _scrub(monkeypatch)
    monkeypatch.setenv("MIROFISH_API_KEY", "sk-miro")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("LLM_MODEL_NAME", "grok-4.6")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.x.ai/v1")
    constructed, calls = _patch_openai(monkeypatch, content="ok")
    assert _default_complete("score this", tmp_path) == "ok"
    assert constructed[0]["api_key"] == "sk-miro"
    assert constructed[0]["base_url"] == _OPENAI_URL
    assert constructed[0]["timeout"] == 30.0
    assert constructed[0]["max_retries"] == 0
    assert calls[0]["model"] == "gpt-4o-mini"


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: APITimeoutError("timed out"),
        lambda: ReadTimeout("timed out"),
    ],
)
def test_timeout_is_not_labeled_parse_failed(tmp_path, monkeypatch, caplog, exc_factory) -> None:
    _scrub(monkeypatch)
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("MIROFISH_API_KEY", "sk-miro")
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("LLM_MODEL_NAME", "grok-4.6")
    constructed, calls = _patch_openai(monkeypatch, exc=exc_factory())
    diagnostics: dict = {}
    with caplog.at_level(logging.WARNING, logger="core.debate_committee"):
        maybe_attach_committee(
            _graded(),
            diagnostics=diagnostics,
            skill_dir=tmp_path,
            force=True,
        )
    assert constructed[0]["timeout"] == 30.0
    assert constructed[0]["base_url"] == _OPENAI_URL
    assert constructed[0]["api_key"] == "sk-miro"
    assert calls[0]["model"] == "gpt-4o-mini"
    assert diagnostics.get("debate_committee_timeout") == 1
    assert diagnostics.get("debate_committee_parse_failed", 0) == 0
    assert diagnostics.get("debate_committee_api_error", 0) == 0
    assert diagnostics.get("debate_committee_ran", 0) == 0
    assert "debate_committee_timeout" in caplog.text
    assert "model=gpt-4o-mini" in caplog.text
    assert "ticker=AAPL" in caplog.text
    assert "elapsed_seconds=" in caplog.text
    assert "sk-miro" not in caplog.text
    assert "xai-test" not in caplog.text


def test_api_error_is_not_labeled_parse_failed(tmp_path, monkeypatch, caplog) -> None:
    _scrub(monkeypatch)
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    _patch_openai(monkeypatch, exc=RuntimeError("upstream blew up"))
    diagnostics: dict = {}
    with caplog.at_level(logging.WARNING, logger="core.debate_committee"):
        maybe_attach_committee(
            _graded("MSFT"),
            diagnostics=diagnostics,
            skill_dir=tmp_path,
            force=True,
        )
    assert diagnostics.get("debate_committee_api_error") == 1
    assert diagnostics.get("debate_committee_parse_failed", 0) == 0
    assert diagnostics.get("debate_committee_timeout", 0) == 0
    assert "debate_committee_api_error" in caplog.text
    assert "model=gpt-4o-mini" in caplog.text
    assert "ticker=MSFT" in caplog.text
    assert "elapsed_seconds=" in caplog.text
    assert "upstream blew up" not in caplog.text
    assert "sk-openai" not in caplog.text


def test_installed_sdk_timeouts_match_by_class_name() -> None:
    from core.debate_committee import _is_timeout_error

    matched = 0
    for module_name in ("httpx", "httpx2"):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        assert _is_timeout_error(module.ReadTimeout("timed out"))
        matched += 1
        try:
            from openai import APITimeoutError as SdkTimeout

            request = module.Request("POST", "https://api.openai.com/v1/chat/completions")
            assert _is_timeout_error(SdkTimeout(request))
            matched += 1
        except Exception:
            continue
    assert matched >= 1
    assert _is_timeout_error(RuntimeError("upstream")) is False


def test_bad_json_stays_parse_failed(tmp_path, monkeypatch, caplog) -> None:
    _scrub(monkeypatch)
    _enable_shadow(monkeypatch)
    monkeypatch.setenv("MIROFISH_API_KEY", "sk-miro")
    _patch_openai(monkeypatch, content="not json at all")
    diagnostics: dict = {}
    with caplog.at_level(logging.WARNING, logger="core.debate_committee"):
        maybe_attach_committee(
            _graded(),
            diagnostics=diagnostics,
            skill_dir=tmp_path,
            force=True,
        )
    assert diagnostics.get("debate_committee_parse_failed") == 1
    assert diagnostics.get("debate_committee_timeout", 0) == 0
    assert diagnostics.get("debate_committee_api_error", 0) == 0
    assert "debate_committee_timeout" not in caplog.text
    assert "debate_committee_api_error" not in caplog.text
