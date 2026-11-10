"""Watchlist fetch logs must survive a string count.

The Nov 9 scheduler logged ``%d format: a real number is required, not str``
while fetching the watchlist. Two things produced that: log lines used ``%d``,
and the redaction filter turned every argument into a string before formatting.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import watchlist_loader as wl
from logger_setup import RedactionFilter

_WATCHLIST_PERCENT_D = (
    ("Fetched %d tickers from %s", ("64", "S&P 500"), "Fetched 64 tickers from S&P 500"),
    ("Loaded %d tickers for universe %s", ("503", "sp500"), "Loaded 503 tickers for universe sp500"),
    ("Using cached %s universe (%d tickers)", ("sp500", "2"), "Using cached sp500 universe (2 tickers)"),
    ("Using cached watchlist (%d tickers)", ("1500",), "Using cached watchlist (1500 tickers)"),
    (
        "Universe %s fetch empty; using fallback (%d names)",
        ("sp400", "18"),
        "Universe sp400 fetch empty; using fallback (18 names)",
    ),
    (
        "Loaded %d tickers (%d index fetches succeeded)",
        ("1500", "3"),
        "Loaded 1500 tickers (3 index fetches succeeded)",
    ),
    (
        "Watchlist quality prefilter applied: %d -> %d tickers",
        ("1500", "800"),
        "Watchlist quality prefilter applied: 1500 -> 800 tickers",
    ),
    (
        "Universe focus applied: %d -> %d tickers (mode=%s)",
        ("1500", "250", "focused"),
        "Universe focus applied: 1500 -> 250 tickers (mode=focused)",
    ),
    (
        "Watchlist mode=universe_%s tickers=%d",
        ("sp500", "503"),
        "Watchlist mode=universe_sp500 tickers=503",
    ),
)


def test_percent_d_with_a_string_count_raises() -> None:
    """The failure Sam's log showed, before the filter formats the line."""
    record = logging.LogRecord(
        "watchlist_loader",
        logging.INFO,
        __file__,
        1,
        "Fetched %d tickers from %s",
        ("64", "S&P 500"),
        None,
    )
    try:
        record.getMessage()
    except TypeError as exc:
        assert "real number is required" in str(exc)
    else:
        raise AssertionError("string %d count should raise TypeError")


def test_redaction_filter_formats_watchlist_percent_d_strings() -> None:
    filt = RedactionFilter()
    for msg, args, expected in _WATCHLIST_PERCENT_D:
        record = logging.LogRecord("watchlist_loader", logging.INFO, __file__, 1, msg, args, None)
        assert filt.filter(record) is True
        assert record.getMessage() == expected


def test_redaction_filter_accepts_numeric_string_for_percent_f() -> None:
    """The same string-count failure hits ``%.0f`` lines outside the watchlist path."""
    record = logging.LogRecord(
        "engine_analysis",
        logging.WARNING,
        __file__,
        1,
        "pausing LLM calls for %.0fs",
        ("12.0",),
        None,
    )
    assert RedactionFilter().filter(record) is True
    assert record.getMessage() == "pausing LLM calls for 12s"


def test_redaction_filter_still_masks_secrets_and_keeps_int_counts() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("test.watchlist_redaction")
    log.handlers.clear()
    log.propagate = False
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        log.info("Fetched %d tickers from %s", 64, "S&P 500")
        log.info("auth access_token=%s", "supersecretvalue")
    finally:
        log.removeHandler(handler)
        handler.close()
    text = stream.getvalue()
    assert "Fetched 64 tickers from S&P 500" in text
    assert "supersecretvalue" not in text
    assert "access_token=[REDACTED]" in text


def test_watchlist_fetch_path_logs_under_redaction_filter(tmp_path: Path, capsys, monkeypatch) -> None:
    """The fetch helpers themselves must not raise or drop the count line."""
    monkeypatch.setattr(wl, "SKILL_DIR", tmp_path)
    monkeypatch.setattr(wl, "CACHE_FILE", tmp_path / ".watchlist_cache.json")
    monkeypatch.setattr(wl, "_fetch_sp500", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr(wl, "_fetch_sp400", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr(wl, "_fetch_sp600", lambda: ["JPM"])
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactionFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    previous_handlers = list(wl.LOG.handlers)
    previous_propagate = wl.LOG.propagate
    previous_level = wl.LOG.level
    wl.LOG.handlers = [handler]
    wl.LOG.propagate = False
    wl.LOG.setLevel(logging.DEBUG)
    try:
        loaded = wl._load_named_fetch("sp500", lambda: ["AAPL", "MSFT"], fallback=["X"])
        cached = wl._load_named_fetch("sp500", lambda: ["NOPE"], fallback=["X"])
        empty = wl._load_named_fetch("emptyu", lambda: [], fallback=["AAPL", "MSFT"])
        full = wl.load_full_watchlist(force_refresh=True)
    finally:
        wl.LOG.handlers = previous_handlers
        wl.LOG.propagate = previous_propagate
        wl.LOG.setLevel(previous_level)
        handler.close()

    assert loaded == ["AAPL", "MSFT"]
    assert cached == ["AAPL", "MSFT"]
    assert empty == ["AAPL", "MSFT"]
    text = stream.getvalue()
    assert "Loaded 2 tickers for universe sp500" in text
    assert "Using cached sp500 universe (2 tickers)" in text
    assert "Universe emptyu fetch empty; using fallback (2 names)" in text
    assert "Fetched 2 tickers from S&P 500" in text
    assert "Fetched 1 tickers from S&P 600" in text
    assert "Loaded 3 tickers (3 index fetches succeeded)" in text
    assert full == ["AAPL", "MSFT", "JPM"]
    captured = capsys.readouterr()
    assert "real number is required" not in captured.err
    assert "Logging error" not in captured.err
