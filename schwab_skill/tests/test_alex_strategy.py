from __future__ import annotations

import pandas as pd

from core.alex_strategy import (
    AlexParams,
    add_alex_indicators,
    evaluate_alex_entry,
    load_alex_universe,
    run_alex_backtest,
    simulate_alex_exit,
    size_multiplier,
)


def _ohlcv(closes: list[float], *, start: str = "2020-01-02") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="B")
    s = pd.Series(closes, index=idx, dtype=float)
    prev = s.shift(1).fillna(s.iloc[0])
    return pd.DataFrame(
        {
            "open": prev.to_numpy(),
            "high": (s * 1.01).to_numpy(),
            "low": (s * 0.99).to_numpy(),
            "close": s.to_numpy(),
            "volume": 1_000_000.0,
        },
        index=idx,
    )


def _oversold_path(n: int = 80) -> list[float]:
    """Steady 100s then a sharp dump so RSI<=30 and close is >=5% below SMA20."""
    body = [100.0] * (n - 12)
    dump = [100.0 * (0.97**i) for i in range(1, 13)]
    return body + dump


def test_universe_has_717_non_it_tickers() -> None:
    mapping = load_alex_universe()
    assert len(mapping) == 717
    assert "AAPL" not in mapping
    assert mapping["JPM"] == "Financials"
    assert mapping["PLD"] == "Real Estate"
    assert mapping["XOM"] == "Energy"


def test_size_multiplier_takes_lower_of_loss_and_drawdown() -> None:
    assert size_multiplier(consecutive_losses=0, drawdown_pct=0.0) == 1.0
    assert size_multiplier(consecutive_losses=4, drawdown_pct=0.05) == 0.75
    assert size_multiplier(consecutive_losses=4, drawdown_pct=0.22) == 0.50
    assert size_multiplier(consecutive_losses=9, drawdown_pct=0.30) == 0.25


def test_entry_requires_rsi_and_sma_gap() -> None:
    params = AlexParams()
    df = add_alex_indicators(_ohlcv(_oversold_path()))
    row = df.iloc[-1]
    ok, reason = evaluate_alex_entry(
        row,
        sector="Energy",
        params=params,
        spy_roc=0.0,
        shy_roc=0.0,
        in_cooldown=False,
    )
    assert ok, reason
    assert float(row["rsi_14"]) <= 30.0
    assert (float(row["sma_20"]) - float(row["close"])) / float(row["sma_20"]) >= 0.05


def test_it_sector_and_guards_block() -> None:
    params = AlexParams()
    df = add_alex_indicators(_ohlcv(_oversold_path()))
    row = df.iloc[-1]
    ok, reason = evaluate_alex_entry(
        row, sector="Information Technology", params=params, spy_roc=0.0, shy_roc=0.0, in_cooldown=False
    )
    assert not ok and reason == "it_excluded"
    ok, reason = evaluate_alex_entry(row, sector="Energy", params=params, spy_roc=-0.02, shy_roc=0.0, in_cooldown=False)
    assert not ok and reason == "spy_roc_guard"
    ok, reason = evaluate_alex_entry(
        row, sector="Financials", params=params, spy_roc=0.0, shy_roc=-0.02, in_cooldown=False
    )
    assert not ok and reason == "shy_roc_guard"
    ok, reason = evaluate_alex_entry(row, sector="Energy", params=params, spy_roc=0.0, shy_roc=-0.02, in_cooldown=False)
    assert ok, reason


def test_stop_beats_target_on_same_bar() -> None:
    params = AlexParams()
    closes = [100.0] * 40 + [90.0]
    df = add_alex_indicators(_ohlcv(closes))
    entry_idx = 39
    entry_price = 100.0
    atr = 2.0
    df.iloc[40, df.columns.get_loc("low")] = 90.0
    df.iloc[40, df.columns.get_loc("high")] = 120.0
    out = simulate_alex_exit(df, entry_idx, entry_price=entry_price, atr=atr, params=params)
    assert out["exit_reason"] == "stop"


