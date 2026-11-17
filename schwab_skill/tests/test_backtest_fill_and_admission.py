"""Exit-blind portfolio admission, next-open fills, and era warmup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import backtest
import market_data
from config import get_backtest_entry_fill
from scripts.run_multi_era_backtest_schwab_only import _aggregate_era


def _ohlc(index: pd.DatetimeIndex, *, open_px: float = 10.0, close_px: float = 11.0) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "open": [open_px] * len(index),
            "high": [max(open_px, close_px) + 1.0] * len(index),
            "low": [min(open_px, close_px) - 1.0] * len(index),
            "close": [close_px] * len(index),
            "volume": [1_000_000.0] * len(index),
        },
        index=index,
    )
    df.index.name = "date"
    return df


def _book() -> list[dict]:
    """Same day, one slot. The early exit is the low score and the big win."""
    return [
        {
            "ticker": "LOW",
            "entry_date": "2024-06-03",
            "exit_date": "2024-06-04",
            "net_return": 0.80,
            "signal_score": 10.0,
            "stop_pct": 0.05,
        },
        {
            "ticker": "HIGH",
            "entry_date": "2024-06-03",
            "exit_date": "2024-08-01",
            "net_return": -0.40,
            "signal_score": 90.0,
            "stop_pct": 0.05,
        },
    ]


def _simulate(trades: list[dict]) -> dict:
    return backtest._simulate_portfolio_equity(
        trades,
        starting_equity=100_000.0,
        max_concurrent_positions=1,
        position_size_pct=1.0,
        risk_per_trade_pct=0.0,
    )


def _admitted(trades: list[dict]) -> list[str]:
    return [row["ticker"] for row in _simulate(trades)["admitted"]]


def test_full_book_keeps_the_higher_score_not_the_earlier_exit() -> None:
    assert _admitted(_book()) == ["HIGH"]


def test_selection_is_unchanged_when_exits_and_outcomes_are_permuted() -> None:
    original = _admitted(_book())
    swapped = _book()
    swapped[0]["exit_date"], swapped[1]["exit_date"] = swapped[1]["exit_date"], swapped[0]["exit_date"]
    swapped[0]["net_return"], swapped[1]["net_return"] = swapped[1]["net_return"], swapped[0]["net_return"]
    assert _admitted(swapped) == original == ["HIGH"]

    tied = [
        {
            "ticker": "ZZZ",
            "entry_date": "2024-06-03",
            "exit_date": "2024-06-04",
            "net_return": 0.50,
            "signal_score": 40.0,
            "stop_pct": 0.05,
        },
        {
            "ticker": "AAA",
            "entry_date": "2024-06-03",
            "exit_date": "2024-09-01",
            "net_return": -0.20,
            "signal_score": 40.0,
            "stop_pct": 0.05,
        },
    ]
    assert _admitted(tied) == ["AAA"]
    tied[0]["exit_date"], tied[1]["exit_date"] = tied[1]["exit_date"], tied[0]["exit_date"]
    tied[0]["net_return"], tied[1]["net_return"] = tied[1]["net_return"], tied[0]["net_return"]
    assert _admitted(tied) == ["AAA"]


def test_multi_era_aggregate_does_not_prefer_the_early_exit(monkeypatch) -> None:
    monkeypatch.setattr("config.get_backtest_portfolio_max_positions", lambda *_a, **_k: 1)
    monkeypatch.setattr("config.get_backtest_position_size_pct", lambda *_a, **_k: 1.0)
    monkeypatch.setattr("config.get_backtest_portfolio_starting_equity", lambda *_a, **_k: 100_000.0)
    monkeypatch.setattr("config.get_backtest_risk_per_trade_pct", lambda *_a, **_k: 0.0)
    row = _aggregate_era(
        name="crash_recovery",
        start_date="2020-01-01",
        end_date="2021-12-31",
        universe_size=2,
        chunk_payloads=[
            {
                "excluded_count": 0,
                "data_integrity": {},
                "trades": _book(),
            }
        ],
    )
    # Full equity on the high-score name (-40%), not the early-exit winner (+80%).
    assert row["total_return_net_pct"] == pytest.approx(-40.0)


def test_entry_fill_default_and_legacy_alias(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("BACKTEST_ENTRY_FILL", raising=False)
    assert get_backtest_entry_fill(tmp_path) == "next_open"
    monkeypatch.setenv("BACKTEST_ENTRY_FILL", "same_close")
    assert get_backtest_entry_fill(tmp_path) == "same_close"
    monkeypatch.setenv("BACKTEST_ENTRY_FILL", "market")
    assert get_backtest_entry_fill(tmp_path) == "next_open"


def test_next_open_uses_the_next_bar_and_skips_a_missing_one() -> None:
    idx = pd.bdate_range("2024-01-02", periods=3)
    df = _ohlc(idx, open_px=50.0, close_px=40.0)
    missed = backtest.resolve_entry_fill(df, 2, "next_open")
    assert missed is None
    filled = backtest.resolve_entry_fill(df, 0, "next_open")
    assert filled is not None
    assert filled["fill_idx"] == 1
    assert filled["entry_price"] == 50.0
    assert filled["entry_price"] != float(df["close"].iloc[0])
    legacy = backtest.resolve_entry_fill(df, 0, "same_close")
    assert legacy is not None
    assert legacy["fill_idx"] == 0
    assert legacy["entry_price"] == 40.0
    after_end = backtest.resolve_entry_fill(df, 1, "next_open", era_end="2024-01-02")
    assert after_end is None


def test_costs_apply_to_the_fill_price() -> None:
    net, ctx = backtest._net_return_after_costs(
        entry_price=50.0,
        exit_price=40.0,
        qty=10,
        slippage_bps_per_side=0.0,
        fee_per_share=0.0,
        min_fee_per_order=0.0,
    )
    assert net == pytest.approx((40.0 - 50.0) / 50.0)
    assert ctx["gross_return"] == pytest.approx(net)


def test_january_is_eligible_only_when_warmup_is_present() -> None:
    warmed = pd.bdate_range("2018-11-07", "2020-06-30")
    first = backtest.first_replay_session(_ohlc(warmed), "2020-01-01", "2020-12-31")
    assert first is not None
    assert first <= pd.Timestamp("2020-01-06")
    assert backtest.sessions_before_start(_ohlc(warmed), "2020-01-01") >= backtest.INDICATOR_WARMUP_SESSIONS

    bare = pd.bdate_range("2020-01-02", "2020-12-31")
    bare_first = backtest.first_replay_session(_ohlc(bare), "2020-01-01", "2020-12-31")
    assert bare_first == pd.Timestamp(bare[backtest.REPLAY_MIN_BAR_INDEX])
    assert bare_first.month == 10
    assert (datetime(2020, 1, 1) - timedelta(days=backtest.HISTORY_WARMUP_DAYS)).strftime("%Y-%m-%d") == "2018-11-07"


def _capture_history(monkeypatch, *, extended: bool) -> dict:
    captured: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            now_ms = int(datetime(2020, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
            day_ms = 24 * 60 * 60 * 1000
            candles = []
            for i in range(3):
                candles.append(
                    {
                        "datetime": now_ms - (2 - i) * day_ms,
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1000.0,
                    }
                )
            return {"candles": candles}

    def _capture(*_args, **kwargs):
        captured["params"] = kwargs.get("params")
        return _Resp()

    monkeypatch.setattr(market_data, "_request_with_backoff", _capture)
    market_data.get_daily_history_with_meta(
        "AAPL",
        auth=object(),
        start_date="2018-11-07",
        end_date="2021-12-31",
        extended_history=extended,
    )
    return captured["params"]


def test_replay_history_requests_twenty_years_of_daily_bars(monkeypatch) -> None:
    params = _capture_history(monkeypatch, extended=True)
    assert params["periodType"] == "year"
    assert params["period"] == 20
    assert params["frequencyType"] == "daily"
    assert params["frequency"] == 1
    assert "startDate" in params and "endDate" in params


def test_live_history_request_stays_on_month(monkeypatch) -> None:
    params = _capture_history(monkeypatch, extended=False)
    assert params["periodType"] == "month"
    assert "period" not in params
    assert "frequency" not in params


def test_backtest_fetch_opts_into_extended_history(monkeypatch) -> None:
    seen: dict = {}

    def fake(*_args, **kwargs):
        seen.update(kwargs)
        idx = pd.bdate_range("2020-01-02", periods=3)
        return _ohlc(idx), {"provider": "schwab", "used_fallback": False, "fallback_reason": None, "rows": 3}

    monkeypatch.setattr("market_data.get_daily_history_with_meta", fake)
    _frame, meta = backtest._fetch_history_with_meta("AAPL", "2018-11-07", "2021-12-31")
    assert seen.get("extended_history") is True
    assert meta["provider"] == "schwab"
    assert not _frame.empty
