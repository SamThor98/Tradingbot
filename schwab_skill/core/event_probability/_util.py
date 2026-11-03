"""Shared helpers for the event-probability layer.

Kept local to the package so provider/normalizer code never reaches into the
private helpers of ``prediction_market.py`` (that module owns the scan-score
overlay and evolves independently).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


def clamp01(value: float) -> float:
    """Constrain a probability to ``[0, 1]``."""
    return max(0.0, min(1.0, float(value)))


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to naive datetimes.

    SQLite drops tzinfo on round-trip, so every timestamp read out of the
    repository must pass through here before it is compared against ``as_of``.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return None
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return as_utc(parsed)


def scale_up(value: float | None, *, lo: float, hi: float) -> float:
    """Map ``value`` into ``[0, 1]``, rising with the input."""
    if value is None or hi <= lo:
        return 0.0
    clipped = max(lo, min(hi, float(value)))
    return (clipped - lo) / (hi - lo)


def scale_down(value: float | None, *, lo: float, hi: float) -> float:
    """Map ``value`` into ``[0, 1]``, falling with the input."""
    if value is None or hi <= lo:
        return 1.0
    clipped = max(lo, min(hi, float(value)))
    return 1.0 - ((clipped - lo) / (hi - lo))


def http_get_json(url: str, *, timeout_sec: float) -> Any:
    """GET JSON with error codes stable enough to log and count."""
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if int(exc.code) == 429:
            raise RuntimeError("rate_limited") from exc
        raise RuntimeError(f"http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("network_error") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid_json") from exc
