"""SaaS live execution opt-in and staged-order confirmation."""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from webapp import main_saas, tenant_dashboard
from webapp.db import Base
from webapp.models import PendingTrade, User, UserCredential
from webapp.security import encrypt_secret, get_current_user


@pytest.fixture
def cred_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", key)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "unit_test_jwt_secret")
    monkeypatch.delenv("SAAS_BILLING_ENFORCE", raising=False)
    monkeypatch.setenv("SCHWAB_MARKET_APP_KEY", "mk")
    monkeypatch.setenv("SCHWAB_MARKET_APP_SECRET", "ms")
    monkeypatch.setenv("SCHWAB_ACCOUNT_APP_KEY", "ak")
    monkeypatch.setenv("SCHWAB_ACCOUNT_APP_SECRET", "as")


@pytest.fixture
def test_db(cred_key: None) -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


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
            u = db.query(User).filter(User.id == "user_1").first()
            assert u is not None
            return u
        finally:
            db.close()

    app = main_saas.app
    app.dependency_overrides[main_saas._db] = override_db
    app.dependency_overrides[tenant_dashboard._db] = override_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _auth_header() -> dict[str, str]:
    token = jwt.encode({"sub": "user_1"}, "unit_test_jwt_secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _seed_user_with_schwab(db: Session) -> None:
    db.add(User(id="user_1", email="a@b.c", auth_provider="supabase", live_execution_enabled=False))
    market_json = json.dumps({"access_token": "m1", "refresh_token": "mr1"})
    account_json = json.dumps({"access_token": "a1", "refresh_token": "ar1"})
    db.add(
        UserCredential(
            user_id="user_1",
            market_token_payload_enc=encrypt_secret(market_json),
            account_token_payload_enc=encrypt_secret(account_json),
        )
    )
    db.commit()


def test_orders_execute_returns_410(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
    finally:
        db.close()

    r = saas_client.post(
        "/api/orders/execute",
        json={"ticker": "AAPL", "qty": 1, "side": "BUY", "order_type": "MARKET"},
        headers=_auth_header(),
    )
    assert r.status_code == 410


def test_approve_blocked_until_live_enabled(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        db.add(
            PendingTrade(
                id="abc12345",
                user_id="user_1",
                ticker="AAPL",
                qty=1,
                price=100.0,
                status="pending",
                signal_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    r = saas_client.post(
        "/api/trades/abc12345/approve?confirm_live=true",
        json={"typed_ticker": "AAPL"},
        headers=_auth_header(),
    )
    assert r.status_code == 403


def test_enable_live_trading_then_approve(
    saas_client: TestClient,
    test_db: sessionmaker,
) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        db.add(
            PendingTrade(
                id="abc12345",
                user_id="user_1",
                ticker="AAPL",
                qty=1,
                price=100.0,
                status="pending",
                signal_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    en = saas_client.post(
        "/api/settings/enable-live-trading",
        json={"risk_acknowledged": True, "typed_phrase": "ENABLE"},
        headers=_auth_header(),
    )
    assert en.status_code == 200
    body = en.json()
    assert body.get("ok") is True
    assert (body.get("data") or {}).get("live_execution_enabled") is True

    with patch("webapp.tenant_dashboard.place_order", return_value={"orderId": "ord_1"}):
        ap = saas_client.post(
            "/api/trades/abc12345/approve?confirm_live=true",
            json={"typed_ticker": "AAPL"},
            headers=_auth_header(),
        )
    assert ap.status_code == 200
    payload = ap.json()
    assert payload.get("ok") is True


def test_wrong_typed_ticker_rejected(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        _seed_user_with_schwab(db)
        u = db.query(User).filter(User.id == "user_1").one()
        u.live_execution_enabled = True
        db.add(
            PendingTrade(
                id="abc12345",
                user_id="user_1",
                ticker="AAPL",
                qty=1,
                price=100.0,
                status="pending",
                signal_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()

    r = saas_client.post(
        "/api/trades/abc12345/approve?confirm_live=true",
        json={"typed_ticker": "MSFT"},
        headers=_auth_header(),
    )
    assert r.status_code == 200
    assert r.json().get("ok") is False
