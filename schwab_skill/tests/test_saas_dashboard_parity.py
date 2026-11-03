"""SaaS dashboard parity: Intel, SEC analyze, decision-card, ablation, config."""

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
from webapp.db import Base, get_db
from webapp.models import ScanResult, User
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
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _auth_header() -> dict[str, str]:
    token = jwt.encode({"sub": "user_1"}, "unit_test_jwt_secret", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@contextmanager
def _fake_skill_dir(_db, _user_id):  # noqa: ANN001
    yield Path(".")


def _fake_intel_payload() -> dict:
    return {
        "conviction": [{"ticker": "TEST", "composite": 1.0, "signal": "BUY"}],
        "volatility": [],
        "options_setup": [],
        "covered_calls": [],
        "summary": {"positions": 1},
        "garch_params": {},
        "data_quality": [],
        "meta": {"computed_at": "2026-08-24T00:00:00+00:00", "cache_ttl_sec": 300, "positions_total": 1},
    }


def test_saas_public_config_advertises_hosted_write_gates(saas_client: TestClient) -> None:
    body = saas_client.get("/api/public-config").json()
    assert body["ok"] is True
    assert body["data"]["plugin_mode_writes_enabled"] is False
    assert body["data"]["ablation_writes_enabled"] is False


def test_saas_config_route_exists(saas_client: TestClient) -> None:
    body = saas_client.get("/api/config").json()
    assert body["ok"] is True
    assert "trade_api_key_required" in body["data"]
    assert "allowed_origins" in body["data"]


def test_saas_decision_card_includes_brief(saas_client: TestClient, test_db: sessionmaker) -> None:
    db = test_db()
    try:
        db.add(
            ScanResult(
                user_id="user_1",
                job_id="job-1",
                ticker="AAPL",
                signal_score=70.0,
                payload_json={
                    "ticker": "AAPL",
                    "price": 100.0,
                    "composite_score": 82.5,
                    "signal_score": 70.0,
                    "rank_score_v2": 65.0,
                    "reliability_score": 71.0,
                    "advisory": {"confidence_bucket": "high", "p_up_10d": 0.61},
                    "strategy_attribution": {"top_live": "stage2_vcp"},
                },
            )
        )
        db.commit()
    finally:
        db.close()

    with (
        patch("webapp.tenant_dashboard.tenant_skill_dir", _fake_skill_dir),
        patch("webapp.tenant_dashboard.get_position_size_usd", return_value=10_000.0),
        patch(
            "sharadar_data.get_sharadar_research_snapshot",
            return_value={
                "ok": False,
                "enabled": False,
                "status": "disabled",
                "errors": ["missing_sharadar_api_key"],
                "daily": {},
                "fundamentals": {},
                "holders": [],
                "insiders": [],
                "actions": [],
                "events": [],
                "pit_fields": {},
            },
        ),
    ):
        resp = saas_client.get("/api/decision-card/AAPL", headers=_auth_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["ticker"] == "AAPL"
    assert "brief" in data
    assert data["confidence"]["rank_score_v2"] == 65.0
    assert data["size"]["qty"] == 100


def test_saas_position_intel_shape_and_cache(saas_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from webapp.routes import tenant_position_intel as tpi

    calls = {"n": 0}

    def _fake_compute(_skill_dir, auth=None):  # noqa: ANN001
        calls["n"] += 1
        return _fake_intel_payload()

    monkeypatch.setattr(tpi, "compute_position_intel_payload", _fake_compute)
    monkeypatch.setattr(tpi, "user_has_account_session", lambda _db, _uid: True)
    tpi._cache.clear()

    class _Auth:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    with (
        patch("webapp.routes.tenant_position_intel.tenant_skill_dir", _fake_skill_dir),
        patch("schwab_auth.DualSchwabAuth", return_value=_Auth()),
    ):
        headers = _auth_header()
        first = saas_client.get("/api/position-intel", headers=headers).json()
        assert first["ok"] is True
        for key in ("conviction", "volatility", "options_setup", "covered_calls", "summary", "data_quality", "meta"):
            assert key in first["data"]
        assert first["data"]["meta"]["cache_hit"] is False
        assert calls["n"] == 1

        second = saas_client.get("/api/position-intel", headers=headers).json()
        assert second["data"]["meta"]["cache_hit"] is True
        assert calls["n"] == 1


def test_saas_position_intel_lookup_validates_ticker(saas_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "webapp.routes.tenant_position_intel.user_can_materialize_for_scan",
        lambda _db, _uid: (True, ""),
    )
    bad = saas_client.get(
        "/api/position-intel/lookup",
        params={"ticker": "!!!"},
        headers=_auth_header(),
    ).json()
    assert bad["ok"] is False
    assert "invalid ticker" in (bad["error"] or "").lower()


def test_saas_sec_analyze_uses_shared_normalizer(saas_client: TestClient) -> None:
    fake_out = {
        "ok": True,
        "ticker": "MSFT",
        "form": "10-K",
        "verdict": "neutral",
        "confidence": 40,
        "why": ["Limited incremental risk language."],
        "limits": [],
        "evidence": [],
        "from_cache": True,
        "source": "edgar",
    }
    with (
        patch("webapp.tenant_dashboard.tenant_skill_dir", _fake_skill_dir),
        patch("webapp.tenant_dashboard.analyze_latest_filing_for_ticker", return_value=fake_out),
    ):
        resp = saas_client.get("/api/sec/analyze/MSFT", headers=_auth_header())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["ticker"] == "MSFT"
    assert body["data"]["summary_headline"]
    assert body["data"]["analysis_mode"] == "full_text"


def test_saas_ablation_status_is_hosted_idle(saas_client: TestClient) -> None:
    body = saas_client.get("/api/ablation/status", headers=_auth_header()).json()
    assert body["ok"] is True
    assert body["data"]["hosted_unavailable"] is True
    assert body["data"]["running"] is False


def test_saas_ablation_run_rejected(saas_client: TestClient) -> None:
    body = saas_client.post("/api/ablation/run", headers=_auth_header(), json={}).json()
    assert body["ok"] is False
    assert "local operator" in (body["error"] or "").lower()
