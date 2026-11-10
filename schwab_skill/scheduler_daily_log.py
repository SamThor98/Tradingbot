"""Daily scheduler logs for the long-running ``main.py`` process.

The Windows wrapper used to open ``logs/main_scheduler_<date>.log`` once, with
``cmd.exe`` redirection, and leave those handles in place until the process
exited. A scheduler that stayed up past midnight kept writing yesterday's
file, and a restart whose command line never applied the redirect wrote
nowhere. ``main.py`` now owns these files and reopens them when the
America/Chicago date changes. Names stay ``main_scheduler_YYYY-MM-DD.log``
and ``main_scheduler_YYYY-MM-DD.err.log``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")

_previous_stdio: tuple[TextIO, TextIO] | None = None


def _as_chicago(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=CHICAGO)
    return now.astimezone(CHICAGO)


def chicago_day(now: datetime | None = None) -> str:
    """Calendar date in America/Chicago, ``YYYY-MM-DD``."""
    current = _as_chicago(now or datetime.now(CHICAGO))
    return current.strftime("%Y-%m-%d")


def scheduler_log_path(log_dir: Path | str, day: str, *, error: bool = False) -> Path:
    """Path tools already read: ``main_scheduler_<day>.log`` or ``.err.log``."""
    name = f"main_scheduler_{day}.err.log" if error else f"main_scheduler_{day}.log"
    return Path(log_dir) / name


class DailyChicagoLogStream:
    """Append-only text stream that rolls to a new dated file at Chicago midnight."""

    def __init__(
        self,
        log_dir: Path | str,
        *,
        error: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._log_dir = Path(log_dir)
        self._error = error
        self._clock = clock or (lambda: datetime.now(CHICAGO))
        self._day: str | None = None
        self._fh: TextIO | None = None

    def write(self, data: str) -> int:
        text = "" if data is None else str(data)
        if text == "":
            self._ensure()
            return 0
        self._ensure()
        assert self._fh is not None
        self._fh.write(text)
        if text.endswith("\n"):
            self._fh.flush()
        return len(text)

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush()

    def close(self) -> None:
        if self._fh is None:
            return
        self._fh.flush()
        self._fh.close()
        self._fh = None
        self._day = None

    def isatty(self) -> bool:
        return False

    def _ensure(self) -> None:
        day = chicago_day(self._clock())
        if self._fh is not None and day == self._day:
            return
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None
        path = scheduler_log_path(self._log_dir, day, error=self._error)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8", buffering=1)
        self._day = day


def attach_scheduler_daily_logs(
    log_dir: Path | str,
    *,
    clock: Callable[[], datetime] | None = None,
) -> bool:
    """Send this process's stdout/stderr to today's scheduler logs.

    Returns False when the files cannot be opened, leaving stdio unchanged so
    the scheduler still runs. A second call is a no-op.
    """
    global _previous_stdio
    if _previous_stdio is not None:
        return True
    out: DailyChicagoLogStream | None = None
    err: DailyChicagoLogStream | None = None
    try:
        out = DailyChicagoLogStream(log_dir, clock=clock)
        err = DailyChicagoLogStream(log_dir, error=True, clock=clock)
        out.write("")
        err.write("")
    except Exception as exc:
        try:
            sys.stderr.write(f"scheduler daily log attach failed: {exc}\n")
        except Exception:
            pass
        for stream in (out, err):
            if stream is not None:
                stream.close()
        return False
    assert out is not None and err is not None
    _previous_stdio = (sys.stdout, sys.stderr)
    sys.stdout = out  # type: ignore[assignment]
    sys.stderr = err  # type: ignore[assignment]
    stamp = _as_chicago((clock or (lambda: datetime.now(CHICAGO)))())
    sys.stdout.write(f"{stamp.strftime('%Y-%m-%d %H:%M:%S %Z')} scheduler output attached\n")
    sys.stdout.flush()
    return True


def detach_scheduler_daily_logs() -> None:
    """Restore the stdio streams captured by :func:`attach_scheduler_daily_logs`."""
    global _previous_stdio
    if _previous_stdio is None:
        return
    current_out, current_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _previous_stdio
    _previous_stdio = None
    for stream in (current_out, current_err):
        close = getattr(stream, "close", None)
        if callable(close):
            close()
