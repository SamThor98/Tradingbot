"""
Shared low-level I/O and concurrency primitives.

This module exists so cache files (`.sec_cache.json`, `.forensic_cache.json`,
`.sector_map_cache.json`, `.signal_quality_metrics.json`, etc.) can be written
atomically — preventing torn JSON on concurrent writers (Celery worker + main
scan + dashboard read) — and so all yfinance calls share a single global lock
to avoid the silent data corruption yfinance exhibits when its internal
``_session`` is hammered by multiple threads.

Both helpers are deliberately small and dependency-free so any module can
import them without pulling in heavy dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

LOG = logging.getLogger(__name__)

# yfinance shares a module-level requests session and is not thread-safe.
# Concurrent calls (parallel Stage A/B workers, sector + forensic enrichment,
# news + earnings lookups) periodically corrupt responses or raise opaque
# JSONDecodeErrors. A single process-wide lock costs us serial yfinance
# throughput but guarantees deterministic enrichment.
yfinance_lock = threading.Lock()

T = TypeVar("T")


def _quiet_noisy_yfinance_logging() -> None:
    """Silence yfinance's own logger.

    On transient failures (Yahoo rate-limits / 400 "Bad Request" HTML pages,
    404 quoteSummary for option symbols, etc.) yfinance logs the *entire* HTTP
    error body at ERROR level, which floods worker/web logs with multi-line HTML.
    We already translate every yfinance outcome into an explicit reason code and
    fall back to Schwab (or empty data), so this output carries no actionable
    signal. Raise the library logger above ERROR and stop propagation; our own
    module loggers (logger ``__name__``) are unaffected.
    """
    for name in ("yfinance", "yfinance.ticker", "yfinance.data", "yfinance.scraper"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.CRITICAL)
        lg.propagate = False


_quiet_noisy_yfinance_logging()


def _atomic_replace(src: Path, dst: Path, *, max_attempts: int = 5) -> None:
    """``os.replace`` with short backoff for Windows / sync-folder file locks."""
    last_exc: OSError | None = None
    for attempt in range(max_attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt + 1 >= max_attempts:
                break
            time.sleep(0.05 * (2**attempt))
    if last_exc is not None:
        raise last_exc
    os.replace(src, dst)


def atomic_write_json(
    path: Path | str,
    data: Any,
    *,
    indent: int | None = 2,
    encoding: str = "utf-8",
) -> None:
    """Serialize ``data`` to JSON and replace ``path`` atomically.

    Writes to a temp file in the destination directory, then ``os.replace``s
    it onto ``path``. On POSIX this is atomic; on Windows ``os.replace``
    overwrites the target in one step (atomic at the filesystem layer for
    same-volume moves). Either way readers never observe a half-written file.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            json.dump(data, fh, indent=indent)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        _atomic_replace(tmp_path, target)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(
    path: Path | str,
    data: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Plain-text counterpart to ``atomic_write_json``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        _atomic_replace(tmp_path, target)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


@contextmanager
def yfinance_call() -> Iterator[None]:
    """Context manager that serializes yfinance API access.

    Usage::

        with yfinance_call():
            df = yf.Ticker(symbol).history(...)

    Hold the lock for the **entire** yfinance interaction (object construction
    plus property access plus method calls) — yfinance lazily caches data on
    the Ticker object, so the corruption window includes attribute reads, not
    just the initial fetch.
    """
    with yfinance_lock:
        yield


def yf_invoke(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Convenience wrapper: ``yf_invoke(callable, *args)`` under the lock."""
    with yfinance_lock:
        return func(*args, **kwargs)
