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


def test_universe_overrides_drive_focused_scan() -> None:
    """API callers can still post focused-mode via strategy_overrides.
    Scan studio also exposes universe_preset=focused as a first-class choice.
    """
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


def test_parse_named_universe_preset() -> None:
    out = parse_scan_run_body({"universe_preset": "nasdaq100", "scan_timeframe": "daily"})
    assert out["universe_preset"] == "nasdaq100"
    assert out["scan_timeframe"] == "daily"
    assert "trend_breakout" in out["strategy_ids"]
    skw = scan_runtime_kwargs(out)
    assert skw["watchlist_override"] is None
    assert skw["universe_preset"] == "nasdaq100"


def test_parse_custom_preset_requires_tickers() -> None:
    with pytest.raises(ValueError):
        parse_scan_run_body({"universe_preset": "custom"})
    out = parse_scan_run_body({"universe_preset": "custom", "tickers": ["AAPL"]})
    assert out["universe_mode"] == "tickers"
    assert scan_runtime_kwargs(out)["watchlist_override"] == ["AAPL"]


def test_breakout_confirm_strategy_sets_env() -> None:
    out = parse_scan_run_body({"strategy_ids": ["breakout_confirm"], "scan_timeframe": "intraday"})
    assert out["env_overrides"].get("BREAKOUT_CONFIRM_ENABLED") == "true"


def test_rejects_unavailable_russell() -> None:
    with pytest.raises(ValueError):
        parse_scan_run_body({"universe_preset": "russell2000"})
