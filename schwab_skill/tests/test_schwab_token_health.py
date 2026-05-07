"""
Regression tests for the refresh-token age health helper.

Covers `compute_token_health` — the pure function the dashboard uses to drive
the "Schwab tokens expire in N days" chip. Tests are stateless (no I/O, no
filesystem) so they're safe to run in any order.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import schwab_auth as sa


def _now() -> datetime:
    return datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)


def test_no_tokens_reports_expired() -> None:
    out = sa.compute_token_health(None, now=_now())
    assert out["status"] == "expired"
    assert out["has_tokens"] is False
    assert out["last_refresh_at"] is None


def test_tokens_without_marker_are_unknown() -> None:
    """Legacy token files (from before the _last_refresh_at rollout) must
    not be reported as expired — that would flag a healthy session as bad."""
    tokens = {"access_token": "x" * 76, "refresh_token": "y" * 140}
    out = sa.compute_token_health(tokens, now=_now())
    assert out["status"] == "unknown"
    assert out["has_tokens"] is True
    assert out["last_refresh_at"] is None
    assert out["hours_until_expiry"] is None


def test_fresh_token_is_healthy() -> None:
    refreshed = _now() - timedelta(hours=2)
    tokens = {
        "access_token": "x" * 76,
        "refresh_token": "y" * 140,
        "_last_refresh_at": refreshed.isoformat(),
    }
    out = sa.compute_token_health(tokens, now=_now())
    assert out["status"] == "healthy"
    assert out["refresh_token_age_hours"] == 2.0
    # 7 days TTL minus 2 hours used = ~166 hours remaining
    assert 165.0 <= out["hours_until_expiry"] <= 167.0


def test_token_in_warn_window() -> None:
    """5 days old → ~2 days remaining → warn band (< 2 days remaining)."""
    refreshed = _now() - timedelta(days=5, hours=1)
    tokens = {
        "access_token": "x" * 76,
        "refresh_token": "y" * 140,
        "_last_refresh_at": refreshed.isoformat(),
    }
    out = sa.compute_token_health(tokens, now=_now())
    assert out["status"] == "warn"


def test_token_in_critical_window() -> None:
    """6.7 days old → < 12h remaining → critical."""
    refreshed = _now() - timedelta(days=6, hours=15)
    tokens = {
        "access_token": "x" * 76,
        "refresh_token": "y" * 140,
        "_last_refresh_at": refreshed.isoformat(),
    }
    out = sa.compute_token_health(tokens, now=_now())
    assert out["status"] == "critical"
    assert out["hours_until_expiry"] < 12


def test_token_past_ttl_is_expired() -> None:
    refreshed = _now() - timedelta(days=8)
    tokens = {
        "access_token": "x" * 76,
        "refresh_token": "y" * 140,
        "_last_refresh_at": refreshed.isoformat(),
    }
    out = sa.compute_token_health(tokens, now=_now())
    assert out["status"] == "expired"
    assert out["hours_until_expiry"] < 0


def test_malformed_marker_is_unknown_not_crash() -> None:
    """A garbled timestamp must not crash the dashboard polling loop."""
    tokens = {
        "access_token": "x" * 76,
        "refresh_token": "y" * 140,
        "_last_refresh_at": "not-an-iso-timestamp",
    }
    out = sa.compute_token_health(tokens, now=_now())
    assert out["status"] == "unknown"


def test_stamp_refresh_at_records_iso_utc() -> None:
    """`_stamp_refresh_at` must produce a tz-aware ISO timestamp the parser
    accepts back."""
    stamped = sa._stamp_refresh_at({"access_token": "a", "refresh_token": "b"})
    raw = stamped["_last_refresh_at"]
    assert isinstance(raw, str)
    parsed = sa._parse_refresh_at(stamped)
    assert parsed is not None
    assert parsed.tzinfo is not None  # offset-aware
