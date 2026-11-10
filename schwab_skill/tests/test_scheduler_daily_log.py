"""A long-running scheduler rolls main_scheduler_YYYY-MM-DD logs at Chicago midnight."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scheduler_daily_log import (
    DailyChicagoLogStream,
    attach_scheduler_daily_logs,
    chicago_day,
    detach_scheduler_daily_logs,
)

CHICAGO = ZoneInfo("America/Chicago")


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_chicago_day_is_not_utc() -> None:
    # 2026-11-10 05:30 UTC is still 2026-11-09 23:30 in Chicago (CST, UTC-6).
    late = datetime(2026, 11, 10, 5, 30, tzinfo=timezone.utc)
    assert chicago_day(late) == "2026-11-09"


def test_stream_rolls_to_the_next_chicago_day(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 11, 9, 15, 1, tzinfo=CHICAGO))
    out = DailyChicagoLogStream(tmp_path, clock=clock)
    err = DailyChicagoLogStream(tmp_path, error=True, clock=clock)
    out.write("monday\n")
    err.write("monday-err\n")
    clock.now = datetime(2026, 11, 10, 0, 0, tzinfo=CHICAGO)
    out.write("tuesday\n")
    err.write("tuesday-err\n")
    out.close()
    err.close()

    assert (tmp_path / "main_scheduler_2026-11-09.log").read_text(encoding="utf-8") == "monday\n"
    assert (tmp_path / "main_scheduler_2026-11-10.log").read_text(encoding="utf-8") == "tuesday\n"
    assert (tmp_path / "main_scheduler_2026-11-09.err.log").read_text(encoding="utf-8") == "monday-err\n"
    assert (tmp_path / "main_scheduler_2026-11-10.err.log").read_text(encoding="utf-8") == "tuesday-err\n"


def test_restart_appends_to_the_same_chicago_day(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 11, 9, 15, 1, tzinfo=CHICAGO))
    first = DailyChicagoLogStream(tmp_path, clock=clock)
    first.write("from-first-process\n")
    first.close()
    second = DailyChicagoLogStream(tmp_path, clock=clock)
    second.write("from-restart\n")
    second.close()
    text = (tmp_path / "main_scheduler_2026-11-09.log").read_text(encoding="utf-8")
    assert text == "from-first-process\nfrom-restart\n"


def test_attach_captures_restart_output_and_restores_stdio(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 11, 9, 15, 1, tzinfo=CHICAGO))
    assert attach_scheduler_daily_logs(tmp_path, clock=clock) is True
    try:
        print("hello-from-restart", flush=True)
        print("err-from-restart", file=sys.stderr, flush=True)
        # A second attach must not wrap the daily stream again.
        assert attach_scheduler_daily_logs(tmp_path, clock=clock) is True
    finally:
        detach_scheduler_daily_logs()

    stdout_log = tmp_path / "main_scheduler_2026-11-09.log"
    stderr_log = tmp_path / "main_scheduler_2026-11-09.err.log"
    text = stdout_log.read_text(encoding="utf-8")
    assert "scheduler output attached" in text
    assert "hello-from-restart" in text
    assert "err-from-restart" in stderr_log.read_text(encoding="utf-8")
    print("after-detach", flush=True)
    assert stdout_log.read_text(encoding="utf-8") == text
