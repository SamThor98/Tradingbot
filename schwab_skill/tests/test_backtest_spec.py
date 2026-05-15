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
            "overrides": {
                "skip_mirofish": True,
                "quality_gates_mode": "soft",
                "backtest_portfolio_starting_equity": 150000,
                "backtest_portfolio_max_positions": 12,
                "backtest_position_size_pct": 0.06,
                "backtest_risk_per_trade_pct": 0.01,
                "adaptive_stop_enabled": False,
                "adaptive_stop_base_pct": 0.08,
                "backtest_adaptive_guardrails_enabled": True,
                "meta_policy_mode": "shadow",
                "event_risk_mode": "live",
                "exit_manager_mode": "off",
                "exec_quality_mode": "shadow",
            },
        }
    )
    env = s.env_overrides_merged()
    assert env is not None
    assert env.get("BACKTEST_SKIP_MIROFISH") == "1"
    assert env.get("QUALITY_GATES_MODE") == "soft"
    assert env.get("BACKTEST_PORTFOLIO_STARTING_EQUITY") == "150000.0"
    assert env.get("BACKTEST_PORTFOLIO_MAX_POSITIONS") == "12"
    assert env.get("BACKTEST_POSITION_SIZE_PCT") == "0.06"
    assert env.get("BACKTEST_RISK_PER_TRADE_PCT") == "0.01"
    assert env.get("ADAPTIVE_STOP_ENABLED") == "false"
    assert env.get("ADAPTIVE_STOP_BASE_PCT") == "0.08"
    assert env.get("BACKTEST_ADAPTIVE_GUARDRAILS_ENABLED") == "true"
    assert env.get("META_POLICY_MODE") == "shadow"
    assert env.get("EVENT_RISK_MODE") == "live"
    assert env.get("EXIT_MANAGER_MODE") == "off"
    assert env.get("EXEC_QUALITY_MODE") == "shadow"
