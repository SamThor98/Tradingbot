"""API contract tests for GET /api/sec/compare."""

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
        "left": {
            "ticker": "AAPL",
            "form": "10-K",
            "filing_date": "2026-03-31",
            "filing_url": "https://example.com/aapl-10k",
            "from_cache": False,
            "source": "sec",
            "confidence": 72,
            "verdict": "neutral",
        },
        "right": {
            "ticker": "AAPL",
            "form": "10-K",
            "filing_date": "2025-03-31",
            "filing_url": "https://example.com/aapl-10k-prior",
            "from_cache": True,
            "source": "cache",
            "confidence": 68,
            "verdict": "neutral",
        },
        "compare": {
            "ok": True,
            "mode": "ticker_over_time",
            "left_label": "AAPL latest",
            "right_label": "AAPL prior",
            "similarities": ["Shared risk terms: liquidity."],
            "differences": ["Guidance tone shifted toward caution."],
            "material_changes": ["Inventory cycle risk now referenced."],
            "investor_takeaway": "Execution language tightened.",
            "compare_confidence": 74,
            "change_summary": {
                "evidence_ranked": [{"claim": "Guidance", "quote": "Management cites demand volatility."}],
                "plain_english_rationale": ["Guidance shift: positive -> neutral."],
            },
        },
    }


def test_sec_compare_over_time_contract(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from webapp.routes import research

    monkeypatch.setattr(research, "compare_ticker_over_time", lambda *args, **kwargs: _compare_stub())
    resp = client.get(
        "/api/sec/compare",
        params={"mode": "ticker_over_time", "ticker": "aapl", "form_type": "10-k"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    data = payload["data"]
    compare = data["compare"]
    assert compare["summary_headline"]
    assert compare["narrative_summary"]
    assert compare["compare_confidence"] == 74
    assert compare["analysis_mode"] == "full_text"
    assert "data_freshness" in compare
    assert compare["data_freshness"]["left_from_cache"] is False
    assert compare["data_freshness"]["right_from_cache"] is True


def test_sec_compare_vs_ticker_contract(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from webapp.routes import research

    stub = _compare_stub()
    stub["mode"] = "ticker_vs_ticker"
    monkeypatch.setattr(research, "compare_ticker_vs_ticker", lambda *args, **kwargs: stub)
    resp = client.get(
        "/api/sec/compare",
        params={"mode": "ticker_vs_ticker", "ticker": "nvda", "ticker_b": "amd"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["data"]["compare"]["top_differences"]


def test_sec_compare_missing_ticker(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    resp = client.get("/api/sec/compare", params={"mode": "ticker_over_time", "ticker": ""})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is False
    assert "ticker" in payload["error"].lower()


def test_sec_compare_disabled(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from webapp.routes import research

    monkeypatch.setattr(
        research,
        "_sec_analysis_settings",
        lambda: {
            "analysis_enabled": True,
            "compare_enabled": False,
            "user_agent": "test-agent",
            "cache_hours": 24.0,
            "max_chars": 50000,
            "max_compare_items": 6,
            "llm_enabled": False,
        },
    )
    resp = client.get(
        "/api/sec/compare",
        params={"mode": "ticker_over_time", "ticker": "aapl"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is False
    assert "disabled" in payload["error"].lower()


def test_sec_compare_include_management_dashboard(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    from webapp.routes import research

    monkeypatch.setattr(research, "compare_ticker_over_time", lambda *args, **kwargs: _compare_stub())
    resp = client.get(
        "/api/sec/compare",
        params={
            "mode": "ticker_over_time",
            "ticker": "aapl",
            "include_management_dashboard": "true",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    data = payload["data"]
    assert "management_dashboard" in data
    dashboard = data["management_dashboard"]
    assert dashboard["integrity_scorecard"]["score"] >= 0
    assert dashboard["data_fidelity"]["say_do_timeline"] == "derived_from_compare_deltas"
