"""Gross exposure in the portfolio replay stays inside current equity.

Equal-weight percent profit factor can sit above 1 while a book that stacks
a full equity allocation on every open name falls 67% to 97%. That stack was
the multi-era aggregator's account return. It is not a dollar profit factor.
"""

from __future__ import annotations

from datetime import date, timedelta

from backtest import _simulate_portfolio_equity


def _book(*, stop: float, n: int = 400, seed: int = 0) -> list[dict]:
    import random

    rng = random.Random(seed)
    start = date(2018, 1, 2)
    trades = []
    for i in range(n):
        entry = start + timedelta(days=i // 6)
        # About a 1.15 percent profit factor: wins a bit larger than losses.
        ret = rng.uniform(0.03, 0.18) if rng.random() < 0.45 else -rng.uniform(0.03, 0.12)
        trades.append(
            {
                "entry_date": entry.isoformat(),
                "exit_date": (entry + timedelta(days=20)).isoformat(),
                "net_return": ret,
                "stop_pct": stop,
            }
        )
    return trades


def _percent_pf(trades: list[dict]) -> float:
    wins = sum(t["net_return"] for t in trades if t["net_return"] > 0)
    losses = abs(sum(t["net_return"] for t in trades if t["net_return"] <= 0))
    return wins / losses


def test_overlapping_full_size_trades_cannot_deploy_more_than_equity() -> None:
    trades = _book(stop=0.0005, seed=0)
    assert _percent_pf(trades) > 1.0
    out = _simulate_portfolio_equity(
        trades,
        starting_equity=100_000.0,
        max_concurrent_positions=10,
        position_size_pct=0.05,
        risk_per_trade_pct=0.0075,
    )
    assert out["gross_capped_at_equity"] is True
    assert out["total_return_net_pct"] > -50.0
    assert out["ending_equity"] > 0
    assert out["profit_factor_dollar"] is not None


def test_two_overlapping_positions_share_one_equity_budget() -> None:
    trades = [
        {
            "entry_date": "2024-01-02",
            "exit_date": "2024-01-20",
            "net_return": 0.10,
            "stop_pct": 0.01,
        },
        {
            "entry_date": "2024-01-03",
            "exit_date": "2024-01-21",
            "net_return": -0.10,
            "stop_pct": 0.01,
        },
    ]
    out = _simulate_portfolio_equity(
        trades,
        starting_equity=100_000.0,
        max_concurrent_positions=10,
        position_size_pct=1.0,
        risk_per_trade_pct=0.0075,
    )
    # 0.75% risk / 1% stop wants 75% each. The second name only gets the remaining 25%.
    assert out["risk_sized_count"] == 2
    assert out["capacity_filtered"] == 0
    # 75% * +10% + 25% * -10% = +5% of equity.
    assert out["total_return_net_pct"] == 5.0
