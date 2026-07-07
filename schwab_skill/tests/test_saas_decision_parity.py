"""SaaS parity tests for decision dashboard and shadow scoreboard routes."""

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

from webapp import main_saas, tenant_dashboard
from webapp.db import Base
from webapp.models import AppState, User
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
    app.dependency_overrides[tenant_dashboard._db] = override_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _auth_header() -> dict[str, str]:
    token = jwt.encode({"sub": "user_1"}, "unit_test_jwt_secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_saas_decision_dashboard_returns_snapshot(saas_client: TestClient, test_db: sessionmaker) -> None:
    resp = saas_client.get("/api/decision-dashboard", headers=_auth_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert "reliability" in data
    assert "strategy_quality" in data
    assert "signal_edge" in data
    assert "scan_preflight" in data
    assert "promotion_readiness" in data


def test_saas_decision_dashboard_uses_tenant_last_scan(
    saas_client: TestClient, test_db: sessionmaker
) -> None:
    db = test_db()
    try:
        db.add(
            AppState(
                user_id="user_1",
                key="last_scan",
                value_json='{"at":"2026-07-05T12:00:00+00:00","signals_found":2,"diagnostics_summary":{"data_quality":"ok","scan_blocked":false},"strategy_summary":{"dominant_live_strategy":"stage2_vcp","dominant_count":2},"diagnostics":{"signal_edge_shadow_mode":"shadow"}}',
            )
        )
        db.commit()
    finally:
        db.close()

    resp = saas_client.get("/api/decision-dashboard", headers=_auth_header())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["strategy_quality"]["last_scan_at"] == "2026-07-05T12:00:00+00:00"
    assert data["strategy_quality"]["signals_found"] == 2
    assert data["strategy_quality"]["dominant_strategy"] == "stage2_vcp"


def test_saas_shadow_scoreboard_returns_payload(saas_client: TestClient) -> None:
    @contextmanager
    def _fake_skill_dir(db, user_id):  # noqa: ANN001
        yield Path(".")

    fake_payload: dict[str, object] = {"plugins": [], "scan_at": None, "modes": {}}
    with (
        patch("webapp.tenant_dashboard.tenant_skill_dir", _fake_skill_dir),
        patch(
            "webapp.tenant_dashboard.build_shadow_scoreboard_payload",
            return_value=fake_payload,
        ),
    ):
        resp = saas_client.get("/api/cockpit/shadow-scoreboard", headers=_auth_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"] == fake_payload
