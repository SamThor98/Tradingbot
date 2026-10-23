"""Read-only Discord scan commands: ranking, pick outcomes, and levels."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from discord_operator_reads import (
    ADVISORY_STOP_PCT,
    render_levels,
    render_picks,
    render_top,
)

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)
SCAN_AT = "2026-10-23T18:30:00+00:00"  # 2:30p ET same day


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "webapp.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE app_state (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            key TEXT,
            value_json TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE scan_results (
            id INTEGER PRIMARY KEY,
            user_id TEXT,
            job_id TEXT,
            ticker TEXT,
            signal_score REAL,
            payload_json TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return path


def _save_scan(path: Path, blob: dict, *, user_id: str = "local", updated_at: str = SCAN_AT) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO app_state (user_id, key, value_json, updated_at) VALUES (?, 'last_scan', ?, ?)",
        (user_id, json.dumps(blob), updated_at),
    )
    conn.commit()
    conn.close()


def _save_result(
    path: Path,
    ticker: str,
    payload: dict,
    created_at: str,
    *,
    user_id: str = "local",
) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO scan_results (user_id, job_id, ticker, signal_score, payload_json, created_at)
        VALUES (?, 'job', ?, 1, ?, ?)
        """,
        (user_id, ticker, json.dumps(payload), created_at),
    )
    conn.commit()
    conn.close()


def _signal(ticker: str, price: float, sma: float, **extra: object) -> dict:
    row = {
        "ticker": ticker,
        "price": price,
        "sma_50": sma,
        "entry_family": "stage2",
        "strategy_plugins": [{"name": "trend_breakout", "triggered": True}],
    }
    row.update(extra)
    return row


def test_top_ranks_filters_and_week_setup(tmp_path: Path) -> None:
    path = _db(tmp_path)
    blob = {
        "at": SCAN_AT,
        "signals": [
            _signal("ZZZ", 110, 100),  # +10% extended
            _signal("AAA", 101, 100),  # +1%
            _signal("BBB", 108, 100),  # exactly 8% kept
            _signal("CCC", 108.2, 100),  # just over 8%
            _signal("DIS", 103, 100, forensic_flags=["altman_distress"]),
            _signal("BUY", 102, 100, buyout_target=True),
            _signal("LOW", 90, 100, forensic_altman=1.1),  # z-score alone is not a flag
            {"ticker": "GAP"},
            _signal(
                "WS",
                104,
                100,
                entry_family="horizon",
                strategy_plugins=[{"name": "weekly_swing", "triggered": True}],
            ),
        ],
        "diagnostics": {"known_buyout_targets": ["MARK"]},
    }
    blob["signals"].append(_signal("MARK", 105, 100))
    _save_scan(path, blob)
    _save_result(
        path,
        "AAA",
        {
            "ticker": "AAA",
            "strategy_plugins": [{"name": "weekly_swing", "triggered": True}],
        },
        "2026-10-19T15:00:00+00:00",  # Monday of that week
    )
    _save_result(
        path,
        "WS",
        {
            "ticker": "WS",
            "strategy_plugins": [{"name": "trend_breakout", "triggered": True}],
        },
        "2026-10-12T15:00:00+00:00",  # previous week, must not flip WS to both
    )

    text = render_top(path, n=10, now=NOW)

    assert "Stale scan" not in text
    assert "ZZZ" not in text
    assert "CCC" not in text
    assert "DIS" not in text
    assert "BUY" not in text
    assert "MARK" not in text
    assert "GAP" not in text
    assert "LOW" in text
    assert "AAA" in text and "both" in text
    assert "BBB" in text
    assert "WS" in text
    # Tightest % vs 50d first: LOW is below the average, then AAA.
    body = text.split("```")[1]
    tickers = [line.split()[1] for line in body.splitlines() if line[:1].isdigit() or line.startswith(" ")]
    assert tickers[0] == "LOW"
    assert tickers.index("AAA") < tickers.index("BBB")
    assert "83.70" in text  # 7% stop on LOW at $90
    assert "Altman-distress" in text
    assert "buyout" in text
    assert ">8% above 50d" in text
    assert len(text) < 2000
    for line in body.splitlines():
        assert len(line) <= 42


