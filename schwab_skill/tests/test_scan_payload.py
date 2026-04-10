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
