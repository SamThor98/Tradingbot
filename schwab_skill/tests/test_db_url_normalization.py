from __future__ import annotations

import pytest

from webapp.db import _normalize_database_url, _validate_database_url


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
