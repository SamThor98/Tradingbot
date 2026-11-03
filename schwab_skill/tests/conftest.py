"""Shared pytest fixtures for the TradingBot suite."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Captured while this conftest is imported, which happens before pytest imports
# any test module. `webapp.main` and `webapp.main_saas` call
# `bootstrap_dotenv_into_environ()` at import time, so collecting the SaaS or
# dashboard tests copies the developer's local `.env` into `os.environ`. Process
# env wins over the `.env` file in `_env_value`, so tests that write a throwaway
# `.env` and pass `skill_dir=tmp_path` would silently read the promoted values
# instead — making them pass or fail based on collection order and on whichever
# `.env` happens to sit in the repo.
_PRISTINE_ENVIRON = dict(os.environ)


def isolate_repo_dotenv(monkeypatch: pytest.MonkeyPatch, *keys: str) -> None:
    """Drop process overrides and ignore the repo ``.env`` for these keys.

    ``_env_value`` reads ``os.environ`` first and then the skill ``.env``.
    A default-off assertion has to clear both, or a local flag leaks in.
    """
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("config._load_env", lambda skill_dir=None: {})
    from config import clear_env_cache

    clear_env_cache()


@pytest.fixture(scope="session", autouse=True)
def _pristine_process_env() -> Iterator[None]:
    """Undo import-time `.env` promotion so config getters are deterministic."""
    for key in set(os.environ) - set(_PRISTINE_ENVIRON):
        del os.environ[key]
    os.environ.update(_PRISTINE_ENVIRON)
    yield
