"""Overlay exit-manager mode must beat EXIT_MANAGER_MODE.

``BacktestIntelligenceConfig.all_off()`` sets ``exit_manager`` to ``off``.
A backtest that passes that overlay must plan zero exit-manager exits even
when the process env (or the default) is ``live``. The same planner must
still plan exits when the overlay itself is ``live``.
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


def _price_df(prices: list[float], volume: float = 1_000_000.0) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=len(prices), freq="B")
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [volume] * len(prices),
            "avg_vol_50": [volume] * len(prices),
            "sma_50": prices,
        },
        index=idx,
    )


def _install_entry_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admit one synthetic name so the exit planner actually runs."""
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
    monkeypatch.setattr(backtest, "get_backtest_portfolio_max_positions", lambda _sd: 10)
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


def _run(monkeypatch: pytest.MonkeyPatch, overlay: BacktestIntelligenceConfig | None) -> dict:
    _install_entry_patches(monkeypatch)
    df = _price_df([100.0] * 230)
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
    kwargs: dict = {
        "tickers": ["AAA"],
        "start_date": "2020-01-01",
        "end_date": "2020-12-31",
        "include_all_trades": True,
    }
    if overlay is not None:
        kwargs["intelligence_overlay"] = overlay
    return backtest._run_backtest_core(**kwargs)


def _counts(result: dict) -> tuple[int, int, int]:
    diag = result["diagnostics"]
    entries = int(diag.get("entries", 0) or 0)
    planned = int(diag.get("exit_manager_simulated", 0) or 0)
    applied = int(diag.get("exits_exit_manager", 0) or 0)
    return entries, planned, applied


def test_overlay_all_off_plans_zero_exits_when_env_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXIT_MANAGER_MODE", "live")
    result = _run(monkeypatch, BacktestIntelligenceConfig.all_off())
    entries, planned, applied = _counts(result)
    assert result["intelligence_overlay"]["exit_manager"] == "off"
    assert entries > 0, result["diagnostics"]
    assert planned == 0
    assert applied == 0


def test_overlay_live_still_plans_exits_when_env_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXIT_MANAGER_MODE", "live")
    overlay = BacktestIntelligenceConfig(exit_manager="live")
    result = _run(monkeypatch, overlay)
    entries, planned, _applied = _counts(result)
    assert result["intelligence_overlay"]["exit_manager"] == "live"
    assert entries > 0, result["diagnostics"]
    assert planned == entries
    assert planned > 0


def test_no_overlay_still_follows_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXIT_MANAGER_MODE", "off")
    off_result = _run(monkeypatch, None)
    off_entries, off_planned, off_applied = _counts(off_result)
    assert off_entries > 0, off_result["diagnostics"]
    assert off_planned == 0
    assert off_applied == 0

    monkeypatch.setenv("EXIT_MANAGER_MODE", "live")
    live_result = _run(monkeypatch, None)
    live_entries, live_planned, _live_applied = _counts(live_result)
    assert live_entries > 0, live_result["diagnostics"]
    assert live_planned == live_entries


def _rising_frame() -> pd.DataFrame:
    prices = [100.0, 102.0, 105.0, 108.0, 109.0, 102.0, 99.0, 98.0]
    idx = pd.bdate_range("2024-01-02", periods=len(prices))
    return pd.DataFrame({"close": prices}, index=idx)


def test_planner_uses_overlay_mode_instead_of_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EXIT_MANAGER_MODE", "live")
    df = _rising_frame()

    off_diag: dict[str, int] = {}
    off_idx, off_reason = backtest._plan_exit_manager_for_entry(
        df,
        0,
        10,
        0.05,
        tmp_path,
        off_diag,
        mode=BacktestIntelligenceConfig.all_off().exit_manager,
    )
    assert off_idx is None and off_reason is None
    assert int(off_diag.get("exit_manager_simulated", 0) or 0) == 0

    live_diag: dict[str, int] = {}
    live_idx, live_reason = backtest._plan_exit_manager_for_entry(
        df,
        0,
        10,
        0.05,
        tmp_path,
        live_diag,
        mode=BacktestIntelligenceConfig(exit_manager="live").exit_manager,
    )
    assert live_idx is not None
    assert live_reason
    assert int(live_diag.get("exit_manager_simulated", 0) or 0) == 1
