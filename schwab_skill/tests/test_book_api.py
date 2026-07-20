"""API tests for Book journal + tax prefs (mocked Schwab)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from webapp import main
from webapp.db import Base, engine


def setup_module() -> None:
    Base.metadata.create_all(bind=engine)


def test_tax_prefs_gate_estimate() -> None:
    with TestClient(main.app) as client:
        with patch(
            "core.book_service.fetch_trades_for_skill",
            return_value=([], {"error": None, "count": 0, "source": "schwab"}),
        ):
            r = client.get("/api/book/tax")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        # May already be configured from a prior local DB; assert shape only when blank
        assert "rates_configured" in body["data"]
        assert "estimate" in body["data"]
        if not body["data"]["rates_configured"]:
            assert body["data"]["estimate"] is None

        saved = client.post(
            "/api/book/tax/prefs",
            json={
                "federal_st_rate": 0.24,
                "federal_lt_rate": 0.15,
                "state_rate": 0.05,
                "tax_year": 2026,
            },
        )
        assert saved.status_code == 200
        assert saved.json()["ok"] is True
        assert saved.json()["data"]["rates_configured"] is True


def test_journal_quick_and_full_note() -> None:
    with TestClient(main.app) as client:
        with patch("execution.get_account_status", return_value={"positions": []}):
            with patch("core.book_service.open_position_symbols", return_value=["AAPL"]):
                listed = client.get("/api/book/journal")
        assert listed.status_code == 200
        assert listed.json()["ok"] is True

        thesis = client.post("/api/book/journal/AAPL/thesis", json={"thesis": "Breakout hold"})
        assert thesis.json()["ok"] is True
        assert thesis.json()["data"]["thesis"] == "Breakout hold"

        quick = client.post(
            "/api/book/journal/notes",
            json={
                "symbol": "AAPL",
                "mode": "quick",
                "body": "Holding through earnings",
                "note_type": "hold",
                "note_date": date.today().isoformat(),
            },
        )
        assert quick.json()["ok"] is True
        assert quick.json()["data"]["mode"] == "quick"

        full = client.post(
            "/api/book/journal/notes",
            json={
                "symbol": "AAPL",
                "mode": "full",
                "body": "Exit review",
                "note_type": "exit",
                "template": {
                    "setup": "VCP",
                    "entry": "52w high",
                    "stop": "8%",
                    "target": "20%",
                    "emotions": "calm",
                    "followed_plan": "yes",
                },
            },
        )
        assert full.json()["ok"] is True
        assert full.json()["data"]["mode"] == "full"
        assert full.json()["data"]["template"]["setup"] == "VCP"

        detail = client.get("/api/book/journal/AAPL")
        assert detail.json()["ok"] is True
        assert len(detail.json()["data"]["notes"]) >= 2


def test_book_export_status_and_download(tmp_path: Path) -> None:
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
    export_path = tmp_path / "book_ytd_2026.xlsx"

    with TestClient(main.app) as client:
        status = client.get("/api/book/export/status")
        assert status.status_code == 200
        assert status.json()["ok"] is True

        with patch(
            "core.book_export.fetch_trades_for_skill",
            return_value=(raw, meta),
        ):
            with patch("core.book_export.resolve_export_path", return_value=export_path):
                with patch("webapp.routes.book.SKILL_DIR", tmp_path):
                    resp = client.get("/api/book/export?tax_year=2026")
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert bytes(resp.content).startswith(b"PK\x03\x04")
        assert export_path.is_file()
