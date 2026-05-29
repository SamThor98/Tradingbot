from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from schwab_auth import write_encrypted_token_file
from webapp import tenant_runtime
from webapp.db import Base
from webapp.models import UserCredential
from webapp.security import encrypt_secret


@pytest.fixture
def cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())


@pytest.fixture
def schwab_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHWAB_MARKET_APP_SECRET", "msecret")
    monkeypatch.setenv("SCHWAB_ACCOUNT_APP_SECRET", "asecret")


def test_token_refresh_persists_despite_poisoned_caller_session(
    tmp_path: Path,
    cred_key: None,
    schwab_secrets: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Isolated in-memory DB that persist_tenant_tokens_back will use via SessionLocal.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(tenant_runtime, "SessionLocal", TestSession)

    # Seed stored (stale) tokens.
    seed = TestSession()
    seed.add(
        UserCredential(
            user_id="u1",
            account_token_payload_enc=encrypt_secret(json.dumps({"access_token": "a1", "refresh_token": "ar1"})),
            market_token_payload_enc=encrypt_secret(json.dumps({"access_token": "m1", "refresh_token": "mr1"})),
        )
    )
    seed.commit()
    seed.close()

    # Fresh (rotated) tokens written to the ephemeral skill dir by the session layer.
    skill_dir = tmp_path / "tenant"
    skill_dir.mkdir()
    write_encrypted_token_file(
        skill_dir / "tokens_account.enc",
        {"access_token": "a2", "refresh_token": "ar2", "token_type": "Bearer"},
        "asecret",
    )
    write_encrypted_token_file(
        skill_dir / "tokens_market.enc",
        {"access_token": "m2", "refresh_token": "mr2"},
        "msecret",
    )

    # Caller's session is poisoned: any query raises (simulates a prior failed flush).
    poisoned = MagicMock()
    poisoned.query.side_effect = RuntimeError("transaction has been rolled back")

    tenant_runtime.persist_tenant_tokens_back(poisoned, "u1", skill_dir)

    # The refreshed tokens must have been written through the isolated session.
    check = TestSession()
    row = check.query(UserCredential).filter(UserCredential.user_id == "u1").first()
    assert row is not None
    assert tenant_runtime._account_token_dict(row)["access_token"] == "a2"
    assert tenant_runtime._market_token_dict(row)["access_token"] == "m2"
    check.close()
