"""Unit tests for the shared local/SaaS decision-card payload."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from webapp.decision_card_service import (
    build_decision_card_payload,
    coerce_scan_signal,
    find_signal_in_scan_collections,
)


@pytest.fixture(autouse=True)
def _disable_sharadar_network(monkeypatch) -> None:
    monkeypatch.setattr(
        "sharadar_data.get_sharadar_research_snapshot",
        lambda *args, **kwargs: {
            "ok": False,
            "enabled": False,
            "status": "disabled",
            "errors": ["missing_sharadar_api_key"],
            "data_quality": "missing_sharadar_api_key",
            "daily": {},
            "fundamentals": {},
            "holders": [],
            "insiders": [],
            "actions": [],
            "events": [],
            "pit_fields": {},
        },
    )


def test_decision_card_payload_includes_brief_and_rank_v2(monkeypatch) -> None:
    monkeypatch.setattr(
        "execution.get_position_size_usd",
        lambda **_kwargs: 10_000.0,
    )
    signal = {
        "ticker": "AAPL",
        "price": 100.0,
        "composite_score": 82.5,
        "signal_score": 70.0,
        "rank_score_v2": 65.0,
        "rank_score": 60.0,
        "reliability_score": 71.2,
        "advisory": {"confidence_bucket": "high", "p_up_10d": 0.61},
        "strategy_attribution": {"top_live": "stage2_vcp"},
        "sec_risk_tag": "low",
        "_data_quality": "degraded",
    }
    checklist: dict[str, Any] = {"block_reasons": [], "block_reasons_plain": []}
    out = build_decision_card_payload(
        signal,
        ticker="aapl",
        skill_dir=Path("."),
        checklist=checklist,
        size_usd=10_000.0,
    )
    assert out["ticker"] == "AAPL"
    assert out["side"] == "BUY"
    assert out["entry_price"] == 100.0
    assert out["data_quality"] == "degraded"
    assert out["stop_invalidation"] == 93.0
    assert out["size"]["qty"] == 100
    assert out["confidence"]["rank_score_v2"] == 65.0
    assert out["confidence"]["signal_score"] == 82.5
    assert "brief" in out
    assert "AAPL remains a breakout candidate" in out["brief"]["setup_summary"]
    assert out["entry_zone"]["low"] == 99.5
    ideas = out["brief"]["entry_stop_ideas"]
    assert any("7% under price, 200-day missing" in x for x in ideas)
    assert not any("Entry zone" in x for x in ideas)
    assert not any("Advisory 7%" in x for x in ideas)


def test_decision_card_brief_sections_always_populated(monkeypatch) -> None:
    monkeypatch.setattr("execution.get_position_size_usd", lambda **_kwargs: 5_000.0)
    signal = {
        "ticker": "MSFT",
        "price": 250.0,
        "composite_score": 64.0,
        "advisory": {"confidence_bucket": "medium", "p_up_10d": 0.55},
        "strategy_attribution": {"top_live": "trend_breakout"},
        "sec_risk_tag": "unknown",
        "volume_ratio": 1.35,
        "sector_etf": "XLK",
        "sma_50": 240.0,
        "sma_200": 210.0,
        "breakout_confirmed": True,
        "pead_surprise_pct": 0.08,
        "pead_beat": True,
        "forensic_sloan": 0.04,
        "filing_recency_days": 12,
        "entry_timing_shadow": {"breakout_buffer_pct": 0.021},
        "mirofish_conviction": 28.0,
        "_filter_status": "kept",
    }
    out = build_decision_card_payload(
        signal,
        ticker="MSFT",
        skill_dir=Path("."),
        checklist={"block_reasons": [], "block_reasons_plain": []},
        size_usd=5_000.0,
    )
    brief = out["brief"]
    for key in (
        "setup_summary",
        "key_risks",
        "catalyst_notes",
        "forensic_flags",
        "sec_notes",
        "expected_move_window",
        "entry_stop_ideas",
    ):
        assert brief.get(key), f"brief.{key} should have content"
    assert "No hard risk blockers" in brief["key_risks"][0]
    assert any("earnings surprise" in x.lower() for x in brief["catalyst_notes"])
    assert any("8.0%" in x for x in brief["catalyst_notes"])
    assert any("XLK" in x for x in brief["catalyst_notes"])
    assert any("Sloan" in x for x in brief["forensic_flags"])
    assert any("filing context age" in x for x in brief["sec_notes"])
    assert "P(up)" in brief["expected_move_window"]
    assert "EV" not in brief["expected_move_window"]
    assert "ev_10d" not in out["confidence"]
    assert any("200-day SMA" in x for x in brief["entry_stop_ideas"])
    assert any("3% under the 200-day" in x for x in brief["entry_stop_ideas"])
    assert not any("Entry zone" in x for x in brief["entry_stop_ideas"])
    assert not any("Advisory 7%" in x for x in brief["entry_stop_ideas"])
    assert any("Breakout buffer" in x for x in brief["entry_stop_ideas"])


def test_large_ratio_fields_display_as_percent_points(monkeypatch) -> None:
    monkeypatch.setattr("execution.get_position_size_usd", lambda **_kwargs: 5_000.0)
    out = build_decision_card_payload(
        {
            "ticker": "MSFT",
            "price": 250.0,
            "pead_surprise_pct": 1.2,
            "pead_beat": True,
            "entry_timing_shadow": {"breakout_buffer_pct": 1.5, "pct_above_sma50": 1.1},
            "sma_50": 119.0,
        },
        ticker="MSFT",
        skill_dir=Path("."),
        checklist={},
        size_usd=5_000.0,
    )
    notes = out["brief"]["catalyst_notes"]
    ideas = out["brief"]["entry_stop_ideas"]
    assert any("120.0%" in x for x in notes)
    assert any("150.0%" in x for x in ideas)
    assert any("110.0%" in x for x in ideas)


def test_decision_card_sparse_row_still_fills_dropdowns(monkeypatch) -> None:
    monkeypatch.setattr("execution.get_position_size_usd", lambda **_kwargs: 5_000.0)
    out = build_decision_card_payload(
        {"ticker": "XYZ", "symbol": "XYZ"},
        ticker="XYZ",
        skill_dir=Path("."),
        checklist={},
        size_usd=5_000.0,
    )
    brief = out["brief"]
    assert brief["key_risks"]
    assert brief["catalyst_notes"]
    assert brief["forensic_flags"]
    assert brief["sec_notes"]
    assert brief["entry_stop_ideas"]
    assert "10 trading days" in brief["expected_move_window"]
    assert "No forensic flags" in brief["forensic_flags"][0]


def test_decision_card_uses_rel_volume_and_horizon(monkeypatch) -> None:
    monkeypatch.setattr("execution.get_position_size_usd", lambda **_kwargs: 5_000.0)
    out = build_decision_card_payload(
        {
            "ticker": "BBY",
            "price": 92.56,
            "rel_volume": 0.4,
            "timeframe": "weekly",
            "strategy_attribution": {"top_live": "weekly_swing"},
        },
        ticker="BBY",
        skill_dir=Path("."),
        checklist={},
        size_usd=5_000.0,
    )
    notes = out["brief"]["catalyst_notes"]
    assert any("0.40x" in x for x in notes)
    assert any("weekly" in x.lower() for x in notes)


def test_find_signal_in_scan_collections_prefers_first_match() -> None:
    kept = [{"ticker": "AAPL", "price": 1}]
    shortlist = [{"ticker": "MSFT", "price": 2}, {"symbol": "NVDA", "price": 3}]
    assert find_signal_in_scan_collections("msft", kept, shortlist)["price"] == 2
    assert find_signal_in_scan_collections("NVDA", kept, shortlist)["price"] == 3
    assert find_signal_in_scan_collections("TSLA", kept, shortlist) is None


def test_coerce_scan_signal_merges_nested_payload() -> None:
    raw = {"ticker": "AAPL", "signal": {"ticker": "AAPL", "price": 12.5, "composite_score": 70}}
    out = coerce_scan_signal(raw)
    assert out is not None
    assert out["price"] == 12.5
    assert out["composite_score"] == 70


def test_decision_card_sharadar_chips_omit_nulls(monkeypatch) -> None:
    monkeypatch.setattr("execution.get_position_size_usd", lambda **_kwargs: 5_000.0)
    monkeypatch.setattr(
        "sharadar_data.get_sharadar_research_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "enabled": True,
            "status": "ok",
            "ticker": "AAPL",
            "as_of": "2026-09-18",
            "from_cache": True,
            "errors": [],
            "data_quality": "ok",
            "daily": {"date": "2026-09-18", "marketcap": 3.2e12, "pe": 32.1, "ps": None, "evebitda": 22.0},
            "fundamentals": {"netinc": 10, "ncfo": 8, "assets": 100, "datekey": "2026-08-01"},
            "fundamentals_history": [
                {"netinc": 10, "ncfo": 8, "assets": 100, "datekey": "2026-08-01"},
            ],
            "holders": [{"name": "Vanguard", "units": 1e9, "percent": 8.1, "value": 5e10}],
            "insiders": [
                {"filingdate": "2026-08-15", "ownername": "Cook", "transactioncode": "S", "transactionshares": -1000}
            ],
            "actions": [],
            "events": [],
            "pit_fields": {"institutional_ownership": 8.1},
        },
    )
    out = build_decision_card_payload(
        {"ticker": "AAPL", "price": 100.0, "forensic_sloan": 0.2},
        ticker="AAPL",
        skill_dir=Path("."),
        checklist={},
        size_usd=5_000.0,
    )
    overlay = out["sharadar"]
    assert overlay["available"] is True
    ids = {c["id"] for c in overlay["chips"]}
    assert ids == {"marketcap", "pe", "evebitda"}
    assert "ps" not in ids
    assert overlay["holders"][0]["name"] == "Vanguard"
    assert overlay["insiders"][0]["ownername"] == "Cook"
    assert overlay["actions"] == []
    assert any("sharadar_art" in x for x in out["brief"]["forensic_flags"])

