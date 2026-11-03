"""Wave 3 parity: regime outage, event-risk blind spot, exit-manager hook."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd


def test_regime_v2_outage_is_unavailable(monkeypatch) -> None:
    import sector_strength as ss

    def _boom(*_a, **_k):
        raise RuntimeError("spy history down")

    monkeypatch.setattr(ss, "compute_regime_v2_score_from_inputs", _boom)
    # Force the outer try to fail before a measured score exists.
    with patch("market_data.get_daily_history", side_effect=RuntimeError("spy history down")):
        snap = ss.get_regime_v2_snapshot(object(), Path("."))
    assert snap["bucket"] == "unavailable"
    assert snap["score"] is None
    assert snap["context"]["available"] is False
    assert ss.regime_v2_is_unavailable(snap) is True
    assert ss.regime_v2_is_unavailable({"score": 71.75, "bucket": "high"}) is False


def test_event_risk_schwab_only_is_unavailable(monkeypatch) -> None:
    import signal_scanner as scanner

    monkeypatch.setattr(scanner, "_nearest_earnings_distance_days", lambda _ticker: None)
    with patch("config.get_schwab_only_data", return_value=True):
        policy = scanner.evaluate_event_risk_policy("AAPL", Path("."))
    assert policy["earnings_lookup"] == "unavailable"
    assert policy["earnings_distance_days"] is None
    assert policy["earnings_near"] is False
    assert "earnings_within_" not in " ".join(policy["reasons"])


def test_event_risk_measured_distance_still_flags(monkeypatch) -> None:
    import signal_scanner as scanner

    monkeypatch.setattr(scanner, "_nearest_earnings_distance_days", lambda _ticker: 1)
    with patch("config.get_schwab_only_data", return_value=True):
        policy = scanner.evaluate_event_risk_policy("AAPL", Path("."))
    assert policy["earnings_lookup"] == "measured"
    assert policy["earnings_near"] is True


def test_exit_manager_off_does_not_call_simulator(monkeypatch) -> None:
    import backtest

    called = {"n": 0}

    def _sim(*_a, **_k):
        called["n"] += 1
        raise AssertionError("simulator should not run when mode is off")

    monkeypatch.setattr("config.get_exit_manager_mode", lambda *_a, **_k: "off")
    monkeypatch.setattr("backtest_intelligence.simulate_exit_with_manager", _sim)
    diag = {"exit_manager_simulated": 0}
    idx, reason = backtest._plan_exit_manager_for_entry(
        pd.DataFrame({"close": [1.0, 1.1]}),
        0,
        1,
        0.05,
        Path("."),
        diag,
    )
    assert idx is None and reason is None
    assert called["n"] == 0
    assert diag["exit_manager_simulated"] == 0


def test_exit_manager_shadow_counts_without_replacing_exit(monkeypatch) -> None:
    import backtest

    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    df = pd.DataFrame({"close": [10.0, 11.0]}, index=idx)

    def _sim(*_a, **_k):
        return 11.0, idx[1], "managed_target", {
            "mode": "shadow",
            "shadow": {"legacy_reason": "time_exit", "managed_reason": "managed_target"},
        }

    monkeypatch.setattr("config.get_exit_manager_mode", lambda *_a, **_k: "shadow")
    monkeypatch.setattr("backtest_intelligence.simulate_exit_with_manager", _sim)
    diag = {}
    out_idx, reason = backtest._plan_exit_manager_for_entry(df, 0, 1, 0.05, Path("."), diag)
    assert out_idx is None and reason is None
    assert diag["exit_manager_simulated"] == 1
    assert diag["exit_manager_shadow_would_differ"] == 1
