from __future__ import annotations

import logging

import _io_utils  # noqa: F401  (import applies the logger silencing at module load)


def test_yfinance_logger_silenced_on_import() -> None:
    lg = logging.getLogger("yfinance")
    assert lg.level == logging.CRITICAL
    assert lg.propagate is False


def test_yfinance_error_record_is_suppressed() -> None:
    lg = logging.getLogger("yfinance")
    # An ERROR-level record (yfinance dumps HTTP error bodies here) must not pass.
    # (We assert via the logger's own level, not isEnabledFor, because a global
    # logging.disable() in some test/CI setups would otherwise mask the check.)
    assert lg.level == logging.CRITICAL
    assert logging.ERROR < lg.level


def test_reapply_is_idempotent() -> None:
    _io_utils._quiet_noisy_yfinance_logging()
    _io_utils._quiet_noisy_yfinance_logging()
    assert logging.getLogger("yfinance.data").level == logging.CRITICAL
