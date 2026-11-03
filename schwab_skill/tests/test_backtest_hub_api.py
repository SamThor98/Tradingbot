"""Allowlist + hub API for the Backtest landing page."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webapp.backtest_hub import _open_research_card, resolve_artifact_path
from webapp.db import Base
from webapp.models import User


def test_resolve_artifact_path_rejects_traversal_and_secrets(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "validation_artifacts").mkdir()
    assert resolve_artifact_path(tmp_path, "../.env") is None
    assert resolve_artifact_path(tmp_path, "..\\..\\.env") is None
    assert resolve_artifact_path(tmp_path, ".env") is None
    assert resolve_artifact_path(tmp_path, "tokens_account.enc") is None
    assert resolve_artifact_path(tmp_path, "not-on-the-list.json") is None
    allowed = resolve_artifact_path(tmp_path, "phase2_edge_audit.json")
    assert allowed is not None
    assert allowed.name == "phase2_edge_audit.json"
    assert allowed.parent.name == "validation_artifacts"
    research = resolve_artifact_path(tmp_path, "open_research_sort_exit.json")
    assert research is not None
    assert research.name == "open_research_sort_exit.json"


def test_open_research_card_keeps_live_sort(tmp_path: Path) -> None:
    art = tmp_path / "validation_artifacts"
    art.mkdir()
    (art / "open_research_sort_exit.json").write_text(
        '{"verdict":"shadow_ready_sort_key_not_promoted","generated_at":"2026-09-23T00:00:00+00:00",'
        '"control_wf_inv_pts_pf":1.3862,"control_take_every_pf":1.1637,'
        '"control_top5_signal_pf":1.155,"exit_grace_pf":1.324,"pead_coverage_20d":0.9774}',
        encoding="utf-8",
    )
    card = _open_research_card(tmp_path)
    assert card["exists"] is True
    assert card["artifact_name"] == "open_research_sort_exit.json"
    assert "signal_score" in card["next_step"]
    assert card["detail"]["control_wf_inv_pts_pf"] == 1.3862


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
    pytest.importorskip("config")
    pytest.importorskip("execution")
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


def test_hub_js_exposes_test_further_column() -> None:
    hub_js = (SKILL_DIR / "webapp" / "static" / "panels" / "backtestHub.js").read_text(encoding="utf-8")
    html = (SKILL_DIR / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    assert "Test further" in hub_js
    assert "horizon_actions" in hub_js
    assert "btBriefingDetails" in html
    assert "bt-board-filter-bar" in html
    assert "backtestStudyPanel" in html
    assert "btStudyNextBtn" in html
    assert "/api/backtest-study" in hub_js


def test_hub_returns_queue_and_related(client: TestClient) -> None:
    resp = client.get("/api/backtest-hub", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert "queue" in data
    assert "related" in data
    assert "runs" in data
    assert "leaderboard" in data
    assert "qualified" in data["leaderboard"]
    assert "thin" in data["leaderboard"]
    assert data["leaderboard"]["criteria"]["min_trades"] == 35
    assert data["leaderboard"]["criteria"]["sort"] == "trust_then_robustness_then_pf"
    assert "latest_success" in data
    assert "one backtest at a time" in data["queue"]["concurrency_note"].lower()


def test_artifact_detail_rejects_env_and_traversal(client: TestClient) -> None:
    for name in (".env", "..%2F.env", "tokens_account.enc"):
        resp = client.get(f"/api/backtest-artifacts/{name}", headers=_auth_headers())
        assert resp.status_code == 404, name


def test_missing_run_is_404(client: TestClient) -> None:
    resp = client.get("/api/backtest-runs/does-not-exist", headers=_auth_headers())
    assert resp.status_code == 404


def _sample_spec() -> dict:
    return {
        "schema_version": 1,
        "universe_mode": "tickers",
        "tickers": ["AAPL", "MSFT"],
        "start_date": "2020-01-01",
        "end_date": "2023-01-01",
    }


def test_queue_while_busy_enqueues(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from webapp import main as webapp_main

    monkeypatch.setattr(webapp_main, "_kick_next_local_backtest_locked", lambda db: None)
    first = client.post("/api/backtest-runs", headers=_auth_headers(), json={"spec": _sample_spec()})
    assert first.status_code == 200
    assert first.json()["ok"] is True
    second = client.post("/api/backtest-runs", headers=_auth_headers(), json={"spec": _sample_spec()})
    assert second.status_code == 200
    assert second.json()["data"]["run_id"] != first.json()["data"]["run_id"]
    hub = client.get("/api/backtest-hub", headers=_auth_headers())
    runs = hub.json()["data"]["runs"]
    assert len(runs) >= 2
    assert all(r["status"] == "queued" for r in runs[:2])


def test_waiting_cap_returns_409(client: TestClient, monkeypatch: pytest.MonkeyPatch, test_db) -> None:
    import json
    import uuid

    from webapp import main as webapp_main
    from webapp.models import BacktestRun

    monkeypatch.setattr(webapp_main, "_kick_next_local_backtest_locked", lambda db: None)
    db = test_db()
    try:
        for _ in range(webapp_main.LOCAL_BACKTEST_MAX_WAITING):
            db.add(
                BacktestRun(
                    id=uuid.uuid4().hex,
                    user_id="local",
                    status="queued",
                    spec_json=json.dumps(_sample_spec()),
                )
            )
        db.commit()
    finally:
        db.close()
    resp = client.post("/api/backtest-runs", headers=_auth_headers(), json={"spec": _sample_spec()})
    assert resp.status_code == 409


def test_cancel_queued_run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from webapp import main as webapp_main

    monkeypatch.setattr(webapp_main, "_kick_next_local_backtest_locked", lambda db: None)
    queued = client.post("/api/backtest-runs", headers=_auth_headers(), json={"spec": _sample_spec()})
    run_id = queued.json()["data"]["run_id"]
    cancelled = client.post(f"/api/backtest-runs/{run_id}/cancel", headers=_auth_headers())
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"


def test_cancel_running_is_409(client: TestClient, test_db) -> None:
    import json
    import uuid

    from webapp.models import BacktestRun

    run_id = uuid.uuid4().hex
    db = test_db()
    try:
        db.add(
            BacktestRun(
                id=run_id,
                user_id="local",
                status="running",
                spec_json=json.dumps(_sample_spec()),
            )
        )
        db.commit()
    finally:
        db.close()
    resp = client.post(f"/api/backtest-runs/{run_id}/cancel", headers=_auth_headers())
    assert resp.status_code == 409


def test_study_queues_anchor_and_second_click_does_not_add_a_family(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from webapp import main as webapp_main

    monkeypatch.setattr(webapp_main, "SKILL_DIR", tmp_path)
    monkeypatch.setattr(webapp_main, "_kick_next_local_backtest_locked", lambda db: None)
    idle = client.get("/api/backtest-study", headers=_auth_headers())
    assert idle.status_code == 200
    assert idle.json()["data"]["action"] == "start"

    spec = {
        "schema_version": 1,
        "universe_mode": "watchlist",
        "universe_preset": "nasdaq100",
        "strategy_ids": ["donchian_20"],
        "start_date": "2024-01-01",
        "end_date": "2026-01-01",
    }
    first = client.post("/api/backtest-study/next", headers=_auth_headers(), json={"spec": spec})
    assert first.status_code == 200
    body = first.json()["data"]
    assert body["queued_run_id"]
    assert body["action"] == "wait"
    assert str(body["hypothesis"]).lower().find("anchor") >= 0 or "factor" in str(body["hypothesis"]).lower()

    second = client.post("/api/backtest-study/next", headers=_auth_headers(), json={})
    assert second.status_code == 200
    assert not second.json()["data"].get("queued_run_id")

    listed = client.get("/api/backtest-runs?limit=20", headers=_auth_headers())
    study_runs = [
        row
        for row in listed.json()["data"]
        if str((row.get("spec") or {}).get("theory_name") or "").startswith("study ")
    ]
    assert len(study_runs) == 1
    assert "stage2_52w_pct" not in ((study_runs[0].get("spec") or {}).get("overrides") or {})


def _clean_anchor_result() -> dict:
    return {
        "profit_factor_net": 1.1,
        "total_trades": 80,
        "max_drawdown_net_pct": 10.0,
        "diagnostics": {},
        "data_integrity": {
            "history_fetch_total": 10,
            "history_fetch_empty": 0,
            "history_fallback_used": 0,
            "history_provider_schwab": 10,
        },
    }


def _mark_run_success(test_db, run_id: str, result: dict) -> None:
    import json

    from webapp.models import BacktestRun

    db = test_db()
    try:
        row = db.query(BacktestRun).filter(BacktestRun.id == run_id).one()
        row.status = "success"
        row.result_json = json.dumps(result)
        db.commit()
    finally:
        db.close()


def _study_run_count(client: TestClient, study_id: str) -> int:
    listed = client.get("/api/backtest-runs?limit=20", headers=_auth_headers())
    needle = f"study {study_id}"
    return sum(
        1
        for row in listed.json()["data"]
        if needle in str((row.get("spec") or {}).get("theory_name") or "")
    )


def test_auto_study_queues_one_next_test_after_the_anchor(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    test_db,
) -> None:
    from webapp import main as webapp_main

    monkeypatch.setattr(webapp_main, "SKILL_DIR", tmp_path)
    monkeypatch.setattr(webapp_main, "_kick_next_local_backtest_locked", lambda db: None)
    spec = {
        "schema_version": 1,
        "universe_mode": "watchlist",
        "universe_preset": "nasdaq100",
        "strategy_ids": ["donchian_20"],
        "start_date": "2024-01-01",
        "end_date": "2026-01-01",
    }
    started = client.post(
        "/api/backtest-study/next",
        headers=_auth_headers(),
        json={"spec": spec, "auto": True},
    )
    assert started.status_code == 200
    anchor_id = started.json()["data"]["queued_run_id"]
    study_id = started.json()["data"]["study_id"]
    assert started.json()["data"]["auto"] is True
    _mark_run_success(test_db, anchor_id, _clean_anchor_result())

    db = test_db()
    try:
        queued = webapp_main._continue_auto_study(db, anchor_id)
        assert queued
        assert queued != anchor_id
        again = webapp_main._continue_auto_study(db, anchor_id)
        assert again is None
    finally:
        db.close()
    assert _study_run_count(client, study_id) == 2


def test_auto_study_does_not_queue_when_data_is_degraded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    test_db,
) -> None:
    from webapp import main as webapp_main

    monkeypatch.setattr(webapp_main, "SKILL_DIR", tmp_path)
    monkeypatch.setattr(webapp_main, "_kick_next_local_backtest_locked", lambda db: None)
    spec = {
        "schema_version": 1,
        "universe_mode": "watchlist",
        "universe_preset": "nasdaq100",
        "strategy_ids": ["donchian_20"],
        "start_date": "2024-01-01",
        "end_date": "2026-01-01",
    }
    started = client.post(
        "/api/backtest-study/next",
        headers=_auth_headers(),
        json={"spec": spec, "auto": True},
    )
    anchor_id = started.json()["data"]["queued_run_id"]
    study_id = started.json()["data"]["study_id"]
    dirty = _clean_anchor_result()
    dirty["data_integrity"] = {
        "history_fetch_total": 10,
        "history_fetch_empty": 0,
        "history_fallback_used": 8,
        "history_provider_yfinance": 8,
    }
    _mark_run_success(test_db, anchor_id, dirty)
    db = test_db()
    try:
        assert webapp_main._continue_auto_study(db, anchor_id) is None
    finally:
        db.close()
    assert _study_run_count(client, study_id) == 1


def test_hub_failure_replaces_loading_copy() -> None:
    hub_js = (SKILL_DIR / "webapp" / "static" / "panels" / "backtestHub.js").read_text(encoding="utf-8")
    app_js = (SKILL_DIR / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert "btLeaderboardBody" in hub_js
    assert "Could not load backtest home." in hub_js
    assert "if (!out.ok)" in app_js
    assert "No chart data available for ${ticker}." in app_js
    assert "This computer uses the local API key." in app_js
