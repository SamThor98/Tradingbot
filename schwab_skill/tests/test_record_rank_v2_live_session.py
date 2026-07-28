"""Tests for rank-v2 live session recorder."""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from scripts.record_rank_v2_live_session import build_session_row  # noqa: E402


def test_build_session_row_qualifies_p76() -> None:
    row = build_session_row(
        {
            "rank_filter_v2_mode": "live",
            "rank_filter_v2_min_percentile": 76,
            "rank_filter_v2_evaluated": 24,
            "rank_filter_v2_dropped": 18,
            "rank_filter_v2_would_drop": 18,
            "rank_filter_v2_threshold": 30.1,
            "data_quality": "ok",
            "watchlist_size": 1505,
        },
        label="t1",
        scan_at="2026-07-22T12:00:00Z",
        signals_found=6,
    )
    assert row["rank_filter_v2"]["retention_pct"] == 25.0
    assert row["rank_filter_v2"]["retention_in_guidance_band"] is True
    assert row["qualifies_post_p76_session"] is True


def test_build_session_row_rejects_wrong_percentile() -> None:
    row = build_session_row(
        {
            "rank_filter_v2_mode": "live",
            "rank_filter_v2_min_percentile": 75,
            "rank_filter_v2_evaluated": 24,
            "rank_filter_v2_dropped": 18,
            "data_quality": "ok",
        },
        label="t2",
        scan_at=None,
        signals_found=1,
    )
    assert row["qualifies_post_p76_session"] is False
