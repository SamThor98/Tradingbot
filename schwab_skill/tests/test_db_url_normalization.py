from __future__ import annotations

import pytest

from webapp.db import (
    _maybe_force_ipv4_for_supabase,
    _normalize_database_url,
    _strip_invalid_host_brackets,
    _validate_database_url,
)


def test_normalize_postgres_scheme_variants() -> None:
    assert _normalize_database_url("postgres://u:p@db.example.com:5432/app") == (
        "postgresql+psycopg2://u:p@db.example.com:5432/app"
    )
    assert _normalize_database_url("postgresql://u:p@db.example.com:5432/app") == (
        "postgresql+psycopg2://u:p@db.example.com:5432/app"
    )


def test_normalize_https_url_with_db_credentials() -> None:
    normalized = _normalize_database_url("https://u:p@dpg-12345.render.com:5432/appdb")
    assert normalized == "postgresql+psycopg2://u:p@dpg-12345.render.com:5432/appdb"


def test_validate_rejects_plain_https_url() -> None:
    with pytest.raises(ValueError, match="Invalid DATABASE_URL"):
        _validate_database_url("https://tradingbot-api.onrender.com")


def test_validate_accepts_normalized_https_db_dsn() -> None:
    normalized = _normalize_database_url("https://u:p@dpg-12345.render.com:5432/appdb")
    assert _validate_database_url(normalized) == normalized


def test_strip_invalid_bracketed_hostname_in_postgres_url() -> None:
    raw = "postgresql://postgres:pw@[db.blfzgeamkovnwlxqbruo.supabase.co]:5432/postgres"
    assert _strip_invalid_host_brackets(raw) == (
        "postgresql://postgres:pw@db.blfzgeamkovnwlxqbruo.supabase.co:5432/postgres"
    )


def test_normalize_bracketed_hostname_then_apply_driver() -> None:
    raw = "postgresql://postgres:pw@[db.blfzgeamkovnwlxqbruo.supabase.co]:5432/postgres"
    assert _normalize_database_url(raw) == (
        "postgresql+psycopg2://postgres:pw@db.blfzgeamkovnwlxqbruo.supabase.co:5432/postgres"
    )


def test_strip_invalid_bracketed_hostname_without_userinfo() -> None:
    raw = "postgresql://[db.blfzgeamkovnwlxqbruo.supabase.co]:5432/postgres"
    assert _strip_invalid_host_brackets(raw) == (
        "postgresql://db.blfzgeamkovnwlxqbruo.supabase.co:5432/postgres"
    )


def test_validate_accepts_sanitized_bracketed_hostname() -> None:
    raw = "postgresql+psycopg2://postgres:pw@[db.blfzgeamkovnwlxqbruo.supabase.co]:5432/postgres"
    assert _validate_database_url(raw) == (
        "postgresql+psycopg2://postgres:pw@db.blfzgeamkovnwlxqbruo.supabase.co:5432/postgres"
    )


def test_validate_accepts_bracketed_hostname_without_userinfo() -> None:
    raw = "postgresql://[db.blfzgeamkovnwlxqbruo.supabase.co]:5432/postgres"
    assert _validate_database_url(raw) == "postgresql://db.blfzgeamkovnwlxqbruo.supabase.co:5432/postgres"


def test_force_ipv4_for_supabase_adds_hostaddr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = "postgresql+psycopg2://postgres:pw@db.blfzgeamkovnwlxqbruo.supabase.co:5432/postgres"

    def _fake_getaddrinfo(*_args, **_kwargs):
        return [
            (2, 1, 6, "", ("1.2.3.4", 5432)),
            (2, 1, 6, "", ("1.2.3.5", 5432)),
        ]

    monkeypatch.setattr("webapp.db.socket.getaddrinfo", _fake_getaddrinfo)
    out = _maybe_force_ipv4_for_supabase(dsn)
    assert out == f"{dsn}?hostaddr=1.2.3.4"


def test_force_ipv4_for_supabase_preserves_existing_hostaddr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dsn = (
        "postgresql+psycopg2://postgres:pw@db.blfzgeamkovnwlxqbruo.supabase.co:5432/postgres"
        "?sslmode=require&hostaddr=1.2.3.4"
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError("getaddrinfo should not be called when hostaddr exists")

    monkeypatch.setattr("webapp.db.socket.getaddrinfo", _boom)
    assert _maybe_force_ipv4_for_supabase(dsn) == dsn


def test_force_ipv4_for_supabase_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    dsn = "postgresql+psycopg2://postgres:pw@db.blfzgeamkovnwlxqbruo.supabase.co:5432/postgres"
    monkeypatch.setenv("DATABASE_FORCE_IPV4", "false")

    def _boom(*_args, **_kwargs):
        raise AssertionError("getaddrinfo should not be called when disabled")

    monkeypatch.setattr("webapp.db.socket.getaddrinfo", _boom)
    assert _maybe_force_ipv4_for_supabase(dsn) == dsn
