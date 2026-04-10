from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / "webapp.db"


def _normalize_database_url(url: str) -> str:
    """Render/Heroku often use postgres://; SQLAlchemy 2 + psycopg2 expect postgresql+psycopg2://."""
    u = url.strip()
    if u.startswith("sqlite"):
        return u
    if u.startswith("postgres://"):
        return "postgresql+psycopg2://" + u[len("postgres://") :]
    if u.startswith("postgresql://") and not u.split("://", 1)[0].endswith("psycopg2"):
        return "postgresql+psycopg2://" + u[len("postgresql://") :]
    return u


def _maybe_require_ssl_for_render(url: str) -> str:
    """Render Postgres often needs sslmode=require; missing it causes OperationalError (sqlalche.me/e/20/e3q8)."""
    if not url.startswith("postgresql"):
        return url
    flag = (os.getenv("DATABASE_SSLMODE") or "").strip().lower()
    if flag in ("disable", "0", "false", "off", "no"):
        return url
    if "sslmode=" in url:
        return url
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return url
    if not host.endswith(".render.com"):
        return url
    q = list(parse_qsl(parsed.query, keep_blank_values=True))
    if not any(k == "sslmode" for k, _ in q):
        q.append(("sslmode", "require"))
    new_query = urlencode(q)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


_raw_db_url = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}")
DATABASE_URL = _maybe_require_ssl_for_render(_normalize_database_url(_raw_db_url))

engine_kwargs: dict[str, object] = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update(
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        pool_pre_ping=True,
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    )

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

