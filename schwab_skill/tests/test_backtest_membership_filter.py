from __future__ import annotations

import os
import sys
import types

import pandas as pd

import backtest


def _sample_history() -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=420, freq="D")
    df = pd.DataFrame(
        {
            "open": [100.0] * len(idx),
            "high": [101.0] * len(idx),
            "low": [99.0] * len(idx),
            "close": [100.0] * len(idx),
            "volume": [1_000_000.0] * len(idx),
        },
        index=idx,
    )
    df.index.name = "date"
    return df


def test_prepare_context_applies_membership_as_of_start_date(monkeypatch) -> None:
    history = _sample_history()
    monkeypatch.setattr(
        backtest,
        "_fetch_history_with_meta",
        lambda *_args, **_kwargs: (
            history.copy(),
            {"provider": "schwab", "reason": "ok", "used_fallback": False},
        ),
    )
    monkeypatch.setattr(backtest, "_load_watchlist", lambda _sd, universe_preset=None: ["AAPL", "MSFT", "TSLA"])
    monkeypatch.setitem(
        sys.modules,
        "sector_strength",
        types.SimpleNamespace(
            SECTOR_ETFS=["XLK"],
            get_ticker_sector_etf=lambda _ticker, skill_dir=None: "XLK",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sp1500_membership",
        types.SimpleNamespace(tickers_as_of=lambda as_of: frozenset({"AAPL", "TSLA"})),
    )

    ctx1 = backtest._prepare_context("2024-01-02", "2024-12-31", watchlist=None, skill_dir=backtest.SKILL_DIR)
    ctx2 = backtest._prepare_context("2024-01-02", "2024-12-31", watchlist=None, skill_dir=backtest.SKILL_DIR)

    assert ctx1.watchlist == ["AAPL", "TSLA"]
    assert ctx2.watchlist == ["AAPL", "TSLA"]
    assert ctx1.data_integrity["membership_filter_mode"] == "historical_membership_start_date"
    assert ctx1.data_integrity["membership_as_of_date"] == "2024-01-02"
    assert ctx1.data_integrity["membership_filtered_out"] == 1


def test_prepare_context_falls_back_when_membership_missing(monkeypatch) -> None:
    history = _sample_history()
    monkeypatch.setattr(
        backtest,
        "_fetch_history_with_meta",
        lambda *_args, **_kwargs: (
            history.copy(),
            {"provider": "schwab", "reason": "ok", "used_fallback": False},
        ),
    )
    monkeypatch.setattr(backtest, "_load_watchlist", lambda _sd, universe_preset=None: ["AAPL", "MSFT"])
    monkeypatch.setitem(
        sys.modules,
        "sector_strength",
        types.SimpleNamespace(
            SECTOR_ETFS=["XLK"],
            get_ticker_sector_etf=lambda _ticker, skill_dir=None: "XLK",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sp1500_membership",
        types.SimpleNamespace(tickers_as_of=lambda _as_of: None),
    )

    ctx = backtest._prepare_context("2024-01-02", "2024-12-31", watchlist=None, skill_dir=backtest.SKILL_DIR)
    assert ctx.watchlist == ["AAPL", "MSFT"]
    assert ctx.data_integrity["membership_filter_mode"] == "membership_missing_live_fallback"
    assert ctx.data_integrity["membership_file_present"] is False


def test_prepare_context_named_universe_skips_sp1500_membership(monkeypatch) -> None:
    history = _sample_history()
    monkeypatch.setattr(
        backtest,
        "_fetch_history_with_meta",
        lambda *_args, **_kwargs: (
            history.copy(),
            {"provider": "schwab", "reason": "ok", "used_fallback": False},
        ),
    )
    monkeypatch.setattr(backtest, "_load_watchlist", lambda _sd, universe_preset=None: ["QQQ", "NVDA"])
    monkeypatch.setitem(
        sys.modules,
        "sector_strength",
        types.SimpleNamespace(
            SECTOR_ETFS=["XLK"],
            get_ticker_sector_etf=lambda _ticker, skill_dir=None: "XLK",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sp1500_membership",
        types.SimpleNamespace(tickers_as_of=lambda as_of: frozenset({"AAPL"})),
    )

    ctx = backtest._prepare_context(
        "2024-01-02",
        "2024-12-31",
        watchlist=None,
        skill_dir=backtest.SKILL_DIR,
        universe_preset="nasdaq100",
    )
    assert ctx.watchlist == ["NVDA", "QQQ"]
    assert ctx.data_integrity["membership_filter_mode"] == "named_universe_live_constituents"
    assert ctx.data_integrity["universe_preset"] == "nasdaq100"


def test_legacy_stage2_admit_rejects_short_window() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {
            "open": [10.0],
            "high": [10.0],
            "low": [10.0],
            "close": [10.0],
            "volume": [1_000.0],
        }
    )
    diag: dict = {"stage2_fail": 0, "entry_family_fail": 0}
    out = backtest._evaluate_backtest_entry_admit(
        df,
        skill_dir=backtest.SKILL_DIR,
        entry_family="stage2",
        strategy_ids=None,
        day_ts=pd.Timestamp("2024-06-03"),
        diagnostics=diag,
    )
    assert out.admitted is False
    assert diag["stage2_fail"] >= 1


def test_horizon_strategy_ids_use_dual_admit_path() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {
            "open": [10.0],
            "high": [10.0],
            "low": [10.0],
            "close": [10.0],
            "volume": [1_000.0],
        }
    )
    diag: dict = {"stage2_fail": 0, "entry_family_fail": 0, "horizon_admit": 0}
    out = backtest._evaluate_backtest_entry_admit(
        df,
        skill_dir=backtest.SKILL_DIR,
        entry_family="stage2",
        strategy_ids=["donchian_20"],
        day_ts=pd.Timestamp("2024-06-03"),
        diagnostics=diag,
    )
    assert out.admitted is False
    assert out.stage2_ok is False
    assert "donchian_20" in (out.horizon_hits or []) or diag["entry_family_fail"] >= 1


def test_named_index_load_ignores_account_focus(monkeypatch) -> None:
    seen: dict[str, str | None] = {}

    def _load(_sd, universe_preset=None):
        seen["mode"] = os.environ.get("SIGNAL_UNIVERSE_MODE")
        seen["preset"] = universe_preset
        return ["AAPL"]

    history = _sample_history()
    monkeypatch.setattr(
        backtest,
        "_fetch_history_with_meta",
        lambda *_args, **_kwargs: (
            history.copy(),
            {"provider": "schwab", "reason": "ok", "used_fallback": False},
        ),
    )
    monkeypatch.setattr(backtest, "_load_watchlist", _load)
    monkeypatch.setenv("SIGNAL_UNIVERSE_MODE", "focused")
    monkeypatch.setitem(
        sys.modules,
        "sector_strength",
        types.SimpleNamespace(
            SECTOR_ETFS=["XLK"],
            get_ticker_sector_etf=lambda _ticker, skill_dir=None: "XLK",
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sp1500_membership",
        types.SimpleNamespace(tickers_as_of=lambda _as_of: None),
    )

    ctx = backtest._prepare_context(
        "2024-01-02",
        "2024-12-31",
        watchlist=None,
        skill_dir=backtest.SKILL_DIR,
        universe_preset="sp1500",
        honor_account_focus=False,
    )
    assert seen["mode"] == "broad"
    assert seen["preset"] == "sp1500"
    assert ctx.data_integrity["account_focus_honored"] is False
    assert os.environ.get("SIGNAL_UNIVERSE_MODE") == "focused"


def test_history_pad_covers_a_one_year_window() -> None:
    assert backtest._history_pad_days(None) >= backtest.MIN_BARS
    assert backtest._history_pad_days([]) >= 420
    assert backtest.replay_honors_account_focus("sp1500", None) is False
    assert backtest.replay_honors_account_focus("sp1500", {"SIGNAL_UNIVERSE_MODE": "focused"}) is True
    assert backtest.replay_honors_account_focus("focused", None) is True
