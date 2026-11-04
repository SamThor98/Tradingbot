"""Scan and hold-reminder logs must accept a numeric string count."""

from __future__ import annotations

import logging

from main import _as_log_count


def test_as_log_count_coerces_numeric_string() -> None:
    assert _as_log_count("2") == 2
    assert _as_log_count(2) == 2
    assert _as_log_count(" 0 ") == 0
    assert _as_log_count("nope") == "nope"


def test_signal_scan_log_accepts_string_count(caplog) -> None:
    caplog.set_level(logging.INFO)
    log = logging.getLogger("test.scan_log_count")
    n = "2"
    log.info("Signal scan: %s signals found, Discord notifications sent.", _as_log_count(n))
    log.info("Hold reminder: %s alerts sent.", _as_log_count(n))
    assert "Signal scan: 2 signals found, Discord notifications sent." in caplog.text
    assert "Hold reminder: 2 alerts sent." in caplog.text


def test_percent_d_message_rejects_string_count() -> None:
    bad = logging.LogRecord(
        "t",
        logging.INFO,
        __file__,
        1,
        "Signal scan: %d signals found, Discord notifications sent.",
        ("2",),
        None,
    )
    try:
        bad.getMessage()
    except TypeError:
        raised = True
    else:
        raised = False
    assert raised
    good = logging.LogRecord(
        "t",
        logging.INFO,
        __file__,
        1,
        "Signal scan: %s signals found, Discord notifications sent.",
        (_as_log_count("2"),),
        None,
    )
    assert good.getMessage() == "Signal scan: 2 signals found, Discord notifications sent."