def test_top_uses_explicit_stop_and_stale_banner(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _save_scan(
        path,
        {
            "at": "2026-10-22T20:00:00+00:00",
            "signals": [_signal("AAPL", 100, 99, stop=91.5)],
        },
    )
    text = render_top(path, n=10, now=NOW)
    assert text.startswith("**Stale scan**")
    assert "91.50" in text
    assert f"{100 * (1 - ADVISORY_STOP_PCT):.2f}" not in text.split("```")[1]


def test_top_missing_database_and_empty_filters(tmp_path: Path) -> None:
    missing = render_top(tmp_path / "missing.db", now=NOW)
    assert "No dashboard database" in missing

    path = _db(tmp_path)
    _save_scan(path, {"at": SCAN_AT, "signals": [_signal("ZZZ", 120, 100)]})
    text = render_top(path, n=10, now=NOW)
    assert "No names passed" in text
    assert "ZZZ" not in text.split("passed")[-1]


def _ohlcv(closes: list[float], start: str = "2026-01-05") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(closes))
    close = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )


def test_picks_excess_uses_prior_close_and_spy(tmp_path: Path) -> None:
    path = _db(tmp_path)
    dates = pd.bdate_range("2026-06-01", periods=40)
    scan_day = dates[20].date()
    entry = 100.0 + 19  # close on dates[19], the prior session
    exit_5 = 100.0 + 24  # dates[24] is 5 sessions after the entry bar
    stock = _ohlcv([100.0 + i for i in range(40)], start="2026-06-01")
    spy = _ohlcv([200.0] * 40, start="2026-06-01")
    created = datetime(scan_day.year, scan_day.month, scan_day.day, 16, 0, tzinfo=timezone.utc).isoformat()
    _save_result(path, "AAA", {"ticker": "AAA", "price": 999}, created)

    def loader(ticker: str) -> pd.DataFrame:
        return spy if ticker == "SPY" else stock

    text = render_picks(path, days=30, now=datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc), history_loader=loader)
    expected_5 = (exit_5 / entry) - 1.0
    assert "entry = prior close" in text
    assert "5d" in text
    assert f"{expected_5 * 100:+.2f}%" in text
    assert "100%" in text  # beat SPY, which was flat
    # Must not use the scan payload price of 999 as the entry.
    assert "999" not in text


def test_picks_stretch_terciles(tmp_path: Path) -> None:
    path = _db(tmp_path)
    dates = pd.bdate_range("2026-01-05", periods=80)
    scan_day = dates[50].date()
    created = datetime(scan_day.year, scan_day.month, scan_day.day, 16, 0, tzinfo=timezone.utc).isoformat()
    entries = [
        ("L1", 101.0, 1.05),
        ("L2", 102.0, 1.05),
        ("M1", 110.0, 1.0),
        ("M2", 112.0, 1.0),
        ("H1", 140.0, 0.95),
        ("H2", 150.0, 0.95),
    ]
    frames: dict[str, pd.DataFrame] = {}
    for ticker, entry, factor in entries:
        closes = [100.0] * 50
        closes.append(entry)  # index 50 is dates[50], but entry must be dates[49]
        # Rebuild: index 49 is the prior close (entry), scan day is dates[50].
        closes = [100.0] * 49 + [entry]
        # 5 sessions after entry -> index 54
        while len(closes) < 55:
            closes.append(entry)
        closes[54] = entry * factor
        while len(closes) < 80:
            closes.append(closes[-1])
        frames[ticker] = _ohlcv(closes, start="2026-01-05")
        _save_result(path, ticker, {"ticker": ticker}, created)
    spy = _ohlcv([100.0] * 80, start="2026-01-05")

    def loader(ticker: str) -> pd.DataFrame:
        return spy if ticker == "SPY" else frames[ticker]

    text = render_picks(path, days=20, now=datetime(2026, 4, 1, 16, 0, tzinfo=timezone.utc), history_loader=loader)
    assert "Stretch terciles" in text
    assert "low" in text and "high" in text
    assert "+5.00%" in text
    assert "-5.00%" in text


