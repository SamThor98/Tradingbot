"""Tests for Finnhub earnings history calendar+stock merge."""

from __future__ import annotations

from finnhub_data import _merge_earnings_history_rows


def test_merge_prefers_calendar_announcement_date() -> None:
    calendar = [
        {
            "date": "2024-01-25",
            "actual_eps": 2.18,
            "estimate_eps": 2.10,
            "year": 2024,
            "quarter": 1,
            "source": "calendar/earnings",
        }
    ]
    stock = [
        {
            "date": "2023-12-31",
            "actual_eps": 2.18,
            "estimate_eps": 2.10,
            "year": 2024,
            "quarter": 1,
            "source": "stock/earnings",
        },
        {
            "date": "2023-09-30",
            "actual_eps": 1.46,
            "estimate_eps": 1.39,
            "year": 2023,
            "quarter": 4,
            "source": "stock/earnings",
        },
    ]
    merged = _merge_earnings_history_rows(calendar, stock)
    assert len(merged) == 2
    q1 = next(r for r in merged if r.get("year") == 2024 and r.get("quarter") == 1)
    assert q1["date"] == "2024-01-25"
    assert q1["source"] == "calendar/earnings"
