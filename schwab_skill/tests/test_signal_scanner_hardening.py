from __future__ import annotations

import sys
import types
from typing import Any

import pandas as pd

import signal_scanner


class _DoneFuture:
    def __init__(self, value: Any):
        self._value = value

    def result(self):
        return self._value

    def done(self) -> bool:
        return True


class _PendingFuture:
    def result(self):
        raise RuntimeError("pending")

    def done(self) -> bool:
        return False


def _sample_df() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=300, freq="D")
    df = pd.DataFrame(
        {
            "open": [100.0] * len(idx),
            "high": [101.0] * len(idx),
            "low": [99.0] * len(idx),
            "close": [100.0] * len(idx),
            "volume": [1_000_000.0] * len(idx),
            "sma_50": [100.0] * len(idx),
            "sma_200": [100.0] * len(idx),
        },
        index=idx,
    )
    df.index.name = "date"
    return df


def _install_common_modules(monkeypatch, *, regime_fn):
    monkeypatch.setitem(sys.modules, "notifier", types.SimpleNamespace(send_alert=lambda *_a, **_k: None))
    monkeypatch.setitem(
        sys.modules,
        "schwab_auth",
        types.SimpleNamespace(DualSchwabAuth=lambda *args, **kwargs: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "data_health",
        types.SimpleNamespace(
            assess_scan_session_data_health=lambda *_a, **_k: {"data_quality": "ok", "reasons": []}
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "sector_strength",
        types.SimpleNamespace(
            is_market_regime_bullish=regime_fn,
            get_winning_sector_etfs=lambda *_a, **_k: {"XLK"},
            get_regime_v2_snapshot=lambda *_a, **_k: {"score": 99.0, "bucket": "high"},
            get_unresolved_sector_symbols=lambda **_k: [],
        ),
    )
    monkeypatch.setattr(signal_scanner, "_record_quality_snapshot", lambda *_a, **_k: None)


def test_regime_failure_fails_closed_by_default(monkeypatch) -> None:
    _install_common_modules(monkeypatch, regime_fn=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("regime down")))
    signals, diagnostics = signal_scanner.scan_for_signals_detailed(
        skill_dir=signal_scanner.SKILL_DIR,
        watchlist_override=[],
    )
    assert signals == []
    assert diagnostics["scan_blocked"] == 1
    assert diagnostics["scan_blocked_reason"] == "regime_check_failed_data_unavailable"
    assert diagnostics["regime_check_failed"] == 1


def test_regime_failure_can_fail_open_with_env_override(monkeypatch) -> None:
    _install_common_modules(monkeypatch, regime_fn=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("regime down")))
    signals, diagnostics = signal_scanner.scan_for_signals_detailed(
        skill_dir=signal_scanner.SKILL_DIR,
        env_overrides={"RISK_FAIL_CLOSED_ON_DATA_OUTAGE": "false"},
        watchlist_override=[],
    )
    assert signals == []
    assert diagnostics["scan_blocked"] == 0
    assert diagnostics["regime_check_failed"] == 1
    assert diagnostics["regime_fail_closed_mode"] is False


def test_stage_a_timeout_accounting(monkeypatch) -> None:
    _install_common_modules(monkeypatch, regime_fn=lambda *_a, **_k: (True, {"spy_price": 500.0, "spy_sma_200": 490.0}))

    class _Exec:
        def __init__(self, max_workers=1):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, _fn, *_args, **_kwargs):
            return _PendingFuture()

    monkeypatch.setattr(signal_scanner.cf, "ThreadPoolExecutor", _Exec)
    monkeypatch.setattr(
        signal_scanner.cf,
        "as_completed",
        lambda *_a, **_k: (_ for _ in ()).throw(signal_scanner.cf.TimeoutError()),
    )

    _signals, diagnostics = signal_scanner.scan_for_signals_detailed(
        skill_dir=signal_scanner.SKILL_DIR,
        watchlist_override=["AAPL", "MSFT"],
    )
    assert diagnostics["stage_a_timeouts"] == 2
    assert diagnostics["exceptions"] >= 2


def test_stage_b_timeout_accounting(monkeypatch) -> None:
    _install_common_modules(monkeypatch, regime_fn=lambda *_a, **_k: (True, {"spy_price": 500.0, "spy_sma_200": 490.0}))
    sample_df = _sample_df()

    def _stage_a_ok(ticker, *_a, **_k):
        return {
            "ok": True,
            "candidate": {
                "ticker": ticker,
                "df": sample_df,
                "price": 100.0,
                "sector_etf": "XLK",
                "sma_50": 100.0,
                "sma_200": 99.0,
                "latest_volume": 1_100_000.0,
                "avg_vol_50": 1_000_000.0,
                "breakout_confirmed": True,
                "stage_a_score": 80.0,
                "data_provider": "schwab",
                "data_provider_primary": True,
                "used_fallback_data": False,
                "fallback_reason": "",
            },
        }

    monkeypatch.setattr(signal_scanner, "_scan_stage_a_one", _stage_a_ok)

    class _Exec:
        def __init__(self, max_workers=1):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args, **kwargs):
            if fn is signal_scanner._scan_stage_a_one:
                return _DoneFuture(fn(*args, **kwargs))
            return _PendingFuture()

    calls = {"n": 0}

    def _as_completed(fs, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return iter(list(fs))
        raise signal_scanner.cf.TimeoutError()

    monkeypatch.setattr(signal_scanner.cf, "ThreadPoolExecutor", _Exec)
    monkeypatch.setattr(signal_scanner.cf, "as_completed", _as_completed)

    _signals, diagnostics = signal_scanner.scan_for_signals_detailed(
        skill_dir=signal_scanner.SKILL_DIR,
        watchlist_override=["AAPL"],
    )
    assert diagnostics["stage_b_timeouts"] == 1
    assert diagnostics["stage_b_exceptions"] >= 1
    assert diagnostics["exceptions"] >= 1


def test_exception_and_fallback_accounting(monkeypatch) -> None:
    _install_common_modules(monkeypatch, regime_fn=lambda *_a, **_k: (True, {"spy_price": 500.0, "spy_sma_200": 490.0}))
    sample_df = _sample_df()

    def _stage_a_mixed(ticker, *_a, **_k):
        if ticker == "AAPL":
            return {
                "ok": False,
                "reason": "exceptions",
                "error": "AAPL exploded",
                "provider": "schwab",
                "used_fallback": True,
                "fallback_reason": "unexpected",
            }
        return {
            "ok": False,
            "reason": "stage2_fail",
            "provider": "yfinance",
            "used_fallback": True,
            "fallback_reason": "",
            "candidate": {
                "ticker": ticker,
                "df": sample_df,
                "data_provider": "yfinance",
                "used_fallback_data": True,
                "fallback_reason": "",
            },
        }

    monkeypatch.setattr(signal_scanner, "_scan_stage_a_one", _stage_a_mixed)

    class _Exec:
        def __init__(self, max_workers=1):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args, **kwargs):
            return _DoneFuture(fn(*args, **kwargs))

    monkeypatch.setattr(signal_scanner.cf, "ThreadPoolExecutor", _Exec)
    monkeypatch.setattr(signal_scanner.cf, "as_completed", lambda fs, timeout=None: iter(list(fs)))

    _signals, diagnostics = signal_scanner.scan_for_signals_detailed(
        skill_dir=signal_scanner.SKILL_DIR,
        watchlist_override=["AAPL", "MSFT"],
    )
    assert diagnostics["exceptions"] >= 1
    assert diagnostics["fallback_reason_missing_count"] >= 1
    assert diagnostics["fallback_inconsistent_count"] >= 1
