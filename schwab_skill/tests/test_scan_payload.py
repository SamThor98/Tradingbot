from __future__ import annotations

import pytest

from webapp.scan_payload import parse_scan_run_body, scan_runtime_kwargs


def test_parse_empty_body() -> None:
    assert parse_scan_run_body(None) == {}
    assert parse_scan_run_body({}) == {}


def test_parse_strategy_overrides_only() -> None:
    out = parse_scan_run_body(
        {
            "strategy_overrides": {
                "breakout_confirm_enabled": False,
                "quality_gates_mode": "soft",
            }
        }
    )
    assert out["universe_mode"] is None
    assert out["tickers"] == []
    env = out["env_overrides"]
    assert env.get("BREAKOUT_CONFIRM_ENABLED") == "false"
    assert env.get("QUALITY_GATES_MODE") == "soft"
    skw = scan_runtime_kwargs(out)
    assert skw["watchlist_override"] is None
    assert skw["env_overrides"] is not None


def test_parse_ticker_universe() -> None:
    out = parse_scan_run_body(
        {
            "universe_mode": "tickers",
            "tickers": ["AAPL", "MSFT"],
        }
    )
    assert out["universe_mode"] == "tickers"
    assert out["tickers"] == ["AAPL", "MSFT"]
    skw = scan_runtime_kwargs(out)
    assert skw["watchlist_override"] == ["AAPL", "MSFT"]


def test_rejects_tickers_without_mode() -> None:
    with pytest.raises(ValueError):
        parse_scan_run_body({"universe_mode": "tickers", "tickers": []})


def test_runtime_env_overrides_are_string_values() -> None:
    out = parse_scan_run_body(
        {
            "strategy_overrides": {
                "breakout_confirm_enabled": True,
                "quality_gates_mode": "hard",
            }
        }
    )
    env = out["env_overrides"]
    assert isinstance(env, dict)
    assert env["BREAKOUT_CONFIRM_ENABLED"] == "true"
    assert env["QUALITY_GATES_MODE"] == "hard"


def test_universe_overrides_drive_test_scan() -> None:
    """The dashboard's "Test scan" button posts focused-mode overrides — verify they
    survive the validator and reach env_overrides as expected."""
    out = parse_scan_run_body(
        {
            "strategy_overrides": {
                "signal_universe_mode": "focused",
                "signal_universe_target_size": 100,
                "quality_watchlist_prefilter_enabled": False,
            }
        }
    )
    env = out["env_overrides"]
    assert env["SIGNAL_UNIVERSE_MODE"] == "focused"
    assert env["SIGNAL_UNIVERSE_TARGET_SIZE"] == "100"
    assert env["QUALITY_WATCHLIST_PREFILTER_ENABLED"] == "false"


def test_universe_target_size_bounds_enforced() -> None:
    with pytest.raises(ValueError):
        parse_scan_run_body({"strategy_overrides": {"signal_universe_target_size": 5}})
    with pytest.raises(ValueError):
        parse_scan_run_body({"strategy_overrides": {"signal_universe_target_size": 5000}})
