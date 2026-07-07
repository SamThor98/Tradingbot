from __future__ import annotations

import math

import pandas as pd

from core.portfolio_analytics import (
    beta_vs_benchmark,
    correlation_summary,
    daily_returns_from_prices,
    drawdown_stats,
    sharpe_ratio,
    trade_performance_pack,
    weighted_portfolio_returns,
)


def test_daily_returns_from_prices_and_weighted_portfolio_returns() -> None:
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    aaa = pd.DataFrame({"close": [100.0, 110.0, 121.0, 133.1]}, index=dates)
    bbb = pd.DataFrame({"close": [50.0, 55.0, 55.0, 60.5]}, index=dates)

    returns = pd.DataFrame(
        {
            "AAA": daily_returns_from_prices(aaa),
            "BBB": daily_returns_from_prices(bbb),
        }
    )
    portfolio = weighted_portfolio_returns({"AAA": 60.0, "BBB": 40.0}, returns)

    assert list(portfolio.round(4)) == [0.1, 0.06, 0.1]


def test_risk_metrics_handle_benchmark_alignment() -> None:
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    portfolio = pd.Series([0.01, -0.02, 0.015, 0.005, -0.004], index=dates)
    benchmark = pd.Series([0.008, -0.01, 0.01, 0.003, -0.002], index=dates)

    assert sharpe_ratio(portfolio) is not None
    assert beta_vs_benchmark(portfolio, benchmark) is not None


def test_correlation_summary_reports_max_pair_and_breaches() -> None:
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    returns = pd.DataFrame(
        {
            "AAA": [0.01, 0.02, -0.01, 0.03, 0.01],
            "BBB": [0.011, 0.021, -0.009, 0.028, 0.012],
            "CCC": [-0.02, 0.01, 0.0, -0.01, 0.02],
        },
        index=dates,
    )

    summary = correlation_summary(returns, threshold=0.9)

    assert summary["max_pair"][0:2] == ("AAA", "BBB")
    assert summary["breaches"]
    assert "AAA" in summary["matrix"]


def test_drawdown_stats_from_equity_curve() -> None:
    stats = drawdown_stats(
        [
            {"date": "2026-01-01", "equity": 100_000},
            {"date": "2026-01-02", "equity": 110_000},
            {"date": "2026-01-03", "equity": 99_000},
            {"date": "2026-01-04", "equity": 120_000},
        ]
    )

    assert stats["max_drawdown_pct"] == -10.0
    assert stats["current_drawdown_pct"] == 0.0
    assert stats["total_return_pct"] == 20.0


def test_trade_performance_pack_uses_resolved_packet_outcomes() -> None:
    rows = [
        {
            "ticker": "AAA",
            "created_at": "2026-01-01",
            "outcome": {"realized_return_pct": 5.0, "horizon_days": 10},
        },
        {
            "ticker": "BBB",
            "created_at": "2026-01-02",
            "outcome": {"realized_return_pct": -2.5, "horizon_days": 10},
        },
        {"ticker": "CCC", "created_at": "2026-01-03", "outcome": {"label": "pending"}},
    ]

    pack = trade_performance_pack(rows)

    assert pack["trades"] == 2
    assert math.isclose(pack["profit_factor"], 2.0)
    assert pack["win_rate"] == 0.5
    assert pack["expectancy_pct"] == 1.25
