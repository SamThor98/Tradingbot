from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from webapp import main as webapp_main


def _request_with_host(hostname: str) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/scan",
        "headers": [],
        "scheme": "http",
        "query_string": b"",
        "server": (hostname, 8000),
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_require_trade_api_key_allows_loopback_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("RENDER", "1")
    monkeypatch.delenv("WEB_ALLOW_UNSAFE_LOCAL_WRITES", raising=False)

    out = webapp_main.require_trade_api_key(
        request=_request_with_host("127.0.0.1"),
        x_api_key=None,
        x_user=None,
    )
    assert out["actor"] == "unsafe-local-user"


def test_require_trade_api_key_rejects_remote_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("RENDER", "1")
    monkeypatch.delenv("WEB_ALLOW_UNSAFE_LOCAL_WRITES", raising=False)

    with pytest.raises(HTTPException) as exc:
        webapp_main.require_trade_api_key(
            request=_request_with_host("example.com"),
            x_api_key=None,
            x_user=None,
        )
    assert exc.value.status_code == 503
