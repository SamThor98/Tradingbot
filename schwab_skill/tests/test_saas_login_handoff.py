"""SaaS /login must not 302-drop Supabase email auth tokens."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp import main_saas


def test_login_serves_client_handoff_not_redirect() -> None:
    """A server 302 to /?section=connect drops #access_token and ?code=."""
    with TestClient(main_saas.app) as client:
        resp = client.get("/login?code=pkce-test-code", follow_redirects=False)
    assert resp.status_code == 200
    assert "text/html" in (resp.headers.get("content-type") or "")
    body = resp.text
    assert "location.replace" in body
    assert "location.hash" in body
    assert "section" in body
    assert resp.headers.get("location") is None


def test_login_handoff_is_uncached() -> None:
    with TestClient(main_saas.app) as client:
        resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 200
    assert "no-store" in (resp.headers.get("cache-control") or "").lower()
