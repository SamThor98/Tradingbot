"""Decision-packet scan recording and outcome labeling."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from core import decision_packet, outcome_backfill


def _history(start: str, closes: list[float], *, tz: str | None = None) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(closes), freq="D", tz=tz)
    return pd.DataFrame({"close": closes}, index=idx)


def _pending(ticker: str, created: datetime, price: float = 100.0) -> dict[str, Any]:
    packet = decision_packet.build_packet(
        ticker=ticker,
        kind="staged",
        signal={"price": price, "rank_score": 70.0},
        created_at=created,
    )
    return packet.model_dump(mode="json")


def test_compute_outcome_labels_tz_aware_history() -> None:
    created = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)
    packet = _pending("AAPL", created)
    closes = [100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110.0, 111]
    hist = _history("2026-08-01", closes, tz="America/New_York")
    out = outcome_backfill.compute_outcome(
        packet,
        history_provider=lambda _t: hist,
        horizon_days=10,
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert out is not None
    assert out["label"] == "win"
    assert out["realized_return_pct"] == 10.0
    assert out["horizon_days"] == 10


def test_compute_outcome_refuses_history_that_starts_after_the_decision() -> None:
    created = datetime(2026, 7, 1, tzinfo=timezone.utc)
    packet = _pending("AAPL", created)
    # 20 bars, but the window opens 30 days after the decision. Labeling the
    # first bar as the entry would invent a return.
    hist = _history("2026-07-31", [100.0 + i for i in range(20)])
    out = outcome_backfill.compute_outcome(
        packet,
        history_provider=lambda _t: hist,
        horizon_days=10,
        now=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert out is None


def test_run_local_backfill_writes_labels_and_skips_same_utc_day(tmp_path) -> None:
    created = datetime.now(timezone.utc) - timedelta(days=40)
    mature = _pending("AAPL", created, price=100.0)
    fresh = _pending("MSFT", datetime.now(timezone.utc), price=50.0)
    decision_packet.overwrite_packets(tmp_path, [mature, fresh])
    closes = [100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109, 90.0, 89]
    calls = {"n": 0}

    def provider(ticker: str) -> pd.DataFrame:
        calls["n"] += 1
        if ticker == "AAPL":
            return _history(created.date().isoformat(), closes)
        return _history(datetime.now(timezone.utc).date().isoformat(), [50.0, 51.0])

    now = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)
    first = outcome_backfill.run_local_backfill(
        tmp_path,
        history_provider=provider,
        horizon_days=10,
        now=now,
        force=True,
    )
    assert first["resolved"] == 1
    assert first["pending"] == 1
    stored = decision_packet.load_packets(tmp_path)
    by_ticker = {row["ticker"]: row for row in stored}
    assert by_ticker["AAPL"]["outcome"]["label"] == "loss"
    assert by_ticker["AAPL"]["outcome"]["realized_return_pct"] == -10.0
    assert by_ticker["MSFT"]["outcome"]["label"] == "pending"

    calls["n"] = 0
    second = outcome_backfill.run_local_backfill(
        tmp_path,
        history_provider=provider,
        horizon_days=10,
        now=now,
    )
    assert second["skipped"] == "already_ran_utc_day"
    assert calls["n"] == 0
    assert decision_packet.load_packets(tmp_path)[0]["outcome"]["label"] == "loss"


def test_failed_label_of_old_pending_is_retried_same_day(tmp_path) -> None:
    created = datetime.now(timezone.utc) - timedelta(days=40)
    decision_packet.overwrite_packets(tmp_path, [_pending("AAPL", created)])
    calls = {"n": 0}

    def provider(_ticker: str) -> pd.DataFrame:
        calls["n"] += 1
        return pd.DataFrame({"close": []})

    now = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)
    first = outcome_backfill.run_local_backfill(
        tmp_path, history_provider=provider, horizon_days=10, now=now, force=True
    )
    assert first["resolved"] == 0
    assert first["overdue_pending"] is True
    second = outcome_backfill.run_local_backfill(
        tmp_path, history_provider=provider, horizon_days=10, now=now
    )
    assert second["skipped"] is None
    assert calls["n"] == 2
    assert decision_packet.load_packets(tmp_path)[0]["outcome"]["label"] == "pending"


def test_needed_history_days_covers_old_pending() -> None:
    created = datetime.now(timezone.utc) - timedelta(days=200)
    days = outcome_backfill.needed_history_days(
        [_pending("IBM", created)],
        horizon_days=10,
    )
    assert days >= 200


def test_default_provider_requests_window_covering_old_pending(tmp_path, monkeypatch) -> None:
    created = datetime.now(timezone.utc) - timedelta(days=200)
    decision_packet.overwrite_packets(tmp_path, [_pending("IBM", created)])
    captured: dict[str, int] = {}

    def fake_history(ticker: str, days: int = 300, **_kwargs: Any) -> pd.DataFrame:
        captured["days"] = int(days)
        closes = [100.0 + i for i in range(15)]
        return _history(created.date().isoformat(), closes)

    monkeypatch.setattr("market_data.get_daily_history", fake_history)
    out = outcome_backfill.run_local_backfill(tmp_path, horizon_days=10, force=True)
    assert captured["days"] >= 200
    assert out["resolved"] == 1
    assert decision_packet.load_packets(tmp_path)[0]["outcome"]["label"] == "win"


def test_record_scan_packets_dedupes_same_utc_day(tmp_path) -> None:
    when = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
    signals = [
        {"ticker": "aapl", "signal_score": 81, "price": 190.5, "_filter_status": "kept"},
        {"ticker": "MSFT", "price": 400, "_filter_status": "filtered_quality_gates"},
    ]
    first = decision_packet.record_scan_packets(tmp_path, signals, created_at=when)
    assert first["recorded"] == 1
    assert first["skipped_filtered"] == 1
    second = decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "AAPL", "price": 191}],
        created_at=when + timedelta(hours=2),
    )
    assert second["recorded"] == 0
    assert second["skipped_duplicate"] == 1
    later = decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "AAPL", "price": 192, "composite_score": 77}],
        created_at=when + timedelta(days=1),
    )
    assert later["recorded"] == 1
    stored = decision_packet.load_packets(tmp_path)
    assert [row["ticker"] for row in stored] == ["AAPL", "AAPL"]
    assert stored[0]["kind"] == "staged"
    assert stored[0]["entry_price"] == 190.5
    assert stored[0]["rank_score"] == 81.0
    assert stored[1]["rank_score"] == 77.0


def test_run_scan_outcome_pass_records_and_labels(tmp_path) -> None:
    created = datetime.now(timezone.utc) - timedelta(days=30)
    decision_packet.record_scan_packets(
        tmp_path,
        [{"ticker": "NVDA", "price": 100, "signal_score": 60}],
        created_at=created,
    )
    closes = [100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109, 112.0, 113]

    def provider(_ticker: str) -> pd.DataFrame:
        return _history(created.date().isoformat(), closes)

    out = outcome_backfill.run_scan_outcome_pass(
        tmp_path,
        [{"ticker": "NVDA", "price": 101}],
        created_at=created,
        history_provider=provider,
        horizon_days=10,
        now=datetime.now(timezone.utc),
        force_backfill=True,
    )
    assert out["packets"]["skipped_duplicate"] == 1
    assert out["backfill"]["resolved"] == 1
    stored = decision_packet.load_packets(tmp_path)
    assert len(stored) == 1
    assert stored[0]["outcome"]["label"] == "win"


def test_load_scan_results_since_and_import(tmp_path) -> None:
    db_path = tmp_path / "webapp.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE scan_results (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            ticker TEXT,
            payload_json TEXT,
            created_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO scan_results (user_id, ticker, payload_json, created_at) VALUES (?, ?, ?, ?)",
        [
            ("local", "AAPL", '{"ticker": "AAPL", "price": 180, "signal_score": 70}', "2026-08-01T15:00:00+00:00"),
            ("local", "MSFT", '{"price": 410, "composite_score": 66}', "2026-09-04 14:05:00+00:00"),
            ("local", "MSFT", '{"price": 412}', "2026-09-04T18:00:00+00:00"),
            ("other", "TSLA", '{"price": 200}', "2026-09-05T15:00:00+00:00"),
        ],
    )
    conn.commit()
    conn.close()

    since = datetime(2026, 9, 3, tzinfo=timezone.utc)
    rows = decision_packet.load_local_scan_signals(db_path, since=since, user_id="local")
    assert [row["signal"]["ticker"] for row in rows] == ["MSFT", "MSFT"]
    stats = decision_packet.record_historical_scan_packets(tmp_path, rows)
    assert stats["recorded"] == 1
    assert stats["skipped_duplicate"] == 1
    stored = decision_packet.load_packets(tmp_path)
    assert stored[0]["ticker"] == "MSFT"
    assert stored[0]["entry_price"] == 410.0
    assert stored[0]["created_at"].startswith("2026-09-04")
