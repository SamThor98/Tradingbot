from __future__ import annotations

import pytest

from webapp.backtest_spec import parse_strategy_spec, spec_preview_dict


def test_parse_watchlist_spec() -> None:
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "watchlist",
            "tickers": [],
            "start_date": "2018-01-01",
            "end_date": "2023-12-31",
        }
    )
    assert s.universe_mode == "watchlist"
    assert s.tickers == []
    prev = spec_preview_dict(s)
    assert prev["universe_mode"] == "watchlist"


def test_parse_ticker_universe() -> None:
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "tickers",
            "tickers": ["AAPL", "msft"],
            "start_date": "2019-06-01",
            "end_date": "2024-06-01",
        }
    )
    assert s.tickers == ["AAPL", "MSFT"]


def test_rejects_short_range() -> None:
    with pytest.raises(ValueError):
        parse_strategy_spec(
            {
                "schema_version": 1,
                "universe_mode": "watchlist",
                "tickers": [],
                "start_date": "2023-01-01",
                "end_date": "2023-01-20",
            }
        )


def test_overrides_to_env() -> None:
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "watchlist",
            "tickers": [],
            "start_date": "2018-01-01",
            "end_date": "2023-12-31",
            "overrides": {"skip_mirofish": True, "quality_gates_mode": "soft"},
        }
    )
    env = s.env_overrides_merged()
    assert env is not None
    assert env.get("BACKTEST_SKIP_MIROFISH") == "1"
    assert env.get("QUALITY_GATES_MODE") == "soft"
