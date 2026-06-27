from __future__ import annotations

from pathlib import Path

import pytest

from config import (
    get_backtest_hold_days,
    get_backtest_min_hold_days_before_trail,
    get_confluence_gate_mode,
    get_exit_manager_mode,
    get_exit_max_hold_days,
    get_exit_min_hold_days_before_trail,
    get_hold_days,
    get_meta_policy_mode,
    get_uncertainty_mode,
)


def test_exit_returns_promoted_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from config import clear_env_cache

    for key in (
        "BACKTEST_HOLD_DAYS",
        "BACKTEST_MIN_HOLD_DAYS_BEFORE_TRAIL",
        "HOLD_DAYS",
        "EXIT_MAX_HOLD_DAYS",
        "EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL",
        "EXIT_MANAGER_MODE",
        "META_POLICY_MODE",
        "UNCERTAINTY_MODE",
        "CONFLUENCE_GATE_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    clear_env_cache()

    assert get_backtest_hold_days(tmp_path) == 40
    assert get_backtest_min_hold_days_before_trail(tmp_path) == 15
    assert get_hold_days(tmp_path) == 40
    assert get_exit_max_hold_days(tmp_path) == 40
    assert get_exit_min_hold_days_before_trail(tmp_path) == 15
    assert get_exit_manager_mode(tmp_path) == "live"
    assert get_meta_policy_mode(tmp_path) == "shadow"
    assert get_uncertainty_mode(tmp_path) == "shadow"
    assert get_confluence_gate_mode(tmp_path) == "shadow"


def test_initial_stop_uses_hard_stop_during_grace(tmp_path: Path) -> None:
    from execution import _initial_stop_payload_for_entry

    payload, kind = _initial_stop_payload_for_entry("AAPL", 10, 100.0, skill_dir=tmp_path)
    assert kind == "hard_grace"
    assert payload.get("orderType") == "STOP"
    assert float(payload.get("stopPrice") or 0) < 100.0


def test_exit_manager_settings_include_grace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from config import clear_env_cache

    monkeypatch.chdir(tmp_path)
    for key in (
        "EXIT_MAX_HOLD_DAYS",
        "EXIT_MIN_HOLD_DAYS_BEFORE_TRAIL",
        "EXIT_MANAGER_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    clear_env_cache()
    from execution import _get_exit_manager_settings

    settings = _get_exit_manager_settings(tmp_path)
    assert settings["max_hold_days"] == 40
    assert settings["min_hold_days_before_trail"] == 15
    assert settings["mode"] == "live"
