"""Honest daily book: fixtures stay out, overlapping trades are not a chain."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from core.tab_agent_book import (
    MISSING_PATHS,
    NEAR_ZERO_DOWNSIDE,
    apply_round_trip,
    calibration_from_pairs,
    score_book_rows,
    score_legs,
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


def _spy_sessions(start: date, end: date, holidays: set[str]) -> dict[str, float]:
    """Weekday SPY returns, with the named holidays left out of the cache."""
    out: dict[str, float] = {}
    cursor = start
    while cursor <= end:
        iso = cursor.isoformat()
        if cursor.weekday() < 5 and iso not in holidays:
            out[iso] = -0.01 if cursor.day % 5 == 0 else 0.002
        cursor += timedelta(days=1)
    return out


def test_market_holidays_keep_spy_on_the_same_sessions() -> None:
    """A weekday holiday must not wipe the SPY comparison, and SPY Sortino uses those sessions."""
    holidays = {"2026-06-19", "2026-07-03", "2026-09-07"}
    daily = [
        {"date": "2026-06-18", "return": 0.02},
        {"date": "2026-06-22", "return": -0.03},
        {"date": "2026-07-02", "return": 0.01},
        {"date": "2026-07-06", "return": -0.02},
        {"date": "2026-09-04", "return": 0.01},
        {"date": "2026-09-08", "return": -0.04},
    ]
    spy = _spy_sessions(date(2026, 6, 18), date(2026, 9, 8), holidays)
    rows = [
        {
            "ticker": "MSFT",
            "date": "2026-06-17",
            "return_pct": 1.0,
            "decision": "long",
            "daily": daily,
        }
    ]
    book = score_book_rows(rows, config=_config(), spy_daily=spy, apply_cost=False)
    for holiday in holidays:
        assert holiday not in book["daily_dates"]
    assert "2026-06-18" in book["daily_dates"]
    assert "2026-06-22" in book["daily_dates"]
    assert "2026-06-23" in book["daily_dates"]
    equity = 1.0
    for day in book["daily_dates"]:
        assert day in spy
        equity *= 1.0 + spy[day]
    assert book["spy_total_return"] == pytest.approx(round(equity - 1.0, 6))
    assert book["spy_sortino"] is not None
    assert book["spy_max_drawdown"] is not None
    assert book["spy_max_drawdown"] < 0
    bare = score_book_rows(rows, config=_config(), apply_cost=False)
    assert "2026-06-19" not in bare["daily_dates"]
    assert bare["daily_dates"][0] == "2026-06-18"
    assert "2026-07-03" not in bare["daily_dates"]
    assert "2026-09-07" not in bare["daily_dates"]


def test_digest_prints_spy_sortino_beside_the_book() -> None:
    from core.tab_agent_digest import _markdown

    text = _markdown(
        {
            "day": "2026-11-18",
            "note": "Analysis prompts only. No order, size, or execution setting is changed by this digest.",
            "tabs": [
                {
                    "tab_id": "today",
                    "live": [
                        {
                            "prompt_key": "mean_reversion",
                            "version_id": "v1",
                            "n": 3,
                            "n_longs": 2,
                            "n_weeks": 3,
                            "sample_status": "insufficient",
                            "win_rate": 0.5,
                            "n_live": 3,
                            "n_replay": 0,
                            "primary_metric": "sortino",
                            "primary_score": 1.2,
                            "spy_sortino": 0.8,
                            "spy_max_drawdown": -0.11,
                            "spy_total_return": 0.05,
                            "max_drawdown": -0.2,
                        }
                    ],
                }
            ],
        }
    )
    assert "sortino=1.2 spy_sortino=0.8 spy_maxDD=-0.11" in text


def test_price_cache_fills_a_pathless_long(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "prices"
    cache.mkdir()
    index = pd.bdate_range("2026-06-01", periods=8)
    closes = [101.0, 110.0, 105.0, 100.0, 102.0, 101.0, 103.0, 104.0]
    frame = pd.DataFrame(
        {
            "open": [100.0, 100.0, 110.0, 105.0, 100.0, 102.0, 101.0, 103.0],
            "high": [price + 1.0 for price in closes],
            "low": [price - 1.0 for price in closes],
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
        },
        index=index,
    )
    frame.to_csv(cache / "AAAA.csv", index_label="date")
    monkeypatch.setenv("BACKTEST_PRICE_CACHE_DIR", str(cache))
    walked = walk_long_position(frame, signal_date="2026-06-01", horizon_days=20, stop_price=None)
    assert walked is not None
    spy = {row["date"]: (0.01 if index % 2 == 0 else -0.02) for index, row in enumerate(walked["daily_returns"])}
    rows = [{"ticker": "AAAA", "date": "2026-06-01", "return_pct": 80.0, "decision": "long"}]
    book = score_book_rows(rows, config=_config(), skill_dir=tmp_path, spy_daily=spy, apply_cost=False)
    assert book.get("not_scored_reason") is None
    assert book["daily_returns"] == pytest.approx([row["return"] for row in walked["daily_returns"]])
    assert book["daily_returns"][0] == pytest.approx(0.10)
    assert book["sortino"] is not None
    assert book["max_drawdown"] is not None
    assert book["spy_total_return"] is not None
    assert book["spy_sortino"] is not None
    assert book["spy_max_drawdown"] is not None


def test_pathless_longs_do_not_report_a_sortino(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No daily path and no cache bar: the book must not invent a Sortino or a max DD."""
    cache = tmp_path / "prices"
    cache.mkdir()
    (cache / "SPY.csv").write_text(
        "date,open,high,low,close,volume\n2026-06-01,100,101,99,100,1\n2026-06-02,100,101,99,101,1\n",
        encoding="utf-8",
    )
    (cache / "BIL.csv").write_text(
        "date,open,high,low,close,volume\n2026-06-01,100,100,100,100,1\n2026-06-02,100,100,100,100.01,1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BACKTEST_PRICE_CACHE_DIR", str(cache))
    rows = [
        {"ticker": "AAAA", "date": "2026-06-01", "return_pct": 12.0, "decision": "long"},
        {"ticker": "BBBB", "date": "2026-07-01", "return_pct": 4.0, "decision": "long"},
    ]
    book = score_book_rows(rows, config=_config(), skill_dir=tmp_path, apply_cost=False)
    assert book["not_scored_reason"] == MISSING_PATHS
    assert book["sample_status"] == MISSING_PATHS
    assert book["sortino"] is None
    assert book["primary_score"] is None
    assert book["max_drawdown"] is None
    assert book["sortino_cost_10bps"] is None
    assert book["max_drawdown_cost_10bps"] is None


def test_cash_at_bil_does_not_print_an_inflated_sortino() -> None:
    cash: dict[str, float] = {}
    cursor = date(2026, 1, 5)
    step = 0
    while cursor <= date(2026, 3, 27):
        if cursor.weekday() < 5:
            cash[cursor.isoformat()] = -0.00002 if step % 11 == 0 else 0.0001
            step += 1
        cursor += timedelta(days=1)
    book = score_legs(
        [
            {"date": "2026-01-05", "decision": "cash", "return_pct": 0.0, "horizon_days": 1},
            {"date": "2026-03-27", "decision": "cash", "return_pct": 0.0, "horizon_days": 1},
        ],
        cash_daily=cash,
        apply_cost=False,
    )
    assert book["invested_days"] == 0
    assert book["sortino"] is None
    assert book["primary_score"] is None
    assert book["primary_unbounded"] is False
    assert book["not_scored_reason"] == NEAR_ZERO_DOWNSIDE
    assert book["sortino_cost_10bps"] is None
