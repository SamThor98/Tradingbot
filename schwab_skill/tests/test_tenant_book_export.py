"""SaaS Book YTD Excel export (tenant routes, no live Schwab)."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webapp import main_saas
from webapp.db import Base, get_db
from webapp.models import User
from webapp.security import get_current_user


@pytest.fixture
def cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unit_test_jwt_secret")
    monkeypatch.delenv("SAAS_BILLING_ENFORCE", raising=False)


@pytest.fixture
def test_db(cred_key: None) -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    db = session()
    db.add(User(id="user_1", email="u@example.com", auth_provider="supabase"))
    db.commit()
    db.close()
    return session


@pytest.fixture
def saas_client(test_db: sessionmaker, cred_key: None) -> TestClient:
    def override_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    def override_get_current_user():
        db = test_db()
        try:
            user = db.query(User).filter(User.id == "user_1").first()
            assert user is not None
            return user
        finally:
            db.close()

    app = main_saas.app
    app.dependency_overrides[main_saas._db] = override_db
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _auth_header() -> dict[str, str]:
    token = jwt.encode({"sub": "user_1"}, "unit_test_jwt_secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_tenant_book_export_requires_schwab_link(saas_client: TestClient) -> None:
    with patch("webapp.routes.tenant_book.user_has_account_session", return_value=False):
        resp = saas_client.get("/api/book/export?tax_year=2026", headers=_auth_header())
    assert resp.status_code == 409
    body = resp.json()
    assert body["ok"] is False
    assert "Schwab" in (body.get("error") or "")


def test_tenant_book_export_download_and_status(saas_client: TestClient, tmp_path: Path) -> None:
    raw = [
        {
            "activityId": 1,
            "tradeDate": "2026-01-10T15:30:00.000Z",
            "description": "OPEN AAA",
            "type": "TRADE",
            "netAmount": -1000.0,
            "transferItems": [
                {
                    "instrument": {"assetType": "EQUITY", "symbol": "AAA"},
                    "amount": 10,
                    "cost": -1000.0,
                    "price": 100.0,
                    "positionEffect": "OPENING",
                }
            ],
        },
        {
            "activityId": 2,
            "tradeDate": "2026-03-10T15:30:00.000Z",
            "description": "CLOSE AAA",
            "type": "TRADE",
            "netAmount": 1100.0,
            "transferItems": [
                {
                    "instrument": {"assetType": "EQUITY", "symbol": "AAA"},
                    "amount": 10,
                    "cost": 1100.0,
                    "price": 110.0,
                    "positionEffect": "CLOSING",
                }
            ],
        },
    ]
    meta = {"error": None, "count": 2, "source": "schwab"}

    @contextmanager
    def _fake_skill_dir(_db, _user_id):
        yield tmp_path

    with (
        patch("webapp.routes.tenant_book.user_has_account_session", return_value=True),
        patch("webapp.routes.tenant_book.tenant_skill_dir", _fake_skill_dir),
        patch("core.book_export.fetch_trades_for_skill", return_value=(raw, meta)),
    ):
        resp = saas_client.get("/api/book/export?tax_year=2026", headers=_auth_header())
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert bytes(resp.content).startswith(b"PK\x03\x04")

    status = saas_client.get("/api/book/export/status", headers=_auth_header())
    assert status.status_code == 200
    body = status.json()
    assert body["ok"] is True
    assert body["data"]["ok"] is True
    assert body["data"]["closed_count"] == 1
    assert body["data"].get("persist_disk") is False
