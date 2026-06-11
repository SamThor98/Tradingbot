from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from webapp import main as webapp_main
    from webapp.routes import research

    monkeypatch.setattr(
        research,
        "_sec_analysis_settings",
        lambda: {
            "analysis_enabled": True,
            "compare_enabled": True,
            "user_agent": "test-agent",
            "cache_hours": 24.0,
            "max_chars": 50000,
            "max_compare_items": 6,
            "llm_enabled": False,
        },
    )
    monkeypatch.setattr(research, "_LOCAL_SEC_MGMT_PROFILE_OVERRIDE", None)
    app = webapp_main.app
    with TestClient(app) as test_client:
        yield test_client


def _compare_stub(**_: object) -> dict[str, object]:
    return {
        "ok": True,
        "mode": "ticker_over_time",
        "form_type": "10-K",
        "compare": {
            "summary_headline": "Commodity cycle pressure increased.",
            "narrative_summary": "Commodity and inventory signals dominate this filing delta.",
            "investor_takeaway": "Execution remains tied to a cyclical commodity setup.",
            "similarities": ["Gross margin language unchanged."],
            "differences": ["Commodity exposure disclosures increased."],
            "material_changes": ["Inventory cycle risk now explicitly referenced."],
            "compare_confidence": 71,
            "change_summary": {"evidence_ranked": [{"claim": "Commodity risk", "quote": "Management cites commodity volatility."}]},
        },
        "left": {"ticker": "X"},
        "right": {"ticker": "X"},
    }


def test_management_dashboard_auto_detects_profile(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from webapp.routes import research

    monkeypatch.setattr(research, "compare_ticker_over_time", lambda *args, **kwargs: _compare_stub())
    resp = client.get(
        "/api/sec/management-dashboard",
        params={"mode": "ticker_over_time", "ticker": "x", "form_type": "10-k"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    dashboard = payload["data"]["management_dashboard"]
    assert dashboard["profile"]["mode"] == "auto_detected"
    assert dashboard["profile"]["selected"] == "cyclical"
    assert isinstance(dashboard["attribution"]["group_level"], list)
    assert isinstance(dashboard["attribution"]["rule_level"], list)
    assert dashboard["data_fidelity"]["say_do_timeline"] == "derived_from_compare_deltas"


def test_management_dashboard_profile_override_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from webapp.routes import research

    monkeypatch.setattr(research, "compare_ticker_over_time", lambda *args, **kwargs: _compare_stub())

    set_resp = client.post(
        "/api/sec/management-dashboard/profile",
        json={
            "profile_override": "mature_compounder",
            "reason": "capital return profile",
            "evidence_ref": "filing-note-123",
        },
    )
    assert set_resp.status_code == 200
    set_payload = set_resp.json()
    assert set_payload["ok"] is True
    assert set_payload["data"]["profile_override"] == "mature_compounder"
    assert set_payload["data"]["last_override"]["reason"] == "capital return profile"
    assert set_payload["data"]["last_override"]["evidence_ref"] == "filing-note-123"

    run_resp = client.get(
        "/api/sec/management-dashboard",
        params={"mode": "ticker_over_time", "ticker": "x"},
    )
    assert run_resp.status_code == 200
    run_payload = run_resp.json()
    assert run_payload["ok"] is True
    profile = run_payload["data"]["management_dashboard"]["profile"]
    assert profile["selected"] == "mature_compounder"
    assert profile["persisted_override"] == "mature_compounder"
    assert profile["last_override"]["after"] == "mature_compounder"
    assert isinstance(profile["history_tail"], list)
    assert profile["history_tail"][-1]["after"] == "mature_compounder"

    clear_resp = client.post(
        "/api/sec/management-dashboard/profile",
        json={"profile_override": None},
    )
    assert clear_resp.status_code == 200
    clear_payload = clear_resp.json()
    assert clear_payload["ok"] is True
    assert clear_payload["data"]["profile_override"] is None
    assert clear_payload["data"]["last_override"]["reason"] == "unspecified"

