"""
Logging configuration for the trading bot.

Outputs to console and a rotating trading_bot.log file (5MB limit).
Optional request_id via contextvars (set from HTTP middleware or jobs).
"""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

from webapp.redaction import redact_sensitive_text

# A numeric string is still a real number for %d / %f. Anything else stays text.
_INT_TEXT = re.compile(r"[+-]?\d+")
_FLOAT_TEXT = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")

LOG_FILE = "trading_bot.log"
MAX_BYTES = 5 * 1024 * 1024  # 5MB
BACKUP_COUNT = 3

# Default log dir: same as this module
LOG_DIR = Path(__file__).resolve().parent

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdFilter(logging.Filter):
    """Injects request_id from contextvars into LogRecord for %(request_id)s."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id_var.get()
        record.request_id = rid if rid else "-"
        return True


def _coerce_numeric_text(value: object) -> object:
    """Turn ``'64'`` into ``64`` so ``%d`` does not raise ``TypeError``.

    ``%d format: a real number is required, not str`` is what the scheduler
    logged when a count arrived as text. Non-numeric text is left alone so a
    later ``%s`` still prints it.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        return value
    text = value.strip()
    if _INT_TEXT.fullmatch(text):
        try:
            return int(text)
        except ValueError:
            return value
    if any(ch in text for ch in ".eE") and _FLOAT_TEXT.fullmatch(text):
        try:
            return float(text)
        except ValueError:
            return value
    return value


def _coerce_numeric_args(args: object) -> object:
    if isinstance(args, dict):
        return {key: _coerce_numeric_text(val) for key, val in args.items()}
    if isinstance(args, tuple):
        return tuple(_coerce_numeric_text(item) for item in args)
    return _coerce_numeric_text(args)


def _render_record_message(record: logging.LogRecord) -> str:
    """Format the record. If ``%d`` was given a numeric string, coerce and retry."""
    try:
        return record.getMessage()
    except TypeError:
        record.args = _coerce_numeric_args(record.args)
        try:
            return record.getMessage()
        except Exception:
            return f"{record.msg} args={record.args!r}"


class RedactionFilter(logging.Filter):
    """Mask token-like payloads after the log line is formatted.

    Formatting has to happen before secrets are stripped. The previous
    version turned every argument into a string first, so any ``%d`` or
    ``%f`` call (watchlist sizes, scan counts, bar counts) raised
    ``TypeError: %d format: a real number is required, not str`` and the
    line never landed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_redacted", False):
            return True
        try:
            rendered = _render_record_message(record)
            record.msg = redact_sensitive_text(rendered)
            record.args = ()
            record._redacted = True
        except Exception:
            return True
        return True


def setup_logging(
    log_dir: Path | str | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """
    Configure Python logging to console and rotating file.
    Returns the root logger.
    """
    log_path = Path(log_dir or LOG_DIR) / LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(request_id)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    req_filt = RequestIdFilter()
    redact_filt = RedactionFilter()

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    ch.addFilter(req_filt)
    ch.addFilter(redact_filt)
    root.addHandler(ch)

    # Rotating file (5MB)
    fh = RotatingFileHandler(
        log_path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    fh.addFilter(req_filt)
    fh.addFilter(redact_filt)
    root.addHandler(fh)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call setup_logging() first if needed."""
    return logging.getLogger(name)
