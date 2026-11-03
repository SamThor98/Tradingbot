from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import data_health as dh

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent


def test_fixture_quote_has_parseable_timestamp() -> None:
    q = json.loads((ROOT / "fixtures" / "schwab_quote_aapl.json").read_text(encoding="utf-8"))
    assert dh.parse_quote_epoch_ms(q) is not None


def test_merge_operator_payload_defaults() -> None:
    assert dh.merge_operator_payload(None)["data_quality"] == "unknown"


def test_scan_quote_max_age_keeps_live_budget_in_session() -> None:
    now = datetime(2026, 9, 8, 11, 0, tzinfo=ET)
    assert dh.scan_quote_max_age_sec(600.0, now=now) == 600.0


def test_scan_quote_max_age_widens_after_extended_close() -> None:
    now = datetime(2026, 9, 8, 23, 33, tzinfo=ET)
    budget = dh.scan_quote_max_age_sec(600.0, now=now)
    # 20:00 → 23:33 is 3h33m plus 30m slack.
    assert budget > 12_000
    assert budget < 16_000


def _quote_at(ts: datetime) -> dict:
    return {
        "symbol": "SPY",
        "lastPrice": 500.0,
        "quoteTimeInLong": int(ts.timestamp() * 1000),
    }


def _daily(last: date) -> pd.DataFrame:
    return pd.DataFrame({"close": [500.0]}, index=pd.DatetimeIndex([pd.Timestamp(last)]))


def _patch_feed(monkeypatch, quote_ts: datetime, bar_day: date) -> None:
    monkeypatch.setattr("market_data.get_current_quote", lambda *a, **k: _quote_at(quote_ts))
    monkeypatch.setattr("market_data.get_daily_history", lambda *a, **k: _daily(bar_day))
    monkeypatch.setattr("config.get_sec_enrichment_enabled", lambda *a, **k: False)
    monkeypatch.setattr("config.get_data_quote_max_age_sec", lambda *a, **k: 600.0)
    monkeypatch.setattr("config.get_data_bar_max_staleness_days", lambda *a, **k: 7)
    monkeypatch.setattr("config.get_data_crosscheck_enabled", lambda *a, **k: False)


def test_scan_session_after_hours_last_print_is_not_stale(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 9, 8, 23, 33, tzinfo=ET)
    print_ts = datetime(2026, 9, 8, 20, 0, tzinfo=ET)
    _patch_feed(monkeypatch, print_ts, date(2026, 9, 8))
    snap = dh.assess_scan_session_data_health(auth=None, skill_dir=tmp_path, now=now)
    assert snap["data_quality"] == "ok"
    assert not any(str(r).startswith("quote_stale") for r in snap["reasons"])


def test_live_mode_after_hours_still_marks_quote_stale(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 9, 8, 23, 33, tzinfo=ET)
    print_ts = datetime(2026, 9, 8, 20, 0, tzinfo=ET)
    _patch_feed(monkeypatch, print_ts, date(2026, 9, 8))
    snap = dh.assess_symbol_data_health(
        "SPY", None, tmp_path, quote_age_mode="live", now=now
    )
    assert snap["data_quality"] == "stale"
    assert any(str(r).startswith("quote_stale") for r in snap["reasons"])


def test_scan_session_rth_old_quote_is_stale(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 9, 8, 11, 0, tzinfo=ET)
    print_ts = datetime(2026, 9, 8, 10, 0, tzinfo=ET)
    _patch_feed(monkeypatch, print_ts, date(2026, 9, 8))
    snap = dh.assess_scan_session_data_health(auth=None, skill_dir=tmp_path, now=now)
    assert snap["data_quality"] == "stale"


def test_quote_unavailable_surfaces_provider_failure_detail(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 9, 8, 11, 0, tzinfo=ET)
    monkeypatch.setattr("market_data.get_current_quote", lambda *a, **k: None)
    monkeypatch.setattr(
        "market_data.get_current_quote_with_status",
        lambda *a, **k: (None, {"reason": "http_error", "http_status": 401, "fallback_provider": None}),
    )
    monkeypatch.setattr("market_data.get_daily_history", lambda *a, **k: _daily(date(2026, 9, 8)))
    monkeypatch.setattr("config.get_sec_enrichment_enabled", lambda *a, **k: False)
    monkeypatch.setattr("config.get_data_quote_max_age_sec", lambda *a, **k: 600.0)
    monkeypatch.setattr("config.get_data_bar_max_staleness_days", lambda *a, **k: 7)
    monkeypatch.setattr("config.get_data_crosscheck_enabled", lambda *a, **k: False)
    snap = dh.assess_symbol_data_health("SPY", None, tmp_path, quote_age_mode="scan", now=now)
    assert snap["data_quality"] == "degraded"
    assert "quote_unavailable" in snap["reasons"]
    assert snap["details"]["quote_http_status"] == 401
    assert snap["details"]["quote_failure_reason"] == "http_error"


def test_scan_weekend_uses_friday_extended_close(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=ET)
    print_ts = datetime(2026, 9, 4, 20, 0, tzinfo=ET)
    _patch_feed(monkeypatch, print_ts, date(2026, 9, 4))
    snap = dh.assess_scan_session_data_health(auth=None, skill_dir=tmp_path, now=now)
    assert snap["data_quality"] == "ok"
