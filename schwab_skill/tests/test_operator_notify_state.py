"""Morning brief and weekly digest send once per ET day."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from core.operator_notify_state import (
    MORNING_BRIEF,
    STATE_NAME,
    WEEKLY_DIGEST,
    load_sent,
    morning_brief_due,
    send_morning_brief_if_due,
    send_weekly_digest_if_due,
    weekly_digest_due,
)

ET = ZoneInfo("America/New_York")


def test_morning_brief_catches_up_after_925_and_then_skips(tmp_path) -> None:
    sent: list[str] = []
    early = datetime(2026, 11, 3, 9, 24, tzinfo=ET)
    assert send_morning_brief_if_due(tmp_path, early, lambda: sent.append("early")) == "before_open"
    assert sent == []

    late = datetime(2026, 11, 3, 9, 25, tzinfo=ET)
    assert send_morning_brief_if_due(tmp_path, late, lambda: sent.append("late")) == "sent"
    assert sent == ["late"]
    assert load_sent(tmp_path)[MORNING_BRIEF] == "2026-11-03"

    restart = datetime(2026, 11, 3, 11, 5, tzinfo=ET)
    assert send_morning_brief_if_due(tmp_path, restart, lambda: sent.append("again")) == "already_sent"
    assert sent == ["late"]
    assert (tmp_path / STATE_NAME).exists()


def test_morning_brief_is_due_again_the_next_et_day(tmp_path) -> None:
    calls: list[str] = []
    first = datetime(2026, 11, 3, 16, 0, tzinfo=ET)
    nxt = datetime(2026, 11, 4, 9, 25, tzinfo=ET)
    assert morning_brief_due(first, None) is True
    send_morning_brief_if_due(tmp_path, first, lambda: calls.append("tue"))
    assert morning_brief_due(nxt, load_sent(tmp_path)[MORNING_BRIEF]) is True
    assert send_morning_brief_if_due(tmp_path, nxt, lambda: calls.append("wed")) == "sent"
    assert calls == ["tue", "wed"]


def test_weekly_digest_sends_once_on_sunday_evening(tmp_path) -> None:
    sent: list[str] = []
    sunday = datetime(2026, 11, 1, 18, 0, tzinfo=ET)
    assert sunday.weekday() == 6
    assert send_weekly_digest_if_due(tmp_path, sunday, lambda: sent.append("digest")) == "sent"
    assert load_sent(tmp_path)[WEEKLY_DIGEST] == "2026-11-01"
    assert weekly_digest_due(sunday, "2026-11-01") is False
    assert send_weekly_digest_if_due(tmp_path, sunday, lambda: sent.append("again")) == "already_sent"
    assert sent == ["digest"]
    monday = datetime(2026, 11, 2, 18, 0, tzinfo=ET)
    assert send_weekly_digest_if_due(tmp_path, monday, lambda: sent.append("monday")) == "not_scheduled"
    assert sent == ["digest"]


def test_failed_send_is_not_marked(tmp_path) -> None:
    now = datetime(2026, 11, 3, 10, 0, tzinfo=ET)

    def _boom() -> None:
        raise RuntimeError("webhook down")

    try:
        send_morning_brief_if_due(tmp_path, now, _boom)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the send error to propagate")
    assert load_sent(tmp_path) == {}
