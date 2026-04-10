"""
One-time empty-Postgres (or any DB) schema setup: SQLAlchemy create_all + alembic stamp.

Run from schwab_skill/ with DATABASE_URL set:

    python scripts/saas_bootstrap.py

Then use normal `alembic upgrade head` for future revisions (or SAAS_RUN_ALEMBIC on API).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic.config import Config  # noqa: E402

import webapp.models  # noqa: E402, F401
from alembic import command  # noqa: E402
from webapp.db import Base, engine  # noqa: E402


def main() -> None:
    Base.metadata.create_all(bind=engine)
    ini = ROOT / "alembic.ini"
    if ini.is_file():
        command.stamp(Config(str(ini)), "saas003")
    print("Bootstrap complete (schema + stamp saas003).")


if __name__ == "__main__":
    main()
