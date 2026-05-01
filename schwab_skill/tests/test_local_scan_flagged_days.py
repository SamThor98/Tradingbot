"""
Regression coverage for local-mode `flagged_days` enrichment.

Mirrors the SaaS pattern in webapp/main_saas.py: each signal in a scan response
should carry a `flagged_days` count derived from how many distinct UTC days the
local user produced a ScanResult row for that ticker.

Without these helpers, the dashboard's "Days Flagged" column was always
rendering "—" because `flagged_days` never appeared on the local payload (see
the troubleshoot in the chat history). The tests below pin the contract so the
column stays populated as we iterate on the scan worker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webapp.db import Base
from webapp.models import ScanResult, User


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        # The ScanResult FK requires a User row for the local dashboard.
        db.add(User(id="local", email=None, auth_provider="local_dashboard"))
        db.commit()
        yield db
    finally:
        db.close()
        engine.dispose()


def _seed_scan_result(db, ticker: str, *, days_ago: int) -> None:
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    row = ScanResult(
        user_id="local",
        job_id=f"job-{ticker}-{days_ago}",
        ticker=ticker,
        signal_score=70.0,
        payload_json={"ticker": ticker},
    )
    db.add(row)
    db.flush()
    # Override created_at after insert so tests can place rows on specific calendar days.
    row.created_at = created
    db.commit()


def test_enrich_counts_distinct_calendar_days(db_session) -> None:
    from webapp.main import _enrich_signals_with_flagged_days

    _seed_scan_result(db_session, "AAPL", days_ago=0)
    _seed_scan_result(db_session, "AAPL", days_ago=2)
    _seed_scan_result(db_session, "AAPL", days_ago=5)
    _seed_scan_result(db_session, "MSFT", days_ago=1)

    signals = [{"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "GOOGL"}]
    out = _enrich_signals_with_flagged_days(db_session, signals)

    by_ticker = {sig["ticker"]: sig for sig in out}
    assert by_ticker["AAPL"]["flagged_days"] == 3
    assert by_ticker["MSFT"]["flagged_days"] == 1
    # Tickers without history get a deterministic 0 so the UI can render "—" via `|| "—"`.
    assert by_ticker["GOOGL"]["flagged_days"] == 0


def test_enrich_respects_lookback_window(db_session) -> None:
    from webapp.main import _enrich_signals_with_flagged_days

    _seed_scan_result(db_session, "AAPL", days_ago=1)
    # Far outside the explicit 7-day lookback below — must not be counted.
    _seed_scan_result(db_session, "AAPL", days_ago=100)

    signals = [{"ticker": "AAPL"}]
    out = _enrich_signals_with_flagged_days(db_session, signals, lookback_days=7)

    assert out[0]["flagged_days"] == 1


def test_persist_then_enrich_round_trip(db_session) -> None:
    from webapp.main import (
        _enrich_signals_with_flagged_days,
        _persist_scan_results_local,
    )

    # Seed prior history so the "now" insert produces flagged_days=2.
    _seed_scan_result(db_session, "AAPL", days_ago=3)

    new_signals = [{"ticker": "AAPL", "signal_score": 71.4, "advisory": {"p_up_10d": 0.6}}]
    inserted = _persist_scan_results_local(db_session, "job-now", new_signals)
    assert inserted == 1

    out = _enrich_signals_with_flagged_days(db_session, new_signals)
    # Two distinct calendar days: today + the seeded day three days ago.
    assert out[0]["flagged_days"] == 2
    # Persistence must not strip enrichment-relevant fields.
    assert out[0]["signal_score"] == pytest.approx(71.4)


def test_enrich_handles_empty_signals(db_session) -> None:
    from webapp.main import _enrich_signals_with_flagged_days

    assert _enrich_signals_with_flagged_days(db_session, []) == []
    assert _enrich_signals_with_flagged_days(db_session, None) == []


def test_persist_skips_signals_without_ticker(db_session) -> None:
    from webapp.main import _persist_scan_results_local

    rows = [{"ticker": ""}, {"signal_score": 60.0}, {"ticker": "NVDA"}]
    inserted = _persist_scan_results_local(db_session, "job-x", rows)
    assert inserted == 1
    persisted = db_session.query(ScanResult).filter(ScanResult.user_id == "local").all()
    assert {r.ticker for r in persisted} == {"NVDA"}
