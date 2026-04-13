"""CORS origin list includes Render / public URL when configured."""

from __future__ import annotations

import pytest

from webapp.cors_config import build_allowed_origins


def test_dev_defaults_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("WEB_PUBLIC_ORIGIN", raising=False)
    origins = build_allowed_origins()
    assert "http://127.0.0.1:8000" in origins
    assert "http://localhost:8000" in origins


def test_blank_web_allowed_falls_back_to_dev_plus_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty WEB_ALLOWED_ORIGINS must not collapse to a single localhost origin only."""
    monkeypatch.setenv("WEB_ALLOWED_ORIGINS", "")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://myapp.onrender.com")
    origins = build_allowed_origins()
    assert "https://myapp.onrender.com" in origins
    assert "http://localhost:8000" in origins


def test_render_and_custom_domain_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_ALLOWED_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://svc.onrender.com")
    monkeypatch.setenv("WEB_PUBLIC_ORIGIN", "https://custom.example.com/path/ignored")
    origins = build_allowed_origins()
    assert origins.count("https://app.example.com") == 1
    assert "https://svc.onrender.com" in origins
    assert "https://custom.example.com" in origins