def test_picks_missing_history_message(tmp_path: Path) -> None:
    path = _db(tmp_path)
    text = render_picks(path, days=10, now=NOW, history_loader=lambda _t: pd.DataFrame())
    assert "No scan picks" in text

    _save_result(path, "AAA", {"ticker": "AAA"}, SCAN_AT)
    empty = render_picks(
        path,
        days=10,
        now=NOW,
        history_loader=lambda ticker: pd.DataFrame() if ticker != "SPY" else _ohlcv([100.0] * 30),
    )
    assert "none have a prior close" in empty or "Skipped" in empty


def test_levels_quote_sma_atr_and_scan_stop(tmp_path: Path) -> None:
    path = _db(tmp_path)
    closes = [100.0 + i * 0.1 for i in range(220)]
    frame = _ohlcv(closes, start="2025-01-02")
    _save_scan(
        path,
        {
            "at": SCAN_AT,
            "signals": [_signal("AAPL", 100, 98, stop_price=91.25)],
        },
    )

    def history(_ticker: str) -> pd.DataFrame:
        return frame

    def quote(_ticker: str) -> dict:
        return {"lastPrice": 130.0}

    view = render_levels("aapl", path, now=NOW, history_loader=history, quote_loader=quote)
    assert view.embed is not None
    blob = json.dumps(view.embed)
    assert "AAPL" in blob
    assert "130.00" in blob
    assert "91.25" in blob
    assert "SMA 50" in blob
    assert "SMA 200" in blob
    assert "ATR 14" in blob
    assert "%" in view.embed["description"]
    assert len(view.text) < 2000


def test_levels_without_scan_row_and_short_history(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _save_scan(path, {"at": SCAN_AT, "signals": []})
    frame = _ohlcv([50.0 + i for i in range(30)], start="2026-09-01")
    view = render_levels(
        "MSFT",
        path,
        now=NOW,
        history_loader=lambda _t: frame,
        quote_loader=lambda _t: None,
    )
    assert view.embed is not None
    stop = next(field for field in view.embed["fields"] if field["name"] == "Stop")
    assert stop["value"] == "Not in the latest scan."
    assert "Need 200 bars" in json.dumps(view.embed)

    short = render_levels(
        "MSFT",
        path,
        now=NOW,
        history_loader=lambda _t: _ohlcv([10, 11, 12]),
        quote_loader=lambda _t: {"lastPrice": 12},
    )
    assert short.embed is None
    assert "not enough daily bars" in short.text


def test_levels_stale_bar_note(tmp_path: Path) -> None:
    path = _db(tmp_path)
    frame = _ohlcv([20.0 + i * 0.05 for i in range(80)], start="2026-01-05")
    view = render_levels(
        "IBM",
        path,
        now=NOW,
        history_loader=lambda _t: frame,
        quote_loader=lambda _t: {"lastPrice": 40.0},
    )
    assert view.embed is not None
    assert "stale" in view.embed["description"].lower()


def test_commands_are_read_only_and_scan_omits_strategy_ids() -> None:
    root = Path(__file__).resolve().parents[1]
    reads = (root / "discord_operator_reads.py").read_text(encoding="utf-8")
    for banned in (
        "get_account_token",
        "place_order",
        "trader/v1",
        "/accounts",
        "approve_order",
        "import execution",
    ):
        assert banned not in reads

    bot = (root / "discord_confirm.py").read_text(encoding="utf-8")
    assert 'name="top"' in bot
    assert 'name="picks"' in bot
    assert 'name="levels"' in bot
    assert 'name="help"' in bot
    assert "/top" in bot and "/picks" in bot and "/levels" in bot
    start = bot.index('name="top"')
    end = bot.index('name="scan"', start)
    block = bot[start:end]
    for banned in ("place_order", "get_account_token", "execution", "approve", "reject"):
        assert banned not in block

    import re

    calls = re.findall(r"run_scan_and_notify\([^)]*\)", bot)
    assert len(calls) == 2
    for call in calls:
        assert call == "run_scan_and_notify(skill_dir=SKILL_DIR, send_summary=False)"
        assert "strategy_ids" not in call
        assert "weekly_swing" not in call
