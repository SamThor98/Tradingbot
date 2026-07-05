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
    from webapp import decision_dashboard_service as dds
    from webapp import main as webapp_main

    monkeypatch.setattr(
        dds,
        "latest_validation_status",
        lambda skill_dir: {"passed": True, "run_status": "completed"},
    )
    monkeypatch.setattr(
        dds,
        "latest_slo_gate_status",
        lambda skill_dir: {"passed": True, "failures": [], "checked_at": "2026-04-30T00:00:00Z"},
    )
    monkeypatch.setattr(
        dds,
        "latest_registry_decision",
        lambda skill_dir: {
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
    assert "signal_edge" in data
    assert data["signal_edge"]["state"] in {
        "shadow_only",
        "fix_entry_first",
        "rank_filter_candidate",
        "experiment_shadow",
    }


def test_decision_dashboard_includes_signal_edge(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from webapp import decision_dashboard_service as dds
    from webapp import main as webapp_main

    monkeypatch.setattr(
        dds, "latest_validation_status", lambda skill_dir: {"passed": True, "run_status": "completed"}
    )
    monkeypatch.setattr(dds, "latest_slo_gate_status", lambda skill_dir: {"passed": True, "failures": []})
    monkeypatch.setattr(dds, "latest_ablation_status", lambda skill_dir: {"exists": False})
    monkeypatch.setattr(dds, "latest_registry_decision", lambda skill_dir: None)
    monkeypatch.setattr(
        dds,
        "signal_edge_validation_status",
        lambda skill_dir, run_id="control_legacy_aug": {
            "run_id": run_id,
            "state": "fix_entry_first",
            "binding_constraint": "entry_timing_not_rank_filter",
            "early_stopout_pct": 33.86,
            "hold_21_40d_pf": 3.42,
            "rank_filter_recommendation": "no_rank_filter_yet",
            "entry_quality_recommendation": "fix_entry_timing_not_rank_filter",
            "entry_quality_reason": "Early stop-outs driven by trailing_stop.",
        },
    )
    monkeypatch.setattr(webapp_main, "_load_state", lambda db, key, default: default)

    resp = client.get("/api/decision-dashboard", headers=_auth_headers())
    assert resp.status_code == 200
    signal_edge = resp.json()["data"]["signal_edge"]
    assert signal_edge["state"] == "fix_entry_first"
    assert signal_edge["rank_filter_recommendation"] == "no_rank_filter_yet"


def test_decision_dashboard_includes_signal_stack_scenarios(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from webapp import decision_dashboard_service as dds
    from webapp import main as webapp_main

    monkeypatch.setattr(
        dds, "latest_validation_status", lambda skill_dir: {"passed": True, "run_status": "completed"}
    )
    monkeypatch.setattr(dds, "latest_slo_gate_status", lambda skill_dir: {"passed": True, "failures": []})
    monkeypatch.setattr(dds, "latest_ablation_status", lambda skill_dir: {"exists": False})
    monkeypatch.setattr(dds, "latest_registry_decision", lambda skill_dir: None)
    monkeypatch.setattr(webapp_main, "_load_state", lambda db, key, default: default)
    monkeypatch.setattr(
        dds,
        "signal_edge_validation_status",
        lambda skill_dir, run_id="control_legacy_aug": {
            "run_id": run_id,
            "state": "fix_entry_first",
            "signal_stack_counterfactual": {
                "pf_mean": 1.05,
                "worst_era_pf": 0.92,
                "passes_promotion_gates": False,
                "promotion_gates": {"pf_mean_min": 1.20, "worst_era_pf_min": 1.00},
                "scenarios": [
                    {
                        "key": "legacy_baseline",
                        "label": "legacy_baseline",
                        "pf_mean": 0.88,
                        "worst_era_pf": 0.71,
                        "passes_promotion_gates": False,
                    },
                    {
                        "key": "exit_grace_breakout_buffer_0.010",
                        "label": "exit_grace_breakout_buffer_0.010",
                        "pf_mean": 1.05,
                        "worst_era_pf": 0.92,
                        "passes_promotion_gates": False,
                    },
                ],
            },
        },
    )

    resp = client.get("/api/decision-dashboard", headers=_auth_headers())
    assert resp.status_code == 200
    stack = resp.json()["data"]["signal_edge"]["signal_stack_counterfactual"]
    assert isinstance(stack.get("scenarios"), list)
    assert len(stack["scenarios"]) == 2
    assert stack["promotion_gates"]["pf_mean_min"] == 1.20


def test_decision_dashboard_blocked_when_gates_fail(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from webapp import decision_dashboard_service as dds
    from webapp import main as webapp_main

    monkeypatch.setattr(
        dds,
        "latest_validation_status",
        lambda skill_dir: {"passed": False, "run_status": "completed"},
    )
    monkeypatch.setattr(
        dds,
        "latest_slo_gate_status",
        lambda skill_dir: {"passed": False, "failures": ["api_5xx_rate"]},
    )
    monkeypatch.setattr(dds, "latest_registry_decision", lambda skill_dir: None)
    monkeypatch.setattr(webapp_main, "_load_state", lambda db, key, default: default)

    resp = client.get("/api/decision-dashboard", headers=_auth_headers())
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["reliability"]["state"] == "at_risk"
    assert data["promotion_readiness"]["release_gate_ready"] is False
    assert data["reliability"]["slo_failures"] == ["api_5xx_rate"]


def test_decision_dashboard_includes_scan_preflight(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from webapp import decision_dashboard_service as dds
    from webapp import main as webapp_main

    monkeypatch.setattr(
        dds, "latest_validation_status", lambda skill_dir: {"passed": True, "run_status": "completed"}
    )
    monkeypatch.setattr(dds, "latest_slo_gate_status", lambda skill_dir: {"passed": True, "failures": []})
    monkeypatch.setattr(dds, "latest_ablation_status", lambda skill_dir: {"exists": False})
    monkeypatch.setattr(dds, "latest_registry_decision", lambda skill_dir: None)
    monkeypatch.setattr(webapp_main, "_load_state", lambda db, key, default: default)
    monkeypatch.setattr(
        dds,
        "signal_edge_scan_preflight",
        lambda skill_dir, run_id="control_legacy_aug": {
            "experiment_recommended": True,
            "experiment_env_ready": False,
            "missing_env": ["ENTRY_SHADOW_DISABLE_SMA50_FILTERS=true"],
            "warnings": ["env not ready"],
            "ready_for_experiment_scan": False,
        },
    )

    resp = client.get("/api/decision-dashboard", headers=_auth_headers())
    assert resp.status_code == 200
    preflight = resp.json()["data"]["scan_preflight"]
    assert preflight["experiment_recommended"] is True
    assert preflight["experiment_env_ready"] is False


def test_scan_lifecycle_includes_signal_edge_preflight(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    from webapp import main as webapp_main

    monkeypatch.setattr(
        webapp_main,
        "_signal_edge_scan_preflight",
        lambda skill_dir, run_id="control_legacy_aug": {
            "experiment_recommended": True,
            "experiment_env_ready": True,
            "ready_for_experiment_scan": True,
            "warnings": [],
        },
    )
    monkeypatch.setattr(webapp_main, "_load_state", lambda db, key, default: default)

    resp = client.get("/api/scan-lifecycle", headers=_auth_headers())
    assert resp.status_code == 200
    preflight = resp.json()["data"]["signal_edge_preflight"]
    assert preflight["experiment_env_ready"] is True
