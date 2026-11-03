"""Local /api/decision-card lookup: in-memory job, last_scan, ScanResult."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webapp.db import Base
from webapp.models import AppState, ScanResult, User


@pytest.fixture(autouse=True)
def _disable_sharadar_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sharadar_data.get_sharadar_research_snapshot",
        lambda *args, **kwargs: {
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
    )


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
    original_job = dict(webapp_main._scan_job)
    webapp_main._scan_job.update(
        {
            "job_id": None,
            "status": "idle",
            "started_at": None,
            "finished_at": None,
            "signals_found": None,
            "diagnostics": None,
            "diagnostics_summary": None,
            "strategy_summary": None,
            "signals": [],
            "shortlist_signals": [],
            "error": None,
        }
    )
    with TestClient(app) as c:
        yield c
    webapp_main._scan_job.clear()
    webapp_main._scan_job.update(original_job)
    app.dependency_overrides.clear()


def test_decision_card_uses_last_scan_when_job_idle(
    client: TestClient, test_db: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("execution.get_position_size_usd", lambda **_kwargs: 10_000.0)
    monkeypatch.setattr("webapp.main.get_position_size_usd", lambda **_kwargs: 10_000.0)
    db = test_db()
    try:
        db.add(
            AppState(
                user_id="local",
                key="last_scan",
                value_json={
                    "at": "2026-09-16T12:00:00+00:00",
                    "signals_found": 1,
                    "signals": [
                        {
                            "ticker": "AAPL",
                            "price": 100.0,
                            "composite_score": 82.5,
                            "advisory": {"confidence_bucket": "high", "p_up_10d": 0.61},
                            "strategy_attribution": {"top_live": "stage2_vcp"},
                            "sec_risk_tag": "low",
                            "volume_ratio": 1.4,
                        }
                    ],
                    "shortlist_signals": [],
                },
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/decision-card/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    brief = body["data"]["brief"]
    assert "AAPL remains a breakout candidate" in brief["setup_summary"]
    assert brief["key_risks"]
    assert brief["catalyst_notes"]
    assert brief["forensic_flags"]
    assert brief["sec_notes"]
    assert brief["entry_stop_ideas"]
    assert "10 trading days" in brief["expected_move_window"]


def test_decision_card_uses_shortlist_and_scan_result(
    client: TestClient, test_db: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("execution.get_position_size_usd", lambda **_kwargs: 10_000.0)
    monkeypatch.setattr("webapp.main.get_position_size_usd", lambda **_kwargs: 10_000.0)
    from webapp import main as webapp_main

    webapp_main._scan_job["status"] = "completed"
    webapp_main._scan_job["shortlist_signals"] = [
        {
            "ticker": "MSFT",
            "price": 250.0,
            "composite_score": 55.0,
            "_filter_status": "filtered_quality_gates",
            "_filter_reasons": ["low_signal_score"],
            "advisory": {"confidence_bucket": "low"},
            "strategy_attribution": {"top_live": "trend_breakout"},
        }
    ]
    db = test_db()
    try:
        db.add(
            ScanResult(
                user_id="local",
                job_id="job-1",
                ticker="NVDA",
                signal_score=70.0,
                payload_json={
                    "ticker": "NVDA",
                    "price": 120.0,
                    "composite_score": 70.0,
                    "advisory": {"confidence_bucket": "medium"},
                    "strategy_attribution": {"top_live": "trend_breakout"},
                },
            )
        )
        db.commit()
    finally:
        db.close()

    shortlist = client.get("/api/decision-card/MSFT").json()
    assert shortlist["ok"] is True
    assert any("quality gates" in x.lower() or "filter" in x.lower() for x in shortlist["data"]["brief"]["key_risks"])

    persisted = client.get("/api/decision-card/NVDA").json()
    assert persisted["ok"] is True
    assert persisted["data"]["ticker"] == "NVDA"
    assert persisted["data"]["brief"]["catalyst_notes"]
