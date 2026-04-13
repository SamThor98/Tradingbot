"""Supabase JWT verification with optional legacy signing secret."""

from __future__ import annotations

import jwt
import pytest
from fastapi import HTTPException

from webapp.security import decode_supabase_jwt


def test_decode_uses_primary_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "primary_only")
    monkeypatch.delenv("SUPABASE_JWT_SECRET_LEGACY", raising=False)
    token = jwt.encode({"sub": "u1"}, "primary_only", algorithm="HS256")
    assert decode_supabase_jwt(token)["sub"] == "u1"


def test_decode_falls_back_to_legacy_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "new_secret")
    monkeypatch.setenv("SUPABASE_JWT_SECRET_LEGACY", "old_secret")
    token = jwt.encode({"sub": "u_legacy"}, "old_secret", algorithm="HS256")
    assert decode_supabase_jwt(token)["sub"] == "u_legacy"


def test_decode_primary_preferred_when_both_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same symmetric key used twice is deduped; one successful decode is enough."""
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "shared")
    monkeypatch.setenv("SUPABASE_JWT_SECRET_LEGACY", "shared")
    token = jwt.encode({"sub": "u2"}, "shared", algorithm="HS256")
    assert decode_supabase_jwt(token)["sub"] == "u2"


def test_decode_fails_when_neither_secret_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "a")
    monkeypatch.setenv("SUPABASE_JWT_SECRET_LEGACY", "b")
    token = jwt.encode({"sub": "x"}, "other", algorithm="HS256")
    with pytest.raises(HTTPException) as ei:
        decode_supabase_jwt(token)
    assert ei.value.status_code == 401
