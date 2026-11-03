"""Remember which ET day an operator brief was already sent.

The scheduler restarts often. The morning brief used to send again on every
startup. This file is the memory that stops that. It does not place orders.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

LOG = logging.getLogger(__name__)

STATE_NAME = ".operator_notify_sent.json"
MORNING_BRIEF = "morning_brief"
WEEKLY_DIGEST = "weekly_digest"
MORNING_BRIEF_AT = (9, 25)


def state_path(skill_dir: Path | str) -> Path:
    return Path(skill_dir) / STATE_NAME


def load_sent(skill_dir: Path | str) -> dict[str, str]:
    path = state_path(skill_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("Operator notify state unreadable: %s", exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value}


def mark_sent(skill_dir: Path | str, key: str, day: str) -> None:
    path = state_path(skill_dir)
    payload = load_sent(skill_dir)
    payload[str(key)] = str(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def morning_brief_due(now: datetime, last_sent: str | None) -> bool:
    """Catch up after 9:25 ET, once per ET date."""
    if last_sent == now.date().isoformat():
        return False
    return (now.hour, now.minute) >= MORNING_BRIEF_AT


def weekly_digest_due(now: datetime, last_sent: str | None) -> bool:
    """Sunday 18:00 ET, once per ET date."""
    if last_sent == now.date().isoformat():
        return False
    return now.weekday() == 6 and (now.hour, now.minute) == (18, 0)


def send_morning_brief_if_due(
    skill_dir: Path | str,
    now: datetime,
    send: Callable[[], Any],
) -> str:
    """Send the brief when it is due. Returns sent, already_sent, or before_open."""
    day = now.date().isoformat()
    last = load_sent(skill_dir).get(MORNING_BRIEF)
    if last == day:
        return "already_sent"
    if (now.hour, now.minute) < MORNING_BRIEF_AT:
        return "before_open"
    send()
    mark_sent(skill_dir, MORNING_BRIEF, day)
    return "sent"


def send_weekly_digest_if_due(
    skill_dir: Path | str,
    now: datetime,
    send: Callable[[], Any],
) -> str:
    """Send the Sunday digest once. Returns sent, already_sent, or not_scheduled."""
    day = now.date().isoformat()
    last = load_sent(skill_dir).get(WEEKLY_DIGEST)
    if last == day:
        return "already_sent"
    if not (now.weekday() == 6 and (now.hour, now.minute) == (18, 0)):
        return "not_scheduled"
    send()
    mark_sent(skill_dir, WEEKLY_DIGEST, day)
    return "sent"