def test_target_and_time_and_rsi_exits() -> None:
    params = AlexParams(max_hold_days=7, rsi_exit=50.0)
    base = [100.0] * 40

    target_df = add_alex_indicators(_ohlcv(base + [103.0]))
    target_df.iloc[40, target_df.columns.get_loc("high")] = 110.0
    target_df.iloc[40, target_df.columns.get_loc("low")] = 102.0
    tgt = simulate_alex_exit(target_df, 39, entry_price=100.0, atr=2.0, params=params)
    assert tgt["exit_reason"] == "target"

    time_df = add_alex_indicators(_ohlcv(base + [100.5] * 8))
    time_df["low"] = time_df["close"] * 0.999
    time_df["high"] = time_df["close"] * 1.001
    time_df["rsi_14"] = 40.0
    time_out = simulate_alex_exit(time_df, 39, entry_price=100.0, atr=2.0, params=params)
    assert time_out["exit_reason"] == "time_exit"
    assert time_out["bars_held"] == 7

    rsi_df = add_alex_indicators(_ohlcv(base + [100.2]))
    rsi_df.iloc[40, rsi_df.columns.get_loc("rsi_14")] = 55.0
    rsi_df.iloc[40, rsi_df.columns.get_loc("high")] = 100.3
    rsi_df.iloc[40, rsi_df.columns.get_loc("low")] = 100.1
    rsi_out = simulate_alex_exit(rsi_df, 39, entry_price=100.0, atr=2.0, params=params)
    assert rsi_out["exit_reason"] == "rsi_exit"


def _guard_series(n: int, start: str = "2020-01-02") -> pd.DataFrame:
    """Mild uptrend so SPY/SHY ROC guards stay open."""
    closes = [100.0 + i * 0.05 for i in range(n)]
    return add_alex_indicators(_ohlcv(closes, start=start))


def test_portfolio_ranks_lowest_rsi_when_slots_are_one() -> None:
    params = AlexParams(max_positions=1, starting_equity=50_000.0, entry_mode="close", commission_per_trade=0.0)
    n = 80
    dump_a = _oversold_path(n)
    dump_b = [100.0] * (n - 12) + [100.0 * (0.96**i) for i in range(1, 13)]
    a = add_alex_indicators(_ohlcv(dump_a))
    b = add_alex_indicators(_ohlcv(dump_b))
    spy = _guard_series(n)
    universe = {"AAA": "Energy", "BBB": "Energy"}
    result = run_alex_backtest(
        {"AAA": a, "BBB": b},
        universe=universe,
        spy=spy,
        shy=spy,
        params=params,
    )
    assert result["total_trades"] >= 1
    first = result["trades"][0]
    assert first["ticker"] == "BBB"


def test_cooldown_blocks_immediate_reentry() -> None:
    params = AlexParams(
        max_positions=1,
        starting_equity=50_000.0,
        entry_mode="close",
        commission_per_trade=0.0,
        cooldown_days=7,
        max_hold_days=1,
    )
    n = 90
    # Repeated dumps so many signals fire; cooldown should thin fills.
    closes = [100.0] * 40
    for _ in range(10):
        closes.extend([100.0 * (0.97**i) for i in range(1, 6)])
    df = add_alex_indicators(_ohlcv(closes[:n] if len(closes) >= n else closes + [closes[-1]] * (n - len(closes))))
    spy = _guard_series(len(df))
    result = run_alex_backtest(
        {"AAA": df},
        universe={"AAA": "Energy"},
        spy=spy,
        shy=spy,
        params=params,
    )
    assert result["diagnostics"]["blocked_cooldown"] >= 1
    dates = [t["entry_date"] for t in result["trades"]]
    assert dates == sorted(dates)
    for prev, nxt in zip(result["trades"], result["trades"][1:]):
        gap = (pd.Timestamp(nxt["entry_date"]) - pd.Timestamp(prev["exit_date"])).days
        assert gap >= 7


def test_era_window_excludes_trades_outside_end_date() -> None:
    params = AlexParams(max_positions=1, starting_equity=50_000.0, entry_mode="close", commission_per_trade=0.0)
    n = 80
    df = add_alex_indicators(_ohlcv(_oversold_path(n)))
    spy = _guard_series(n)
    full = run_alex_backtest({"AAA": df}, universe={"AAA": "Energy"}, spy=spy, shy=spy, params=params)
    assert full["total_trades"] >= 1
    cutoff = df.index[50]
    clipped = run_alex_backtest(
        {"AAA": df},
        universe={"AAA": "Energy"},
        spy=spy,
        shy=spy,
        params=params,
        end_date=cutoff.strftime("%Y-%m-%d"),
    )
    for trade in clipped["trades"]:
        assert pd.Timestamp(trade["entry_date"]) <= cutoff
