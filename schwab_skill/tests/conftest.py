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


@pytest.fixture(scope="session", autouse=True)
def _pristine_process_env() -> Iterator[None]:
    """Undo import-time `.env` promotion so config getters are deterministic."""
    for key in set(os.environ) - set(_PRISTINE_ENVIRON):
        del os.environ[key]
    os.environ.update(_PRISTINE_ENVIRON)
    yield
