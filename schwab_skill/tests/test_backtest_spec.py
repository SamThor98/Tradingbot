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


def test_parse_named_universe_and_strategy_ids() -> None:
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "watchlist",
            "universe_preset": "nasdaq100",
            "strategy_ids": ["donchian_20"],
            "scan_timeframe": "daily",
            "start_date": "2018-01-01",
            "end_date": "2023-12-31",
            "overrides": {
                "stage2_52w_pct": 0.9,
                "stage2_sma_upward_days": 25,
                "vcp_days": 6,
                "quality_min_signal_score": 55,
                "backtest_hold_days": 50,
                "backtest_min_hold_days_before_trail": 20,
                "rank_filter_shadow_min_percentile_rank_v2": 68,
            },
        }
    )
    assert s.universe_preset == "nasdaq100"
    assert s.universe_mode == "watchlist"
    assert s.strategy_ids == ["donchian_20"]
    assert s.scan_timeframe == "daily"
    env = s.env_overrides_merged()
    assert env is not None
    assert env["STAGE2_52W_PCT"] == "0.9"
    assert env["STAGE2_SMA_UPWARD_DAYS"] == "25"
    assert env["VCP_DAYS"] == "6"
    assert env["QUALITY_MIN_SIGNAL_SCORE"] == "55"
    assert env["BACKTEST_HOLD_DAYS"] == "50"
    assert env["BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL"] == "20"
    assert env["RANK_FILTER_SHADOW_MIN_PERCENTILE_RANK_V2"] == "68"
    prev = spec_preview_dict(s)
    assert prev["universe_preset"] == "nasdaq100"
    assert prev["strategy_ids"] == ["donchian_20"]


def test_parse_legacy_watchlist_defaults_to_sp1500() -> None:
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "watchlist",
            "tickers": [],
            "start_date": "2018-01-01",
            "end_date": "2023-12-31",
        }
    )
    assert s.universe_preset == "sp1500"
    assert s.strategy_ids == []


def test_rejects_unwired_universe() -> None:
    with pytest.raises(ValueError, match="not available"):
        parse_strategy_spec(
            {
                "schema_version": 1,
                "universe_mode": "watchlist",
                "universe_preset": "russell2000",
                "start_date": "2018-01-01",
                "end_date": "2023-12-31",
            }
        )


def test_breakout_confirm_strategy_sets_env() -> None:
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "watchlist",
            "universe_preset": "sp500",
            "strategy_ids": ["breakout_confirm"],
            "start_date": "2018-01-01",
            "end_date": "2023-12-31",
        }
    )
    env = s.env_overrides_merged()
    assert env is not None
    assert env.get("BREAKOUT_CONFIRM_ENABLED") == "true"


def test_parse_weekly_sp1500_queue_payload() -> None:
    """Exact composer payload that 8182 rejected while uvicorn still had the old spec."""
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "watchlist",
            "universe_preset": "sp1500",
            "scan_timeframe": "weekly",
            "strategy_ids": ["weekly_swing", "weekly_reversal"],
            "start_date": "2024-09-11",
            "end_date": "2026-09-11",
            "overrides": {"stage2_sma_upward_days": 18},
        }
    )
    assert s.universe_preset == "sp1500"
    assert s.scan_timeframe == "weekly"
    assert s.strategy_ids == ["weekly_swing", "weekly_reversal"]
    env = s.env_overrides_merged()
    assert env is not None
    assert env["STAGE2_SMA_UPWARD_DAYS"] == "18"
    assert env["SIGNAL_UNIVERSE_MODE"] == "broad"


def test_explicit_focus_stays_on_a_named_index() -> None:
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "watchlist",
            "universe_preset": "sp1500",
            "start_date": "2018-01-01",
            "end_date": "2023-12-31",
            "overrides": {"signal_universe_mode": "focused"},
        }
    )
    assert s.env_overrides_merged()["SIGNAL_UNIVERSE_MODE"] == "focused"


def test_weekly_timeframe_defaults_primary_strategy() -> None:
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "watchlist",
            "scan_timeframe": "weekly",
            "start_date": "2018-01-01",
            "end_date": "2023-12-31",
        }
    )
    assert s.strategy_ids == ["weekly_swing"]


def test_run_strategy_backtest_passes_catalog_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run_backtest(**kwargs):
        captured.update(kwargs)
        return {"total_trades": 0}

    monkeypatch.setattr("backtest.run_backtest", fake_run_backtest)
    s = parse_strategy_spec(
        {
            "schema_version": 1,
            "universe_mode": "watchlist",
            "universe_preset": "nasdaq100",
            "strategy_ids": ["trend_breakout"],
            "start_date": "2018-01-01",
            "end_date": "2023-12-31",
        }
    )
    from pathlib import Path

    from webapp.backtest_spec import run_strategy_backtest

    out = run_strategy_backtest(Path("."), s)
    assert out["total_trades"] == 0
    assert captured["universe_preset"] == "nasdaq100"
    assert captured["strategy_ids"] == ["trend_breakout"]
    assert captured["tickers"] is None
