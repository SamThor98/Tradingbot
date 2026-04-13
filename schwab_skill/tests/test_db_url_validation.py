"""Guards for DATABASE_URL misconfiguration."""

import importlib
import sys


def test_database_url_https_raises_clear_error(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "https://abc.supabase.co",
    )
    # webapp.db is imported by many tests; reload after env change.
    sys.modules.pop("webapp.db", None)
    try:
        import webapp.db  # noqa: F401
    except ValueError as e:
        assert "postgresql" in str(e).lower()
        assert "supabase" in str(e).lower()
    else:
        raise AssertionError("expected ValueError")


def test_database_url_postgresql_imports(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pass@localhost:5432/db",
    )
    sys.modules.pop("webapp.db", None)
    importlib.import_module("webapp.db")
