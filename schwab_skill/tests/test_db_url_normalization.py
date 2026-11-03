from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text

from webapp.db import (
    _normalize_database_url,
    _strip_invalid_host_brackets,
    _validate_database_url,
    configure_sqlite_connection,
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


def test_file_sqlite_uses_wal_and_busy_timeout(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'desk.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_conn, _record) -> None:
        configure_sqlite_connection(dbapi_conn)

    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert str(mode).lower() == "wal"
    assert int(timeout) == 5000


def test_memory_sqlite_skips_wal() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _configure(dbapi_conn, _record) -> None:
        configure_sqlite_connection(dbapi_conn)

    with engine.connect() as conn:
        mode = str(conn.execute(text("PRAGMA journal_mode")).scalar()).lower()
    assert mode != "wal"
