from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webapp.db import Base
from webapp.models import User


@pytest.fixture
def test_db(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WEB_API_KEY", "test-key-123")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)

    db = session()
    db.add(User(id="local", email=None, auth_provider="local_dashboard"))
    db.commit()
    db.close()
    return session


@pytest.fixture
def client(test_db: sessionmaker) -> TestClient:
    from webapp import main as webapp_main

    def override_db():
        db = test_db()
        try:
            yield db
        finally:
            db.close()

    app = webapp_main.app
    app.dependency_overrides[webapp_main.get_db] = override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": "test-key-123"}


def test_decision_dashboard_ready(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from webapp import main as webapp_main

    monkeypatch.setattr(
        webapp_main,
        "_latest_validation_status",
        lambda: {"passed": True, "run_status": "completed"},
    )
    monkeypatch.setattr(
        webapp_main,
        "_latest_slo_gate_status",
        lambda: {"passed": True, "failures": [], "checked_at": "2026-04-30T00:00:00Z"},
    )
    monkeypatch.setattr(
        webapp_main,
        "_latest_registry_decision",
        lambda: {
            "recorded_at": "2026-04-30T00:00:00Z",
            "event_type": "strategy_promotion_decision",
            "target": "strategy_champion_params",
            "decision": "promote",
            "rationale": ["gate_passed"],
        },
    )
    monkeypatch.setattr(
        webapp_main,
        "_load_state",
        lambda db, key, default: {
            "at": "2026-04-30T00:00:00Z",
            "signals_found": 5,
            "diagnostics_summary": {
                "data_quality": "ok",
                "scan_blocked": False,
                "top_blockers": [],
            },
            "strategy_summary": {
                "dominant_live_strategy": "breakout",
                "dominant_count": 3,
            },
        },
    )

    resp = client.get("/api/decision-dashboard", headers=_auth_headers())
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["reliability"]["state"] == "healthy"
    assert data["promotion_readiness"]["release_gate_ready"] is True
    assert data["strategy_quality"]["dominant_strategy"] == "breakout"


def test_decision_dashboard_blocked_when_gates_fail(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from webapp import main as webapp_main

    monkeypatch.setattr(
        webapp_main,
        "_latest_validation_status",
        lambda: {"passed": False, "run_status": "completed"},
    )
    monkeypatch.setattr(
        webapp_main,
        "_latest_slo_gate_status",
        lambda: {"passed": False, "failures": ["api_5xx_rate"]},
    )
    monkeypatch.setattr(webapp_main, "_latest_registry_decision", lambda: None)
    monkeypatch.setattr(webapp_main, "_load_state", lambda db, key, default: default)

    resp = client.get("/api/decision-dashboard", headers=_auth_headers())
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["reliability"]["state"] == "at_risk"
    assert data["promotion_readiness"]["release_gate_ready"] is False
    assert data["reliability"]["slo_failures"] == ["api_5xx_rate"]
