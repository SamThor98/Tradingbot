"""Honest daily book: fixtures stay out, overlapping trades are not a chain."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pandas as pd
import pytest

from core.tab_agent_book import (
    apply_round_trip,
    calibration_from_pairs,
    score_book_rows,
    stop_fill,
    walk_long_position,
    week_cluster_stats,
)
from core.tab_agent_objective import ObjectiveConfig


def _config() -> ObjectiveConfig:
    return ObjectiveConfig(min_samples=1, horizon_days=20, drawdown_tolerance=0.20)


def test_quarantined_packet_never_enters_a_book(tmp_path) -> None:
    quarantine = {
        "packets": [
            {
                "packet_id": "q-1",
                "ticker": "ZZZZ",
                "created_at": "2026-05-28T15:00:00+00:00",
                "entry_price": 10.0,
            }
        ]
    }
    (tmp_path / "decision_packets.fixture_quarantine.json").write_text(json.dumps(quarantine), encoding="utf-8")
    rows = [
        {
            "packet_id": "q-1",
            "ticker": "ZZZZ",
            "date": "2026-05-28",
            "return_pct": 54.0,
            "decision": "long",
        },
        {"ticker": "SNA", "date": "2026-07-06", "return_pct": 40.0, "decision": "long"},
        {
            "ticker": "MSFT",
            "date": "2026-06-02",
            "return_pct": 1.0,
            "decision": "long",
            "daily": [{"date": "2026-06-03", "return": 0.01}],
        },
    ]
    book = score_book_rows(rows, config=_config(), skill_dir=tmp_path, apply_cost=False)
    assert book["excluded"] == 2
    assert book["tickers"] == ["MSFT"]
    assert book["n"] == 1
    assert book["avg_realized_return_pct"] == 1.0


def test_overlapping_trades_are_equal_weight_not_a_chain() -> None:
    """Three same-day -20% trades. Serial compounding is -48.8%. The daily book is -20%."""
    daily = [{"date": "2026-10-01", "return": -0.20}]
    rows = [
        {"ticker": f"T{index}", "date": "2026-10-01", "return_pct": -20.0, "decision": "long", "daily": daily}
        for index in range(3)
    ]
    book = score_book_rows(rows, config=_config(), apply_cost=False, spy_daily={"2026-10-01": 0.01})
    assert book["per_trade_max_drawdown"] == pytest.approx(0.8**3 - 1.0)
    assert book["max_drawdown"] == pytest.approx(-0.20)
    assert book["per_trade_sortino"] == pytest.approx(-1.0)
    assert book["sortino"] == pytest.approx(-math.sqrt(252))
    assert book["spy_total_return"] == pytest.approx(0.01)
    assert book["book_method"] == "daily_mtm"
    assert book["week_gate"] == "insufficient"


def test_two_names_share_a_day_at_equal_weight() -> None:
    rows = [
        {
            "ticker": "AAA",
            "date": "2026-06-01",
            "return_pct": -10.0,
            "decision": "long",
            "daily": [
                {"date": "2026-06-01", "return": -0.10},
                {"date": "2026-06-03", "return": -0.10},
            ],
        },
        {
            "ticker": "BBB",
            "date": "2026-06-02",
            "return_pct": -30.0,
            "decision": "long",
            "daily": [{"date": "2026-06-03", "return": -0.30}],
        },
    ]
    book = score_book_rows(
        rows,
        config=_config(),
        apply_cost=False,
        cash_daily={"2026-06-02": 0.0002},
    )
    by_day = dict(zip(book["daily_dates"], book["daily_returns"], strict=False))
    assert by_day["2026-06-01"] == pytest.approx(-0.10)
    assert by_day["2026-06-02"] == pytest.approx(0.0002)
    assert by_day["2026-06-03"] == pytest.approx(-0.20)


def test_gap_stop_fills_at_the_open() -> None:
    assert stop_fill(95.0, 90.0) == 90.0
    assert stop_fill(95.0, 97.0) == 95.0
    frame = pd.DataFrame(
        {
            "open": [100.0, 100.0, 90.0],
            "high": [101.0, 101.0, 91.0],
            "low": [99.0, 99.0, 88.0],
            "close": [100.0, 100.0, 89.0],
        },
        index=pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-05"]),
    )
    walked = walk_long_position(frame, signal_date="2026-10-01", horizon_days=5, stop_price=95.0)
    assert walked is not None
    assert walked["entry_convention"] == "next_open"
    assert walked["entry_price"] == 100.0
    assert walked["stopped"] is True
    assert walked["daily_returns"][-1]["return"] == pytest.approx(-0.10)
    assert walked["daily_returns"][-1]["return"] != pytest.approx(-0.05)


def test_round_trip_cost_and_the_sensitivity() -> None:
    gross = 0.02
    at_34 = apply_round_trip(gross, 0.0034)
    at_10 = apply_round_trip(gross, 0.0010)
    assert at_34 == pytest.approx(1.02 * (1.0 - 0.0034) - 1.0)
    assert at_10 == pytest.approx(1.02 * (1.0 - 0.0010) - 1.0)
    assert at_10 > at_34
    rows = [
        {
            "ticker": "MSFT",
            "date": "2026-06-01",
            "return_pct": 2.0,
            "decision": "long",
            "daily": [
                {"date": "2026-06-02", "return": 0.02},
                {"date": "2026-06-03", "return": -0.01},
            ],
        }
    ]
    book = score_book_rows(rows, config=_config(), apply_cost=True)
    assert book["daily_returns"][0] == pytest.approx(apply_round_trip(0.02, 0.0017))
    assert book["daily_returns"][1] == pytest.approx(apply_round_trip(-0.01, 0.0017))
    assert book["round_trip_cost_pct"] == pytest.approx(0.34)
    assert book["sortino_cost_10bps"] != book["sortino"]


def test_week_cluster_marks_a_short_sample_insufficient() -> None:
    days = [datetime(2026, 1, 5).date()]
    from datetime import timedelta

    short_days = [days[0] + timedelta(days=7 * index) for index in range(7)]
    long_days = [days[0] + timedelta(days=7 * index) for index in range(8)]
    short = week_cluster_stats([0.01] * 7, short_days)
    long = week_cluster_stats([0.01] * 8, long_days)
    assert short["status"] == "insufficient"
    assert short["n_weeks"] == 7
    assert short["shrunk"] == round((7 / 17) * 0.01, 6)
    assert long["status"] == "ok"
    assert long["shrunk"] == round((8 / 18) * 0.01, 6)
    assert long["ci_low"] is not None and long["ci_high"] is not None


def test_calibration_is_not_the_negative_of_a_skip() -> None:
    pairs = [(0.2, 1.0, "2026-06-01"), (0.2, 1.0, "2026-06-02")]
    book = calibration_from_pairs(pairs)
    assert book["scoring"] == "calibration"
    assert book["primary_metric"] == "brier"
    assert book["brier"] == pytest.approx(0.64)
    assert book["sortino"] is None
    assert book["lower_is_better"] is True
    crowded = calibration_from_pairs([(0.8, 0.0, f"2026-06-{index + 1:02d}") for index in range(5)])
    assert crowded["reliability"]
    assert crowded["reliability"][0]["n"] == 5


def test_unresolved_attributions_wait_for_the_horizon_label(tmp_path) -> None:
    from core.tab_agent import score_prompt_versions

    quarantine = {"packets": [{"packet_id": "q-1", "ticker": "ZZZZ", "created_at": "2026-05-28T15:00:00+00:00"}]}
    (tmp_path / "decision_packets.fixture_quarantine.json").write_text(json.dumps(quarantine), encoding="utf-8")
    packets = [
        {
            "packet_id": "q-1",
            "ticker": "ZZZZ",
            "created_at": "2026-05-28T15:00:00+00:00",
            "side": "long",
            "outcome": {"label": "win", "realized_return_pct": 54.0, "horizon_days": 20},
        },
        {
            "ticker": "MSFT",
            "created_at": "2026-06-02T15:00:00+00:00",
            "side": "long",
            "outcome": {"label": "win", "realized_return_pct": 4.0, "horizon_days": 20},
        },
        {
            "ticker": "NVDA",
            "created_at": "2026-11-04T15:00:00+00:00",
            "side": "long",
            "outcome": {"label": "pending"},
        },
    ]
    versions = [{"id": "v1", "prompt_key": "institutional_trend", "status": "live", "parent": None}]
    attributions = [
        {"version_id": "v1", "ticker": "ZZZZ", "day": "2026-05-28", "decision": "long"},
        {"version_id": "v1", "ticker": "MSFT", "day": "2026-06-02", "decision": "long"},
        {
            "version_id": "v1",
            "ticker": "NVDA",
            "day": "2026-11-04",
            "decision": "long",
            "created_at": "2026-11-04T15:00:00+00:00",
        },
    ]
    report = score_prompt_versions(
        versions=versions,
        packets=packets,
        attributions=attributions,
        skill_dir=tmp_path,
        tab_id="today",
    )
    book = report["versions"][0]
    assert book["n"] == 1
    assert book["avg_realized_return_pct"] == 4.0
    join = report["attribution_join"]
    assert join["excluded"] == 1
    assert join["unresolved"] == 1
    assert join["joined"] == 1
    assert join["earliest_unresolved_day"] == "2026-11-04"
    assert "2026-12-03" in join["note"]


def test_scale_out_label_records_cost_without_changing_the_stored_return(tmp_path) -> None:
    from core import decision_packet, outcome_backfill

    created = datetime(2026, 6, 1, tzinfo=timezone.utc)
    packet = decision_packet.build_packet(
        ticker="AAPL",
        kind="staged",
        signal={"price": 100.0, "rank_score": 70.0, "stop_pct": 0.05},
        created_at=created,
    ).model_dump(mode="json")
    decision_packet.overwrite_packets(tmp_path, [packet])
    rows = [{"open": 100.0, "close": 100.0, "high": 101.0, "low": 99.0} for _ in range(50)]
    rows[4] = {"open": 111.0, "close": 111.0, "high": 112.0, "low": 109.0}
    rows[5] = {"open": 109.0, "close": 109.0, "high": 111.0, "low": 108.0}
    rows[6] = {"open": 108.0, "close": 108.0, "high": 109.0, "low": 106.0}
    hist = pd.DataFrame(rows, index=pd.date_range("2026-06-01", periods=50, freq="D"))
    outcome_backfill.run_local_backfill(
        tmp_path,
        history_provider=lambda _ticker: hist,
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
        force=True,
    )
    stored = decision_packet.load_packets(tmp_path)[0]["outcome"]
    assert stored["realized_return_pct"] == 8.5
    assert stored["entry_convention"] == "next_open"
    assert stored["realized_cost_pct"] == pytest.approx(0.34)
    assert stored["stop_fill"] == "min_stop_open"
    assert stored["entry_price_used"] == pytest.approx(100.0)
