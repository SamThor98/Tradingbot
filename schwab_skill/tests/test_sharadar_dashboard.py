"""Sharadar Bundle dashboard snapshot + PIT as-of mapping."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from research.providers.sharadar_pit import SharadarPITAdapter  # noqa: E402
from sharadar_data import (  # noqa: E402
    art_row_asof,
    get_sharadar_research_snapshot,
    map_daily_row,
    pit_fields_from_parts,
    sharadar_preflight_item,
)
from webapp.routes.sharadar import normalize_ticker, research_sharadar  # noqa: E402


def test_art_row_asof_uses_filing_date_not_later_prints() -> None:
    rows = [
        {"ticker": "AAPL", "dimension": "ART", "datekey": "2017-01-31", "calendardate": "2016-12-31", "roe": 0.22},
        {"ticker": "AAPL", "dimension": "ART", "datekey": "2016-01-27", "calendardate": "2015-12-31", "roe": 0.18},
        {"ticker": "AAPL", "dimension": "ART", "datekey": "2016-07-27", "calendardate": "2016-06-30", "roe": 0.20},
    ]
    picked = art_row_asof(rows, "2016-06-01")
    assert picked["datekey"] == "2016-01-27"
    assert picked["roe"] == pytest.approx(0.18)


def test_daily_marketcap_and_ev_are_usd_millions() -> None:
    daily = map_daily_row({"ticker": "AAPL", "date": "2026-09-18", "marketcap": 4_905_541.7, "ev": 5_000_000.0, "pe": 38.0})
    assert daily["marketcap"] == pytest.approx(4_905_541.7 * 1_000_000.0)
    assert daily["ev"] == pytest.approx(5_000_000.0 * 1_000_000.0)
    assert daily["pe"] == pytest.approx(38.0)


def test_pit_fields_never_invent_zeros() -> None:
    fields = pit_fields_from_parts(daily={}, fundamentals={}, holders=[])
    assert fields["market_cap"] is None
    assert fields["float_shares"] is None
    assert fields["institutional_ownership"] is None
    assert fields["roe"] is None
    assert 0 not in fields.values()


def test_pit_fields_sum_holder_percent_when_present() -> None:
    daily = map_daily_row({"ticker": "AAPL", "date": "2016-06-01", "marketcap": 500_000.0})
    fields = pit_fields_from_parts(
        daily=daily,
        fundamentals={"roe": 0.21},
        holders=[{"percent": 4.0}, {"percent": 2.5}],
    )
    assert fields["market_cap"] == pytest.approx(500e9)
    assert fields["roe"] == pytest.approx(0.21)
    assert fields["institutional_ownership"] == pytest.approx(6.5)


def test_snapshot_fail_closed_without_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SHARADAR_API_KEY", raising=False)
    monkeypatch.delenv("SHARADAR_ENABLED", raising=False)
    payload = get_sharadar_research_snapshot("AAPL", skill_dir=tmp_path)
    assert payload["ok"] is False
    assert payload["enabled"] is False
    assert payload["pit_fields"]["market_cap"] is None
    assert payload["errors"][0] in {"sharadar_disabled", "missing_sharadar_api_key"}

    monkeypatch.setenv("SHARADAR_ENABLED", "true")
    missing = get_sharadar_research_snapshot("AAPL", skill_dir=tmp_path)
    assert missing["ok"] is False
    assert "missing_sharadar_api_key" in missing["errors"]


def test_preflight_warns_without_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SHARADAR_API_KEY", raising=False)
    item = sharadar_preflight_item(tmp_path)
    assert item["id"] == "sharadar"
    assert item["status"] == "warn"


def test_snapshot_maps_mocked_bundle_tables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SHARADAR_API_KEY", "test-key")
    monkeypatch.setenv("SHARADAR_CACHE_ENABLED", "false")

    tables = {
        "tickers": (200, [{"ticker": "AAPL", "name": "Apple Inc", "exchange": "NASDAQ", "sector": "Technology"}]),
        "daily": (200, [{"ticker": "AAPL", "date": "2026-09-18", "marketcap": 3.2e6, "pe": 32.1, "ps": 8.4}]),
        "fundamentals": (
            200,
            [
                {
                    "ticker": "AAPL",
                    "dimension": "ART",
                    "datekey": "2026-08-01",
                    "calendardate": "2026-06-30",
                    "revenueusd": 4e11,
                    "netincusd": 1e11,
                    "roe": 1.47,
                }
            ],
        ),
        "insiders": (
            200,
            [{"filingdate": "2026-08-15", "ownername": "Cook", "transactioncode": "S", "transactionshares": -1000}],
        ),
        "holdings_ticker": (
            200,
            [{"calendardate": "2026-06-30", "name": "Vanguard", "units": 1e9, "percent": 8.1}],
        ),
        "actions": (200, [{"date": "2026-05-01", "action": "dividend"}]),
        "events": (200, []),
        "sp500": (200, [{"date": "2026-09-18", "action": "current", "ticker": "AAPL"}]),
    }

    def fake_get(self, table: str, params: dict) -> tuple[int, object]:
        if table not in tables:
            return 200, []
        status, rows = tables[table]
        return status, rows

    monkeypatch.setattr("sharadar_data.SharadarClient.get_table", fake_get)
    payload = get_sharadar_research_snapshot("AAPL", skill_dir=tmp_path, asof="2026-09-18")
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["profile"]["name"] == "Apple Inc"
    assert payload["daily"]["marketcap"] == pytest.approx(3.2e12)
    assert payload["fundamentals"]["revenue"] == pytest.approx(4e11)
    assert payload["sp500"]["is_member"] is True
    assert payload["insiders"][0]["ownername"] == "Cook"
    assert payload["holders"][0]["name"] == "Vanguard"
    assert payload["pit_fields"]["market_cap"] == pytest.approx(3.2e12)
    assert payload["data_quality"] == "ok"


def test_art_row_maps_filing_date_field() -> None:
    from sharadar_data import map_fundamentals_row

    mapped = map_fundamentals_row(
        {
            "ticker": "AAPL",
            "dimension": "ART",
            "date": "2026-07-31",
            "calendardate": "2026-06-30",
            "roe": 1.47,
        }
    )
    assert mapped["datekey"] == "2026-07-31"
    assert mapped["calendardate"] == "2026-06-30"


def test_snapshot_sorts_on_date_not_legacy_columns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SHARADAR_API_KEY", "test-key")
    monkeypatch.setenv("SHARADAR_CACHE_ENABLED", "false")
    seen: dict[str, dict] = {}

    def fake_get(self, table: str, params: dict) -> tuple[int, object]:
        seen[table] = dict(params)
        return 200, []

    monkeypatch.setattr("sharadar_data.SharadarClient.get_table", fake_get)
    get_sharadar_research_snapshot("AAPL", skill_dir=tmp_path, asof="2026-09-18")
    assert seen["fundamentals"]["sort"] == "date.desc"
    assert seen["insiders"]["sort"] == "date.desc"
    assert seen["holdings"]["sort"] == "units.desc"
    assert "datekey" not in str(seen["fundamentals"].get("sort") or "")
    assert "filingdate" not in str(seen["insiders"].get("sort") or "")


def test_sloan_from_art_never_invents_zeros() -> None:
    from sharadar_research import forensic_from_sharadar, sloan_from_art

    assert sloan_from_art({"netinc": 10, "ncfo": None, "assets": 100}) is None
    snap = forensic_from_sharadar(
        {
            "ok": True,
            "enabled": True,
            "status": "ok",
            "daily": {},
            "fundamentals": {"netinc": 10, "ncfo": 8, "assets": 100},
            "fundamentals_history": [{"netinc": 10, "ncfo": 8, "assets": 100}],
        }
    )
    assert snap["sloan"]["sloan_ratio"] == pytest.approx(0.02)
    assert snap["data_lineage"] == "sharadar_art"


def test_qualifying_events_hide_empty() -> None:
    from sharadar_research import qualifying_corporate_events

    assert qualifying_corporate_events([], []) == []
    rows = qualifying_corporate_events(
        [{"date": "2026-05-01", "event": "dividend"}],
        [{"date": "2026-06-01", "event": "earnings call"}],
    )
    assert len(rows) == 1
    assert rows[0]["event"] == "dividend"


def test_compare_bundle_keeps_nulls() -> None:
    from sharadar_research import compare_bundle

    out = compare_bundle(
        {
            "ok": True,
            "enabled": True,
            "status": "ok",
            "ticker": "AAPL",
            "daily": {"pe": 32.1, "evebitda": None},
            "fundamentals": {"roe": 1.47, "revenue": 4e11, "fcf": None},
            "profile": {"name": "Apple"},
        },
        {
            "ok": True,
            "enabled": True,
            "status": "ok",
            "ticker": "MSFT",
            "daily": {"pe": 35.0, "evebitda": 22.0},
            "fundamentals": {"roe": 0.38, "revenue": 2e11, "fcf": 6e10},
            "profile": {"name": "Microsoft"},
        },
    )
    assert out["left"]["fcf"] is None
    assert out["left"]["evebitda"] is None
    assert out["right"]["pe"] == pytest.approx(35.0)


def test_research_compare_route_invalid_ticker() -> None:
    from webapp.routes.sharadar import research_sharadar_compare

    resp = research_sharadar_compare("!!!", "MSFT")
    assert resp.ok is False
    assert "Invalid ticker" in str(resp.error)


def test_adapter_fundamentals_asof_fail_closed_without_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SHARADAR_API_KEY", raising=False)
    adapter = SharadarPITAdapter(skill_dir=tmp_path)
    out = adapter.fundamentals_asof("AAPL", "2016-06-01")
    assert out["market_cap"] is None
    assert adapter.data_quality()["status"] == "error"


def test_pit_universe_provider_code_default_is_stub(tmp_path: Path) -> None:
    from config import get_pit_universe_provider

    assert get_pit_universe_provider(tmp_path) == "stub"
    assert normalize_ticker("aapl") == "AAPL"
    assert normalize_ticker("BRK.B") == "BRK.B"
    assert normalize_ticker("") is None
    assert normalize_ticker("not a ticker!!") is None


def test_research_route_invalid_ticker() -> None:
    resp = research_sharadar("!!!")
    assert resp.ok is False
    assert "Invalid ticker" in str(resp.error)


def test_saas_compare_route_wired() -> None:
    from webapp.main_saas import app

    paths = set(app.openapi().get("paths") or {})
    assert "/api/research/sharadar/{ticker}" in paths
    assert "/api/research/sharadar-compare/{ticker_a}/{ticker_b}" in paths
    tenant = (SKILL_DIR / "webapp" / "routes" / "tenant_sharadar.py").read_text(encoding="utf-8")
    assert "get_current_user" in tenant
    assert "get_sharadar_research_snapshot" in tenant


def test_scanner_forensic_and_rank_untouched() -> None:
    forensic = (SKILL_DIR / "forensic_accounting.py").read_text(encoding="utf-8")
    rank = (SKILL_DIR / "core" / "scoring_rank_v2.py").read_text(encoding="utf-8")
    assert "sharadar" not in forensic.lower()
    assert "sharadar" not in rank.lower()


def test_dashboard_panel_wired() -> None:
    static = SKILL_DIR / "webapp" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "screens" / "research.js").read_text(encoding="utf-8")
    api = (static / "modules" / "api.js").read_text(encoding="utf-8")
    assert 'id="sharadarBundleSection"' in html
    assert 'id="sharadarCompareSection"' in html
    assert "loadSharadarBundle" in js
    assert "/api/research/sharadar/" in api
    assert "/api/research/sharadar-compare/" in api
    panel = (static / "panels" / "sharadarFundamentals.js").read_text(encoding="utf-8")
    assert "does not change scan scores" in panel
    assert "datekey" in panel
    compare = (static / "panels" / "sharadarCompare.js").read_text(encoding="utf-8")
    assert "sharadarCompareSection" in compare
