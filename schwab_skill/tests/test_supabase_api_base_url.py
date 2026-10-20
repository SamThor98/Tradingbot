"""Resolve Supabase API origin from SUPABASE_URL or Supabase DATABASE_URL."""

from __future__ import annotations

import pytest

from webapp.security import supabase_api_base_url


def test_explicit_supabase_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://xyzproject.supabase.co/")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:pw@db.otherref.supabase.co:5432/postgres",
    )
    assert supabase_api_base_url() == "https://xyzproject.supabase.co"


def test_infer_from_db_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres:pw@db.abcdefghijklmnopqrs.supabase.co:5432/postgres",
    )
    assert supabase_api_base_url() == "https://abcdefghijklmnopqrs.supabase.co"


def test_infer_from_pooler_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://postgres.abcdefghijklmnopqrs:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
    )
    assert supabase_api_base_url() == "https://abcdefghijklmnopqrs.supabase.co"


def test_no_infer_for_non_supabase_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/app")
    assert supabase_api_base_url() is None
