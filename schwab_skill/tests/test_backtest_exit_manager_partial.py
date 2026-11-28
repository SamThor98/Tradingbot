"""Live exit-manager backtests book a 50% partial at 1.5R.

The remainder exits under the live trail, floored at breakeven after the
partial. The trade return is the size-weighted blend. Off mode keeps a
single close-to-close exit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent

if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import backtest  # noqa: E402
from backtest_intelligence import BacktestIntelligenceConfig  # noqa: E402


def _frame(prices: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(prices), freq="D")
    return pd.DataFrame({"close": prices}, index=idx)


def test_partial_triggers_at_one_and_a_half_r_and_blends(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL", "0")
    entry = 100.0
    stop = 0.05
    trigger = entry * (1.0 + 1.5 * stop)
    trail_exit = trigger - entry * stop
    prices = [entry, trigger, trail_exit, trail_exit]
    legs = backtest._walk_exit_manager_legs(_frame(prices), 0, 10, stop, tmp_path, entry_price=entry)
    assert legs["partial_idx"] == 1
    assert legs["partial_price"] == pytest.approx(trigger)
    assert legs["partial_fraction"] == pytest.approx(0.5)
    assert legs["exit_idx"] == 2
    assert legs["reason"] == "trailing_stop_after_partial"

    pos = backtest.ActivePosition(
        ticker="AAA",
        entry_idx=0,
        entry_date=pd.Timestamp("2024-01-01"),
        entry_price=entry,
        qty=10,
        day_volume=1_000_000.0,
        signal={},
        reasons=[],
        comps={},
        sector_reason="",
        stop_pct=stop,
        pm_mult=1.0,
        meta_size_mult=1.0,
        event_size_mult=1.0,
        event_policy=None,
        meta_payload=None,
        effective_slippage_bps=0.0,
        exec_info={},
        telemetry={},
        highest_close=entry,
        exit_manager_plan=legs,
    )
    blended, fields = backtest._exit_manager_trade_fill(pos, 2, trail_exit)
    expected = 0.5 * trigger + 0.5 * trail_exit
    assert blended == pytest.approx(expected)
    assert blended != pytest.approx(trail_exit)
    assert (blended - entry) / entry == pytest.approx((expected - entry) / entry)
    assert fields["exit_manager_partial_done"] is True
    assert fields["exit_manager_partial_price"] == pytest.approx(trigger)
    assert fields["exit_manager_partial_fraction"] == pytest.approx(0.5)
    assert fields["exit_manager_remainder_price"] == pytest.approx(trail_exit)
    assert fields["exit_manager_partial_date"] == pd.Timestamp("2024-01-02").isoformat()


def test_price_below_one_and_a_half_r_does_not_partial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL", "0")
    entry = 100.0
    stop = 0.05
    trigger = entry * (1.0 + 1.5 * stop)
    prices = [entry, trigger - 0.05, trigger - 0.05, trigger - 0.05]
    legs = backtest._walk_exit_manager_legs(_frame(prices), 0, 10, stop, tmp_path, entry_price=entry)
    assert legs["partial_idx"] is None
    assert legs["partial_fraction"] is None


def test_grace_blocks_partial_until_the_live_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL", raising=False)
    entry = 100.0
    stop = 0.05
    prices = [entry] * 20
    prices[10] = 130.0
    prices[16] = 130.0
    prices[17] = 120.0
    legs = backtest._walk_exit_manager_legs(_frame(prices), 0, 30, stop, tmp_path, entry_price=entry)
    assert legs["partial_idx"] == 16
    assert pd.Timestamp(legs["partial_date"]).normalize() == pd.Timestamp("2024-01-17")


def test_off_mode_does_not_plan_a_partial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EXIT_MANAGER_MODE", "live")
    monkeypatch.setenv("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL", "0")
    entry = 100.0
    stop = 0.05
    trigger = entry * (1.0 + 1.5 * stop)
    df = _frame([entry, trigger, trigger - entry * stop])
    plan: dict = {}
    idx, reason = backtest._plan_exit_manager_for_entry(
        df,
        0,
        10,
        stop,
        tmp_path,
        {},
        entry_price=entry,
        mode="off",
        plan_out=plan,
    )
    assert idx is None and reason is None
    assert plan == {}


def _portfolio_prices() -> list[float]:
    prices = [100.0] * 210
    prices[201] = 108.0
    prices[202] = 104.0
    prices[203] = 103.0
    return prices


def _install_entry_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENTRY_TIMING_SHADOW_MODE", "off")
    monkeypatch.setattr(backtest, "get_backtest_entry_fill", lambda _sd=None: "same_close")
    monkeypatch.setattr(backtest, "get_breakout_confirm_enabled", lambda _sd: False)
    monkeypatch.setattr(backtest, "get_quality_gates_mode", lambda _sd: "off")
    monkeypatch.setattr(backtest, "get_adaptive_stop_enabled", lambda _sd: False)
    monkeypatch.setattr(backtest, "get_adaptive_stop_base_pct", lambda _sd: 0.05)
    monkeypatch.setattr(backtest, "get_forensic_enabled", lambda _sd: False)
    monkeypatch.setattr(backtest, "get_forensic_filter_mode", lambda _sd: "off")
    monkeypatch.setattr(backtest, "get_forensic_cache_hours", lambda _sd: 24.0)
    monkeypatch.setattr(backtest, "get_forensic_sloan_max", lambda _sd: 1.0)
    monkeypatch.setattr(backtest, "get_forensic_beneish_max", lambda _sd: 1.0)
    monkeypatch.setattr(backtest, "get_forensic_altman_min", lambda _sd: -10.0)
    monkeypatch.setattr(backtest, "get_pead_enabled", lambda _sd: False)
    monkeypatch.setattr(backtest, "get_pead_lookback_days", lambda _sd: 7)
    monkeypatch.setattr(backtest, "get_backtest_portfolio_starting_equity", lambda _sd: 100_000.0)
    monkeypatch.setattr(backtest, "get_backtest_portfolio_max_positions", lambda _sd: 1)
    monkeypatch.setattr(backtest, "is_stage_2", lambda _window, _sd: True)
    monkeypatch.setattr(backtest, "check_vcp_volume", lambda _window, _sd: True)
    monkeypatch.setattr(backtest, "_evaluate_quality_gates", lambda _signal, _sd: [])
    monkeypatch.setattr(backtest, "_quality_mode_should_filter", lambda _reasons, _sd: False)
    monkeypatch.setattr(backtest, "_sector_filter_pass", lambda _ticker, _i, _context: (True, "sector_winning"))
    monkeypatch.setattr(backtest, "_resolve_stop_pct_for_entry", lambda _df, _i, skill_dir=None: 0.05)
    monkeypatch.setattr(
        backtest,
        "compute_signal_components",
        lambda _window, mirofish_conviction=None, mirofish_result=None: {
            "score": 70.0,
            "avg_vcp_volume_ratio": 0.55,
        },
    )
    monkeypatch.setattr(
        backtest,
        "apply_exec_quality_overlay",
        lambda slippage_bps_per_side, day_volume, qty, skill_dir, mode: (
            float(slippage_bps_per_side),
            {"regime": "normal", "effective_slippage_bps": float(slippage_bps_per_side)},
        ),
    )


def _run_portfolio(monkeypatch: pytest.MonkeyPatch, overlay: BacktestIntelligenceConfig) -> dict:
    _install_entry_patches(monkeypatch)
    prices = _portfolio_prices()
    idx = pd.date_range("2020-01-01", periods=len(prices), freq="B")
    df = pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1_000_000.0] * len(prices),
            "avg_vol_50": [1_000_000.0] * len(prices),
            "sma_50": prices,
        },
        index=idx,
    )
    ctx = backtest.BacktestContext(
        watchlist=["AAA"],
        price_data={"AAA": df},
        sector_etf_by_ticker={},
        sector_perf={},
        excluded_tickers=[],
        data_integrity={},
        history_meta_by_ticker={},
    )
    monkeypatch.setattr(backtest, "_prepare_context", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(
        backtest,
        "_run_mirofish_for_entry",
        lambda ticker, _window, skill_dir=None: {"conviction_score": 60.0},
    )
    return backtest._run_backtest_core(
        tickers=["AAA"],
        start_date="2020-01-01",
        end_date="2021-12-31",
        include_all_trades=True,
        intelligence_overlay=overlay,
    )


def test_portfolio_books_partial_blend_when_exit_manager_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXIT_MANAGER_MODE", "live")
    monkeypatch.setenv("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL", "0")
    result = _run_portfolio(monkeypatch, BacktestIntelligenceConfig(exit_manager="live"))
    trades = result["trades"]
    assert trades
    trade = trades[0]
    partial = 108.0
    remainder = 103.0
    blend = 0.5 * partial + 0.5 * remainder
    assert trade["exit_manager_partial_done"] is True
    assert trade["exit_manager_partial_price"] == pytest.approx(partial)
    assert trade["exit_manager_partial_fraction"] == pytest.approx(0.5)
    assert trade["exit_manager_remainder_price"] == pytest.approx(remainder)
    assert trade["exit_price"] == pytest.approx(blend, abs=1e-4)
    assert trade["return"] == pytest.approx((blend - 100.0) / 100.0)
    assert trade["return"] != pytest.approx((remainder - 100.0) / 100.0)
    assert result["diagnostics"]["exit_manager_partial_done"] == 1


def test_portfolio_off_mode_keeps_the_single_close(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXIT_MANAGER_MODE", "live")
    monkeypatch.setenv("EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL", "0")
    result = _run_portfolio(monkeypatch, BacktestIntelligenceConfig.all_off())
    trades = result["trades"]
    assert trades
    trade = trades[0]
    assert trade["exit_manager_partial_done"] is False
    assert "exit_manager_partial_price" not in trade
    assert trade["exit_price"] == pytest.approx(100.0)
    assert trade["return"] == pytest.approx(0.0)
    assert result["diagnostics"]["exit_manager_partial_done"] == 0
