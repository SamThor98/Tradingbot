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


def _write_price(cache, ticker: str, frame: pd.DataFrame) -> None:
    frame.to_csv(cache / f"{ticker}.csv", index_label="date")


def _path_net(leg: dict, lookup) -> float:
    from core.tab_agent_book import ROUND_TRIP_COST, _Calendar, _compound, _resolve_leg

    calendar = _Calendar(None)
    path, source = _resolve_leg(leg, cost=ROUND_TRIP_COST, apply_cost=True, calendar=calendar, lookup=lookup)
    assert source == "cache"
    return _compound([ret for day, ret in path if calendar.is_session(day)])


def test_selection_pool_uses_the_cache_walk_not_the_stored_return(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A scale-out percent must not be the same-day pool once the cache can walk the hold."""
    from core.tab_agent import scan_leg, score_prompt_versions
    from core.tab_agent_book import _price_lookup

    cache = tmp_path / "prices"
    cache.mkdir()
    index = pd.bdate_range("2026-06-01", periods=25)
    held = [100.0 + index_ * 0.4 for index_ in range(len(index))]
    aaa = pd.DataFrame(
        {
            "open": [100.0] * len(index),
            "high": [price + 1.0 for price in held],
            "low": [price - 0.4 for price in held],
            "close": held,
            "volume": [1_000_000.0] * len(index),
        },
        index=index,
    )
    ran = [100.0 * (1.05**index_) for index_ in range(len(index))]
    bbb = pd.DataFrame(
        {
            "open": [100.0] * len(index),
            "high": [price + 1.0 for price in ran],
            "low": [90.0] * len(index),
            "close": ran,
            "volume": [1_000_000.0] * len(index),
        },
        index=index,
    )
    qqq = aaa.copy()
    qqq["close"] = qqq["close"] * 3.0
    qqq["high"] = qqq["close"] + 1.0
    _write_price(cache, "AAA", aaa)
    _write_price(cache, "BBB", bbb)
    _write_price(cache, "QQQQ", qqq)
    monkeypatch.setenv("BACKTEST_PRICE_CACHE_DIR", str(cache))
    quarantine = {"packets": [{"packet_id": "q-1", "ticker": "QQQQ", "created_at": "2026-06-01T15:00:00+00:00"}]}
    (tmp_path / "decision_packets.fixture_quarantine.json").write_text(json.dumps(quarantine), encoding="utf-8")
    day = "2026-06-01T15:00:00+00:00"
    packets = [
        {
            "ticker": "AAA",
            "created_at": day,
            "side": "long",
            "stop_price": 90.0,
            "outcome": {"label": "win", "realized_return_pct": 40.0, "horizon_days": 20, "exit_model": "scale_out"},
        },
        {
            "ticker": "BBB",
            "created_at": day,
            "side": "long",
            "stop_price": 110.0,
            "outcome": {"label": "win", "realized_return_pct": 50.0, "horizon_days": 20, "exit_model": "scale_out"},
        },
        {
            "packet_id": "q-1",
            "ticker": "QQQQ",
            "created_at": day,
            "side": "long",
            "outcome": {"label": "win", "realized_return_pct": 99.0, "horizon_days": 20},
        },
    ]
    versions = [{"id": "v1", "prompt_key": "institutional_trend", "status": "live", "parent": None}]
    attributions = [{"version_id": "v1", "ticker": "AAA", "day": "2026-06-01", "decision": "long"}]
    report = score_prompt_versions(
        versions=versions,
        packets=packets,
        attributions=attributions,
        skill_dir=tmp_path,
        tab_id="today",
    )
    book = report["versions"][0]
    lookup = _price_lookup(tmp_path)
    assert lookup is not None
    net_aaa = _path_net(scan_leg(packets[0], horizon_days=20), lookup)
    net_bbb = _path_net(scan_leg(packets[1], horizon_days=20), lookup)
    assert net_aaa < 0.2
    assert abs(net_bbb) < 0.02
    expected = net_aaa - (net_aaa + net_bbb) / 2.0
    stored_mix = net_aaa - (40.0 + 50.0) / 200.0
    selection = book["selection_vs_pool"]
    assert selection["n"] == 1
    assert selection["mean"] == pytest.approx(expected, abs=1e-6)
    assert selection["shrunk"] == pytest.approx(expected / 11.0, abs=1e-6)
    assert abs(selection["mean"] - stored_mix) > 0.2
    assert book["n"] == 1
    assert book["avg_realized_return_pct"] == 40.0


def _ohlc(
    index: pd.DatetimeIndex, closes: list[float], *, opens: list[float] | None = None, lows: list[float] | None = None
) -> pd.DataFrame:
    if opens is None:
        opens = [closes[0], *closes[:-1]]
    if lows is None:
        lows = [min(open_, close) - 0.5 for open_, close in zip(opens, closes, strict=True)]
    highs = [max(open_, close) + 0.5 for open_, close in zip(opens, closes, strict=True)]
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1_000_000.0] * len(closes)},
        index=index,
    )


def _spy_map(index: pd.DatetimeIndex, closes: list[float]) -> dict[str, float]:
    out: dict[str, float] = {}
    previous: float | None = None
    for stamp, price in zip(index, closes, strict=True):
        if previous is not None and previous > 0:
            out[str(stamp)[:10]] = price / previous - 1.0
        previous = price
    return out


def _hold_excess(leg: dict, lookup, spy: dict[str, float]) -> tuple[float, float]:
    """Hold minus SPY over that hold. Same walk the book uses, outside the aggregator."""
    from core.tab_agent_book import ROUND_TRIP_COST, _Calendar, _covered_net_and_spy, _resolve_leg

    calendar = _Calendar(spy)
    path, source = _resolve_leg(leg, cost=ROUND_TRIP_COST, apply_cost=True, calendar=calendar, lookup=lookup)
    assert source == "cache"
    net, spy_ret = _covered_net_and_spy(path, calendar, spy)
    assert spy_ret is not None
    return net, spy_ret


def test_raw_excess_minus_selection_is_the_prompt_weighted_pool_excess() -> None:
    """Different hold lengths must not make pool-minus-SPY depend on the prompt's window.

    A stopped name and a full hold on the same day have different SPY paths.
    Subtracting the day's average return from the prompt's own SPY is not the
    pool's excess. An orphan long, and a second row for a ticker, stay out.
    """
    index = pd.bdate_range("2026-06-01", periods=24)
    days = [str(stamp)[:10] for stamp in index]
    n = len(index)
    spy = _spy_map(index, [100.0 + step * 0.45 for step in range(n)])
    bbb_opens = [100.0] * n
    bbb_lows = [99.0] * n
    bbb_closes = [100.0 + step * 0.2 for step in range(n)]
    bbb_opens[1] = 94.0
    bbb_lows[1] = 80.0
    bbb_closes[1] = 96.0
    frames = {
        "AAA": _ohlc(index, [50.0 + step * 0.35 for step in range(n)]),
        "BBB": _ohlc(index, bbb_closes, opens=bbb_opens, lows=bbb_lows),
        "CCC": _ohlc(index, [80.0] * n),
        "DDD": _ohlc(index, [40.0 + step * 0.7 for step in range(n)]),
        "EEE": _ohlc(index, [70.0 - step * 0.15 for step in range(n)]),
        "ZZZ": _ohlc(index, [30.0 + step * 0.1 for step in range(n)]),
    }

    def leg(ticker: str, day: str, stop: float) -> dict:
        return {
            "date": day,
            "ticker": ticker,
            "decision": "long",
            "return_pct": 1.0,
            "horizon_days": 4,
            "stop_price": stop,
        }

    day_a, day_b, day_c, orphan_day = days[0], days[6], days[12], days[18]
    pool_legs = [
        leg("AAA", day_a, 10.0),
        leg("BBB", day_a, 90.0),
        leg("CCC", day_a, 10.0),
        leg("BBB", day_a, 10.0),
        leg("AAA", day_b, 10.0),
        leg("DDD", day_b, 10.0),
        leg("EEE", day_b, 10.0),
        leg("CCC", day_c, 10.0),
        leg("DDD", day_c, 10.0),
    ]
    prompt_legs = [
        leg("BBB", day_a, 90.0),
        leg("AAA", day_b, 10.0),
        leg("EEE", day_b, 10.0),
        leg("ZZZ", orphan_day, 10.0),
    ]
    lookup = frames.get
    stopped_net, stopped_spy = _hold_excess(leg("BBB", day_a, 90.0), lookup, spy)
    full_net, full_spy = _hold_excess(leg("AAA", day_a, 10.0), lookup, spy)
    assert stopped_spy != pytest.approx(full_spy)
    assert abs(stopped_net - full_net) > 0.01

    peers: dict[str, list[float]] = {}
    peer_nets: dict[str, list[float]] = {}
    seen: set[tuple[str, str]] = set()
    for row in pool_legs:
        key = (row["date"], row["ticker"])
        if key in seen:
            continue
        seen.add(key)
        net, spy_ret = _hold_excess(row, lookup, spy)
        peers.setdefault(row["date"], []).append(net - spy_ret)
        peer_nets.setdefault(row["date"], []).append(net)

    prompt_excess: list[float] = []
    pool_excess: list[float] = []
    old_gap: list[float] = []
    for row in prompt_legs:
        day_peers = peers.get(row["date"])
        if not day_peers:
            continue
        net, spy_ret = _hold_excess(row, lookup, spy)
        prompt_excess.append(net - spy_ret)
        pool_excess.append(sum(day_peers) / len(day_peers))
        old_gap.append(sum(peer_nets[row["date"]]) / len(peer_nets[row["date"]]) - spy_ret)
    true_pool = sum(pool_excess) / len(pool_excess)
    assert abs(true_pool - sum(old_gap) / len(old_gap)) > 1e-3

    book = score_legs(prompt_legs, pool_legs=pool_legs, spy_daily=spy, price_lookup=lookup)
    excess = book["excess_vs_spy"]
    selection = book["selection_vs_pool"]
    assert excess["n"] == selection["n"] == len(prompt_excess)
    assert excess["n"] < len(prompt_legs)
    assert excess["mean"] - selection["mean"] == pytest.approx(true_pool, abs=1e-5)
    assert excess["mean"] == pytest.approx(sum(prompt_excess) / len(prompt_excess), abs=1e-5)
    assert selection["mean"] == pytest.approx(
        sum(prompt - pool for prompt, pool in zip(prompt_excess, pool_excess, strict=True)) / len(prompt_excess),
        abs=1e-5,
    )


def test_buy_every_scan_excess_matches_the_pool_when_every_row_is_picked(tmp_path, monkeypatch) -> None:
    """Buying every scan row is the pool. Its excess over SPY is the baseline's excess."""
    from core.decision_packet import overwrite_packets
    from core.tab_agent import scan_leg, score_prompt_versions
    from core.tab_agent_book import _price_lookup
    from core.tab_agent_replay import append_sample

    monkeypatch.setenv("DECISION_PACKET_HORIZON_DAYS", "4")
    monkeypatch.setenv("BACKTEST_PRICE_CACHE_DIR", str(tmp_path / "prices"))
    index = pd.bdate_range("2026-06-01", periods=16)
    n = len(index)
    days = [str(stamp)[:10] for stamp in index]
    bbb_opens = [100.0] * n
    bbb_lows = [99.0] * n
    bbb_closes = [100.0 + step * 0.15 for step in range(n)]
    bbb_opens[1] = 94.0
    bbb_lows[1] = 80.0
    bbb_closes[1] = 96.0
    frames = {
        "SPY": _ohlc(index, [100.0 + step * 0.4 for step in range(n)]),
        "AAA": _ohlc(index, [50.0 + step * 0.3 for step in range(n)]),
        "BBB": _ohlc(index, bbb_closes, opens=bbb_opens, lows=bbb_lows),
        "CCC": _ohlc(index, [60.0 + step * 0.05 for step in range(n)]),
        "DDD": _ohlc(index, [42.0 + step * 0.55 for step in range(n)]),
    }
    cache = tmp_path / "prices"
    cache.mkdir()
    for ticker, frame in frames.items():
        frame.to_csv(cache / f"{ticker}.csv", index_label="date")

    specs = [
        ("AAA", days[0], 10.0),
        ("BBB", days[0], 90.0),
        ("CCC", days[0], 10.0),
        ("AAA", days[5], 10.0),
        ("DDD", days[5], 10.0),
        ("CCC", days[8], 10.0),
    ]
    packets = []
    attributions = []
    for ticker, day, stop in specs:
        packets.append(
            {
                "ticker": ticker,
                "created_at": f"{day}T15:00:00+00:00",
                "side": "long",
                "stop_price": stop,
                "outcome": {"label": "win", "realized_return_pct": 9.0, "horizon_days": 4},
            }
        )
        attributions.append({"version_id": "v1", "ticker": ticker, "day": day, "decision": "long"})
        append_sample(
            tmp_path,
            {
                "source": "replay",
                "tab_id": "today",
                "prompt_key": "buy_every_scan",
                "version_id": "baseline-only",
                "ticker": ticker,
                "as_of": day,
                "decision": "long",
                "counterfactual_return_pct": 9.0,
                "stop_price": None,
                "horizon_days": 20,
                "scored": True,
            },
        )
    append_sample(
        tmp_path,
        {
            "source": "replay",
            "tab_id": "today",
            "prompt_key": "institutional_trend",
            "version_id": "v-other",
            "ticker": "ZZZ",
            "as_of": days[10],
            "decision": "long",
            "counterfactual_return_pct": 40.0,
            "horizon_days": 20,
            "scored": True,
        },
    )
    overwrite_packets(tmp_path, packets)
    versions = [{"id": "v1", "prompt_key": "institutional_trend", "status": "live", "parent": None}]
    report = score_prompt_versions(
        versions=versions,
        packets=packets,
        attributions=attributions,
        skill_dir=tmp_path,
        tab_id="today",
    )
    book = report["versions"][0]
    baseline = report["baseline"]
    lookup = _price_lookup(tmp_path)
    assert lookup is not None
    from core.tab_agent_book import benchmark_series

    spy_daily, _cash = benchmark_series(tmp_path)
    assert spy_daily
    row_excess = []
    for packet in packets:
        net, spy_ret = _hold_excess(scan_leg(packet, horizon_days=4), lookup, spy_daily)
        row_excess.append(net - spy_ret)
    pool_excess = sum(row_excess) / len(row_excess)
    stopped, _spy = _hold_excess(scan_leg(packets[1], horizon_days=4), lookup, spy_daily)
    held, _held_spy = _hold_excess(scan_leg(packets[0], horizon_days=4), lookup, spy_daily)
    assert abs(stopped - held) > 0.01
    excess = book["excess_vs_spy"]
    selection = book["selection_vs_pool"]
    base_excess = baseline["excess_vs_spy"]
    assert excess["n"] == base_excess["n"] == len(specs)
    assert selection["n"] == len(specs)
    assert selection["mean"] == pytest.approx(0.0, abs=1e-5)
    assert excess["mean"] == pytest.approx(pool_excess, abs=1e-5)
    assert base_excess["mean"] == pytest.approx(pool_excess, abs=1e-5)
    assert excess["mean"] - selection["mean"] == pytest.approx(base_excess["mean"], abs=1e-5)


def test_pool_and_baseline_use_the_stored_stop_and_horizon(tmp_path, monkeypatch) -> None:
    """A frozen daily path and the default 20-day hold are not the book walk.

    The stored stop hits inside the stored horizon. A 20-session hold with no
    stop, and the daily series saved on the packet, are different prices.
    """
    from core.decision_packet import overwrite_packets
    from core.tab_agent import scan_leg, scan_pool_legs, score_prompt_versions
    from core.tab_agent_book import benchmark_series

    monkeypatch.setenv("DECISION_PACKET_HORIZON_DAYS", "6")
    monkeypatch.setenv("BACKTEST_PRICE_CACHE_DIR", str(tmp_path / "prices"))
    index = pd.bdate_range("2026-06-01", periods=30)
    days = [str(stamp)[:10] for stamp in index]
    n = len(index)
    stop_opens = [100.0] * n
    stop_lows = [99.0] * n
    stop_closes = [100.0 + step * 0.8 for step in range(n)]
    stop_opens[1] = 100.0
    stop_lows[1] = 70.0
    stop_closes[1] = 104.0
    hold_closes = [40.0 + step * 0.6 for step in range(n)]
    frames = {
        "SPY": _ohlc(index, [100.0 + step * 0.25 for step in range(n)]),
        "STOP": _ohlc(index, stop_closes, opens=stop_opens, lows=stop_lows),
        "HOLD": _ohlc(index, hold_closes),
    }
    cache = tmp_path / "prices"
    cache.mkdir()
    for ticker, frame in frames.items():
        frame.to_csv(cache / f"{ticker}.csv", index_label="date")
    bogus = [{"date": days[min(step + 1, n - 1)], "return": 0.04} for step in range(20)]

    def packet(ticker: str, day: str, stop: float) -> dict:
        return {
            "ticker": ticker,
            "created_at": f"{day}T15:00:00+00:00",
            "side": "long",
            "stop_price": stop,
            "outcome": {
                "label": "win",
                "realized_return_pct": 12.0,
                "horizon_days": 6,
                "daily_returns": bogus,
            },
        }

    packets = [packet("STOP", days[0], 92.0), packet("HOLD", days[0], 10.0)]
    overwrite_packets(tmp_path, packets)
    report = score_prompt_versions(
        versions=[{"id": "v1", "prompt_key": "institutional_trend", "status": "live", "parent": None}],
        packets=packets,
        attributions=[
            {"version_id": "v1", "ticker": "STOP", "day": days[0], "decision": "long"},
            {"version_id": "v1", "ticker": "HOLD", "day": days[0], "decision": "long"},
        ],
        skill_dir=tmp_path,
        tab_id="today",
    )
    spy_daily, _cash = benchmark_series(tmp_path)
    assert spy_daily
    legs = scan_pool_legs(packets, horizon_days=6, skill_dir=tmp_path)
    assert [leg["daily"] for leg in legs] == [None, None]
    assert [leg["horizon_days"] for leg in legs] == [6, 6]
    assert [leg["stop_price"] for leg in legs] == [92.0, 10.0]
    from core.tab_agent_book import _price_lookup

    lookup = _price_lookup(tmp_path)
    assert lookup is not None
    correct = [_hold_excess(leg, lookup, spy_daily) for leg in legs]
    correct_excess = sum(net - spy for net, spy in correct) / len(correct)
    default_legs = [
        {
            "date": days[0],
            "ticker": "STOP",
            "decision": "long",
            "horizon_days": 20,
            "stop_price": None,
            "return_pct": 1,
        },
        {
            "date": days[0],
            "ticker": "HOLD",
            "decision": "long",
            "horizon_days": 20,
            "stop_price": None,
            "return_pct": 1,
        },
    ]
    default_excess = sum(net - spy for net, spy in (_hold_excess(leg, lookup, spy_daily) for leg in default_legs)) / 2
    assert abs(correct_excess - default_excess) > 0.02
    stopped = _hold_excess(scan_leg(packets[0], horizon_days=6), lookup, spy_daily)
    unstopped = _hold_excess(
        {"date": days[0], "ticker": "STOP", "decision": "long", "horizon_days": 6, "stop_price": None, "return_pct": 1},
        lookup,
        spy_daily,
    )
    assert abs(stopped[0] - unstopped[0]) > 0.02
    book = report["versions"][0]
    baseline = report["baseline"]
    assert book["excess_vs_spy"]["mean"] == pytest.approx(correct_excess, abs=1e-5)
    assert baseline["excess_vs_spy"]["mean"] == pytest.approx(correct_excess, abs=1e-5)
    assert book["excess_vs_spy"]["n"] == baseline["excess_vs_spy"]["n"] == 2
    assert baseline["excess_vs_spy"]["mean"] != pytest.approx(default_excess, abs=1e-3)


def test_pool_eligibility_matches_the_baseline(tmp_path, monkeypatch) -> None:
    """Pending, quarantined, duplicate, and replay-only rows are not a second book."""
    from core.decision_packet import overwrite_packets
    from core.tab_agent import scan_pool_legs, score_prompt_versions
    from core.tab_agent_replay import append_sample

    monkeypatch.setenv("DECISION_PACKET_HORIZON_DAYS", "4")
    monkeypatch.setenv("BACKTEST_PRICE_CACHE_DIR", str(tmp_path / "prices"))
    index = pd.bdate_range("2026-06-01", periods=16)
    days = [str(stamp)[:10] for stamp in index]
    n = len(index)
    frames = {
        "SPY": _ohlc(index, [100.0 + step * 0.2 for step in range(n)]),
        "AAA": _ohlc(index, [50.0 + step * 0.3 for step in range(n)]),
        "BBB": _ohlc(index, [40.0 + step * 0.25 for step in range(n)]),
        "CCC": _ohlc(index, [70.0 + step * 0.1 for step in range(n)]),
        "QQQ": _ohlc(index, [30.0 + step * 0.4 for step in range(n)]),
    }
    cache = tmp_path / "prices"
    cache.mkdir()
    for ticker, frame in frames.items():
        frame.to_csv(cache / f"{ticker}.csv", index_label="date")
    quarantine = {"packets": [{"packet_id": "q-1", "ticker": "QQQ", "created_at": f"{days[0]}T15:00:00+00:00"}]}
    (tmp_path / "decision_packets.fixture_quarantine.json").write_text(json.dumps(quarantine), encoding="utf-8")

    def matured(ticker: str, day: str, *, packet_id: str | None = None, stop: float = 10.0) -> dict:
        row = {
            "ticker": ticker,
            "created_at": f"{day}T15:00:00+00:00",
            "side": "long",
            "stop_price": stop,
            "outcome": {"label": "win", "realized_return_pct": 3.0, "horizon_days": 4},
        }
        if packet_id:
            row["packet_id"] = packet_id
        return row

    packets = [
        matured("AAA", days[0], stop=15.0),
        matured("AAA", days[0], stop=80.0),
        matured("BBB", days[0]),
        matured("CCC", days[6]),
        {
            "ticker": "AAA",
            "created_at": f"{days[6]}T15:00:00+00:00",
            "side": "long",
            "stop_price": 10.0,
            "outcome": {"label": "pending", "horizon_days": 4},
        },
        matured("QQQ", days[0], packet_id="q-1"),
        matured("AAA", days[2], stop=12.0)
        | {"outcome": {"label": "win", "realized_return_pct": 3.0, "horizon_days": 20}},
    ]
    overwrite_packets(tmp_path, packets)
    append_sample(
        tmp_path,
        {
            "source": "replay",
            "tab_id": "today",
            "prompt_key": "institutional_trend",
            "version_id": "v-replay",
            "ticker": "ZZZ",
            "as_of": days[4],
            "decision": "long",
            "counterfactual_return_pct": 25.0,
            "horizon_days": 20,
            "scored": True,
        },
    )
    legs = scan_pool_legs(packets, horizon_days=4, skill_dir=tmp_path)
    keys = [(leg["ticker"], leg["date"]) for leg in legs]
    assert keys == [("AAA", days[0]), ("BBB", days[0]), ("CCC", days[6])]
    assert legs[0]["stop_price"] == 15.0
    report = score_prompt_versions(
        versions=[{"id": "v1", "prompt_key": "mean_reversion", "status": "live", "parent": None}],
        packets=packets,
        attributions=[{"version_id": "v1", "ticker": ticker, "day": day, "decision": "long"} for ticker, day in keys],
        skill_dir=tmp_path,
        tab_id="today",
    )
    baseline = report["baseline"]
    book = report["versions"][0]
    assert baseline["n"] == len(legs)
    assert baseline["excess_vs_spy"]["n"] == len(legs)
    assert baseline["excess_vs_spy"]["n_days"] == 2
    assert book["excess_vs_spy"]["n"] == len(legs)
    assert book["selection_vs_pool"]["mean"] == pytest.approx(0.0, abs=1e-5)
    assert book["excess_vs_spy"]["mean"] == pytest.approx(baseline["excess_vs_spy"]["mean"], abs=1e-5)
