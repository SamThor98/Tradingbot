"""Tradeable-universe Discord brief: bucket match and the 8:30 CT slot."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.tradeable_brief import (
    compose_tradeable_embed,
    is_tradeable_row,
    tick_tradeable_brief,
    tradeable_rows,
    tradeable_slot_key,
)

CHICAGO = ZoneInfo("America/Chicago")
NEW_YORK = ZoneInfo("America/New_York")


def test_tradeable_bucket_matches_dashboard_filter() -> None:
    kept = {"ticker": "AAPL", "signal_score": 80, "_filter_status": "kept", "entry_family": "stage2"}
    assert is_tradeable_row(kept) is True
    assert is_tradeable_row({**kept, "entry_family": "pead_primary"}) is False
    assert is_tradeable_row({**kept, "entry_family": "horizon"}) is False
    assert is_tradeable_row({**kept, "executable": False}) is False
    assert is_tradeable_row({**kept, "strategy_attribution": {"timeframe": "weekly"}}) is False
    assert is_tradeable_row({**kept, "scan_timeframe": "monthly"}) is False
    assert is_tradeable_row({**kept, "_filter_status": "trimmed_top_n"}) is False
    assert is_tradeable_row({**kept, "_filter_status": "filtered_quality_gates"}) is False


def test_shortlist_is_the_source_when_present() -> None:
    scan = {
        "signals": [{"ticker": "ONLYFINAL", "signal_score": 90, "_filter_status": "kept"}],
        "shortlist_signals": [
            {"ticker": "MSFT", "signal_score": 70, "_filter_status": "kept"},
            {"ticker": "DROP", "signal_score": 99, "_filter_status": "filtered_event_risk"},
        ],
    }
    assert [row["ticker"] for row in tradeable_rows(scan)] == ["MSFT"]


def test_slot_is_weekday_0830_chicago() -> None:
    assert (
        tradeable_slot_key(datetime(2026, 10, 9, 8, 30, tzinfo=CHICAGO), hour=8, minute=30, tz=CHICAGO)
        == "2026-10-09T0830"
    )
    assert (
        tradeable_slot_key(datetime(2026, 10, 9, 9, 30, tzinfo=NEW_YORK), hour=8, minute=30, tz=CHICAGO)
        == "2026-10-09T0830"
    )
    assert tradeable_slot_key(datetime(2026, 10, 9, 8, 29, tzinfo=CHICAGO), hour=8, minute=30, tz=CHICAGO) is None
    assert tradeable_slot_key(datetime(2026, 10, 10, 8, 30, tzinfo=CHICAGO), hour=8, minute=30, tz=CHICAGO) is None


def test_tick_posts_once_and_names_tradeable_rows(tmp_path: Path) -> None:
    sent: list[dict] = []

    def _remember(embed: dict) -> bool:
        sent.append(embed)
        return True

    scan = {
        "at": "2026-10-09T13:10:00+00:00",
        "diagnostics": {"data_quality": "degraded", "data_quality_reasons": ["bars_stale"]},
        "shortlist_signals": [
            {
                "ticker": "NVDA",
                "signal_score": 81,
                "_filter_status": "kept",
                "entry_family": "stage2",
                "strategy_attribution": {"passed_names": ["Stage 2 / VCP breakout"]},
            },
            {"ticker": "PEAD", "signal_score": 95, "_filter_status": "kept", "entry_family": "pead_primary"},
        ],
    }
    now = datetime(2026, 10, 9, 8, 30, tzinfo=CHICAGO)
    assert tick_tradeable_brief(
        tmp_path,
        now,
        scan_reader=lambda: scan,
        sender=_remember,
    )
    assert (
        tick_tradeable_brief(
            tmp_path,
            now,
            scan_reader=lambda: scan,
            sender=_remember,
        )
        is False
    )
    assert len(sent) == 1
    embed = sent[0]
    assert embed["title"] == "Tradeable universe"
    assert embed["footer"]["text"] == "No order sent"
    assert "data_quality=degraded" in embed["description"]
    assert "bars_stale" in embed["description"]
    blob = embed["fields"][0]["value"]
    assert "NVDA" in blob
    assert "PEAD" not in blob


def test_embed_says_when_the_scan_is_from_another_day() -> None:
    embed = compose_tradeable_embed(
        {"at": "2026-10-08T20:00:00+00:00", "signals": []},
        now=datetime(2026, 10, 9, 8, 30, tzinfo=CHICAGO),
        tz=CHICAGO,
    )
    assert "not from this morning" in embed["description"]
    assert embed["fields"][0]["value"] == "None."
